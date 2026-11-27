"""API для Mini App (приложения внутри Telegram).

Приложение открывается по ссылке t.me/<бот>?startapp=<id группы>. Каждый запрос несёт
initData от Telegram — по нему проверяем подпись и что пользователь состоит в группе."""
import base64
import binascii
import hashlib
import io
import logging
import hmac
import html
import os
import shutil
import time
from datetime import date
from typing import Any, Callable, Coroutine

from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.web_app import safe_parse_webapp_init_data
from flask import Blueprint, Response, abort, jsonify, request, send_from_directory

from .config import ROOT, Config
from .db import ACTIVE, DISCOUNTED, SOLD, WRITTEN_OFF, Database, Product
from .deploy import current_version
from .notifier import Notifier
from .utils import BARCODE_RE, fmt_date, product_card

WEBAPP_DIR = os.path.join(ROOT, "webapp")
MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
MEMBER_CACHE_TTL = 600


def startapp_param(chat_id: int) -> str:
    return str(chat_id)


def create_blueprint(cfg: Config, db: Database, bot: Bot, notifier: Notifier,
                     run: Callable[[Coroutine], Any]) -> Blueprint:
    bp = Blueprint("webapp", __name__)
    member_cache: dict[tuple[int, int], float] = {}
    photo_dir = os.path.join(os.path.dirname(cfg.db_path), "photos")
    os.makedirs(photo_dir, exist_ok=True)

    def photo_sig(pid: int) -> str:
        return hmac.new(cfg.webhook_secret.encode(), f"photo:{pid}".encode(), hashlib.sha256).hexdigest()[:20]

    def to_json(p: Product, today: date) -> dict:
        left = (p.expiry_date - today).days
        if p.status == WRITTEN_OFF:
            stage = "off"
        elif p.status == SOLD:
            stage = "sold"
        elif left <= 0:
            stage = "writeoff"
        elif p.status == DISCOUNTED:
            stage = "discounted"
        elif left <= cfg.discount_days_before:
            stage = "discount"
        else:
            stage = "ok"
        return {
            "id": p.id, "title": p.title, "name": p.name, "barcode": p.barcode,
            "expiry": p.expiry_date.isoformat(), "expiry_text": fmt_date(p.expiry_date),
            "days_left": left, "status": p.status, "stage": stage,
            "added_by": p.added_by, "discount_by": p.discount_by, "writeoff_by": p.writeoff_by,
            "sold_by": p.sold_by, "quantity": p.quantity, "initial_qty": p.initial_qty,
            "sold_qty": p.sold_qty, "written_off_qty": p.written_off_qty,
            "photo": f"/api/photo/{p.id}/{photo_sig(p.id)}" if p.photo_file_id else None,
        }

    def auth() -> tuple[int, str]:
        """Проверяет initData и членство в группе. Возвращает (chat_id, имя пользователя)."""
        init_data = request.headers.get("X-Init-Data", "")
        try:
            data = safe_parse_webapp_init_data(cfg.bot_token, init_data)
        except ValueError:
            abort(401, "Откройте приложение из Telegram")
        if time.time() - data.auth_date.timestamp() > 24 * 3600:
            abort(401, "Приложение было открыто слишком давно. Закройте его и откройте заново кнопкой «📦 Открыть учёт».")
        param = (data.start_param or "").strip()
        if not param.lstrip("-").isdigit():
            abort(400, "Откройте приложение кнопкой из рабочей группы (/app)")
        chat_id = int(param)
        if cfg.allowed_chat_ids and chat_id not in cfg.allowed_chat_ids:
            abort(403, "Эта группа не подключена к боту")
        user = data.user
        if user is None:
            abort(401, "Нет данных пользователя")
        key = (chat_id, user.id)
        if member_cache.get(key, 0) < time.time():
            try:
                member = run(bot.get_chat_member(chat_id, user.id))
            except Exception:
                abort(403, "Не удалось проверить доступ к группе")
            if member.status not in MEMBER_STATUSES:
                abort(403, "Вы не состоите в рабочей группе")
            member_cache[key] = time.time() + MEMBER_CACHE_TTL
        name = " ".join(x for x in (user.first_name, user.last_name) if x)
        return chat_id, name

    def own_product(pid: int, chat_id: int) -> Product:
        p = run(db.get(pid))
        if not p or p.chat_id != chat_id:
            abort(404, "Товар не найден")
        return p

    @bp.errorhandler(400)
    @bp.errorhandler(401)
    @bp.errorhandler(403)
    @bp.errorhandler(404)
    def api_error(e):
        return jsonify(error=getattr(e, "description", str(e))), e.code

    @bp.errorhandler(Exception)
    def api_crash(e):
        if hasattr(e, "code") and hasattr(e, "description"):  # обычные HTTP-ошибки Flask
            return jsonify(error=e.description), e.code
        logging.exception("Ошибка в приложении")
        return jsonify(error="Что-то пошло не так на сервере. Список обновлён — проверьте товар и повторите."), 500

    async def announce(chat_id: int, text: str) -> None:
        """Сообщение в группу о действии из приложения. Если Telegram на минуту недоступен —
        не страшно: само действие уже сохранено, поэтому ошибку только записываем в лог."""
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logging.exception("Не удалось отправить сообщение в группу")

    # ---------- страницы ----------

    app_version = current_version(ROOT)   # версия кода, с которой запущен сервер

    @bp.get("/app")
    def app_page():
        # В страницу вшиваем версию: если Telegram развернёт старую страницу из памяти,
        # она увидит, что на сервере уже новая, и перезагрузится сама.
        with open(os.path.join(WEBAPP_DIR, "index.html"), encoding="utf-8") as f:
            page = f.read().replace("__APP_VERSION__", app_version)
        resp = Response(page, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    @bp.get("/app/static/<path:name>")
    def app_static(name: str):
        resp = send_from_directory(os.path.join(WEBAPP_DIR, "static"), name)
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp

    # ---------- API ----------

    @bp.get("/api/state")
    def state():
        chat_id, name = auth()
        today = notifier.today()
        products = run(db.list_open(chat_id))
        return jsonify(
            me=name, today=today.isoformat(), app_version=app_version,
            discount_days=cfg.discount_days_before, discount_percent=cfg.discount_percent,
            products=[to_json(p, today) for p in products],
        )

    def read_form(today: date) -> dict:
        """Поля товара: из формы (multipart) или из JSON (фото — base64, так шлёт приложение
        через Telegram Serverless)."""
        src = request.get_json(silent=True) if request.is_json else None
        get = (lambda k: src.get(k)) if isinstance(src, dict) else request.form.get
        barcode = str(get("barcode") or "").replace(" ", "")
        title = str(get("name") or "").strip()[:100] or None
        try:
            expiry = date.fromisoformat(str(get("expiry") or ""))
        except ValueError:
            abort(400, "Укажите срок годности")
        raw_qty = get("quantity")
        try:
            qty = 1 if raw_qty is None or raw_qty == "" else int(raw_qty)   # 0 — это ошибка, а не «1 шт.»
        except (TypeError, ValueError):
            abort(400, "Количество — целое число")
        if not BARCODE_RE.match(barcode):
            abort(400, "Штрихкод — только цифры")
        if expiry < today:
            abort(400, "Эта дата уже прошла — такой товар нужно списать")
        if not 1 <= qty <= 9999:
            abort(400, "Количество — от 1 до 9999")
        if isinstance(src, dict):
            b64 = str(src.get("photo_b64") or "")
            if "," in b64[:100]:  # data:image/jpeg;base64,....
                b64 = b64.split(",", 1)[1]
            try:
                image = base64.b64decode(b64, validate=True) if b64 else b""
            except (binascii.Error, ValueError):
                abort(400, "Фото повреждено — сфотографируйте ещё раз")
        else:
            photo = request.files.get("photo")
            image = photo.read() if photo else b""
        return {"barcode": barcode, "name": title, "expiry": expiry, "qty": qty, "image": image}

    def cache_photo(pid: int, image: bytes | None = None, copy_from: int | None = None) -> None:
        os.makedirs(photo_dir, exist_ok=True)   # папку могли удалить — создаём заново
        path = os.path.join(photo_dir, f"{pid}.jpg")
        if image:
            with open(path, "wb") as f:
                f.write(image)
        elif copy_from is not None:
            src = os.path.join(photo_dir, f"{copy_from}.jpg")
            if os.path.exists(src):
                shutil.copyfile(src, path)

    @bp.get("/api/catalog")
    def catalog():
        """Каталог: все товары, которые когда-либо вносили, — одна строка на штрихкод."""
        chat_id, _ = auth()
        items: dict[str, dict] = {}
        for p in run(db.list_all(chat_id)):           # от новых к старым
            it = items.get(p.barcode)
            if it is None:
                it = items[p.barcode] = {"barcode": p.barcode, "title": None, "photo": None,
                                         "on_shelf": 0, "dates_on_shelf": 0, "times_added": 0}
            it["times_added"] += 1
            if it["title"] is None and p.name:
                it["title"] = p.name
            if it["photo"] is None and p.photo_file_id:
                it["photo"] = f"/api/photo/{p.id}/{photo_sig(p.id)}"
            if not p.is_closed:
                it["on_shelf"] += p.quantity
                it["dates_on_shelf"] += 1
        out = []
        for it in items.values():
            it["title"] = it["title"] or f"Товар {it['barcode']}"
            out.append(it)
        out.sort(key=lambda x: x["title"].lower())
        return jsonify(items=out)

    @bp.get("/api/lookup/<barcode>")
    def lookup(barcode: str):
        """Автоподстановка: что знаем о товаре с этим штрихкодом."""
        chat_id, _ = auth()
        if not BARCODE_RE.match(barcode):
            return jsonify(found=False)
        p = run(db.last_by_barcode(chat_id, barcode))
        if not p:
            return jsonify(found=False)
        today = notifier.today()
        on_shelf = [to_json(x, today) for x in run(db.find_by_barcode(chat_id, barcode)) if not x.is_closed]
        return jsonify(found=True, name=p.name, photo=to_json(p, today)["photo"], on_shelf=on_shelf)

    @bp.post("/api/products")
    def add_product():
        chat_id, name = auth()
        today = notifier.today()
        f = read_form(today)
        known = None
        if not f["image"]:
            # фото не сделали — берём фото этого товара из прошлого раза
            known = run(db.last_by_barcode(chat_id, f["barcode"]))
            if not known or not known.photo_file_id:
                abort(400, "Добавьте фото товара")
        if not f["name"] and known is None:
            known = run(db.last_by_barcode(chat_id, f["barcode"]))
        title = f["name"] or (known.name if known else None)

        async def create() -> Product:
            pid = await db.add(chat_id, "", f["barcode"], title, f["expiry"], name, f["qty"])
            p = await db.get(pid)
            caption = "✅ <b>Товар добавлен на учёт</b> (через приложение)\n\n" + product_card(p, today)
            photo = BufferedInputFile(f["image"], filename="photo.jpg") if f["image"] else known.photo_file_id
            try:
                msg = await bot.send_photo(chat_id, photo, caption=caption)
            except Exception:
                await db.delete(pid, chat_id)  # не оставляем товар без фото — пусть попробуют ещё раз
                raise
            await db.set_photo(pid, msg.photo[-1].file_id)
            await notifier.notify_product(await db.get(pid))
            return await db.get(pid)

        try:
            p = run(create())
        except Exception as e:
            logging.exception("Не удалось добавить товар")
            abort(400, friendly_error(e))
        cache_photo(p.id, f["image"], None if f["image"] else (known.id if known else None))
        return jsonify(product=to_json(p, today))

    @bp.post("/api/products/<int:pid>/edit")
    def edit_product(pid: int):
        chat_id, name = auth()
        today = notifier.today()
        p = own_product(pid, chat_id)
        if p.is_closed:
            abort(400, "Товар уже закрыт — изменить нельзя")
        f = read_form(today)

        async def save() -> Product:
            file_id = None
            if f["image"]:
                msg = await bot.send_photo(
                    chat_id, BufferedInputFile(f["image"], filename="photo.jpg"),
                    caption=f"📸 {html.escape(name)} обновил(а) фото: <code>#{pid}</code> "
                            f"{html.escape(f['name'] or p.title)}",
                )
                file_id = msg.photo[-1].file_id
            await db.update(pid, name, name=f["name"], barcode=f["barcode"], expiry=f["expiry"],
                            quantity=f["qty"], today=today, discount_days=cfg.discount_days_before,
                            photo_file_id=file_id)
            # название и фото — общие для всей карточки (все партии с этим штрихкодом)
            await db.share_card_info(chat_id, f["barcode"], f["name"], file_id)
            changes = []
            if f["expiry"] != p.expiry_date:
                changes.append(f"срок {fmt_date(p.expiry_date)} → {fmt_date(f['expiry'])}")
            if (f["name"] or None) != p.name:
                changes.append(f"название → {html.escape(f['name'] or '—')}")
            if f["barcode"] != p.barcode:
                changes.append(f"штрихкод → {f['barcode']}")
            if changes:
                await announce(chat_id, f"✏️ {html.escape(name)} изменил(а) <code>#{pid}</code> "
                                        f"{html.escape(p.title)}: " + ", ".join(changes))
            await notifier.notify_product(await db.get(pid))
            return await db.get(pid)

        try:
            p2 = run(save())
        except Exception as e:
            logging.exception("Не удалось изменить товар")
            abort(400, friendly_error(e))
        if f["image"]:  # новое фото — у всех партий этой карточки
            for sib in run(db.find_by_barcode(chat_id, f["barcode"])):
                if not sib.is_closed:
                    cache_photo(sib.id, f["image"])
        return jsonify(product=to_json(p2, today))

    @bp.post("/api/products/<int:pid>/discount")
    def discount(pid: int):
        chat_id, name = auth()
        p = own_product(pid, chat_id)
        if p.status != ACTIVE or not run(db.set_discounted(pid, name)):
            abort(400, "Скидка на этот товар уже отмечена")
        run(announce(chat_id, f"🏷 {html.escape(name)} поставил(а) скидку "
                              f"{cfg.discount_percent}%: <code>#{pid}</code> {html.escape(p.title)}"))
        return jsonify(product=to_json(run(db.get(pid)), notifier.today()))

    def take(pid: int, kind: str):
        chat_id, name = auth()
        p = own_product(pid, chat_id)
        if p.is_closed:
            done = "продан" if p.status == SOLD else "списан"
            abort(400, f"Этот товар уже {done} полностью — на полке его не осталось")
        # количество не ведём: продано / списано — вся партия
        ok = run(db.set_written_off(pid, name) if kind == "writeoff" else db.set_sold(pid, name))
        if not ok:
            abort(400, "Не получилось — обновите список")
        p2 = run(db.get(pid))
        icon, verb = ("🗑", "списал(а)") if kind == "writeoff" else ("💰", "отметил(а) продажу")
        run(announce(chat_id, f"{icon} {html.escape(name)} {verb}: <code>#{pid}</code> "
                              f"{html.escape(p.title)} (до {fmt_date(p.expiry_date)})"))
        return jsonify(product=to_json(p2, notifier.today()))

    @bp.post("/api/products/<int:pid>/writeoff")
    def writeoff(pid: int):
        return take(pid, "writeoff")

    @bp.post("/api/products/<int:pid>/sold")
    def sold(pid: int):
        return take(pid, "sold")

    @bp.post("/api/products/<int:pid>/delete")
    def delete(pid: int):
        chat_id, _ = auth()
        own_product(pid, chat_id)
        run(db.delete(pid, chat_id))
        return jsonify(ok=True)

    def photo_bytes(pid: int, sig: str, thumb: bool) -> bytes:
        if not hmac.compare_digest(sig, photo_sig(pid)):
            abort(404)
        path = os.path.join(photo_dir, f"{pid}.jpg")
        if not os.path.exists(path):
            p = run(db.get(pid))
            if not p or not p.photo_file_id:
                abort(404)

            async def fetch() -> bytes:
                buf = io.BytesIO()
                await bot.download(p.photo_file_id, destination=buf)
                return buf.getvalue()

            try:
                data = run(fetch())
            except Exception:
                abort(404)
            os.makedirs(photo_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        if thumb:
            tpath = os.path.join(photo_dir, f"{pid}_t.jpg")
            if not os.path.exists(tpath) or os.path.getmtime(tpath) < os.path.getmtime(path):
                if not make_thumb(path, tpath):
                    tpath = path
            path = tpath
        with open(path, "rb") as f:
            return f.read()

    @bp.get("/api/photo/<int:pid>/<sig>")
    def photo(pid: int, sig: str):
        data = photo_bytes(pid, sig, request.args.get("thumb") == "1")
        resp = Response(data, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=86400"
        return resp

    @bp.get("/api/photo64/<int:pid>/<sig>")
    def photo64(pid: int, sig: str):
        """Фото в base64 — для приложения на Telegram Serverless (картинка идёт через Telegram)."""
        data = photo_bytes(pid, sig, request.args.get("thumb") == "1")
        return jsonify(data="data:image/jpeg;base64," + base64.b64encode(data).decode())

    return bp


def friendly_error(e: Exception) -> str:
    text = str(e)
    if "нет связи с Telegram" in text or "Tunnel" in text or "URLError" in text:
        return ("Не получилось отправить фото в Telegram — сервер на минуту перегружен. "
                "Ничего не сохранилось, нажмите «Сохранить» ещё раз.")
    return f"Не удалось сохранить: {text}"


def make_thumb(src: str, dst: str, size: int = 240) -> bool:
    """Маленькая копия фото для списка. Нужен Pillow; без него отдаём оригинал."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((size, size))
            im.save(dst, "JPEG", quality=75)
        return True
    except Exception:
        return False


def app_link(bot_username: str, chat_id: int) -> str:
    return f"https://t.me/{bot_username}?startapp={startapp_param(chat_id)}"

