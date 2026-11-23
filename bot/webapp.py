"""API для Mini App (приложения внутри Telegram).

Приложение открывается по ссылке t.me/<бот>?startapp=<id группы>. Каждый запрос несёт
initData от Telegram — по нему проверяем подпись и что пользователь состоит в группе."""
import hashlib
import hmac
import html
import os
import time
from datetime import date
from typing import Any, Callable, Coroutine

from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.utils.web_app import safe_parse_webapp_init_data
from flask import Blueprint, abort, jsonify, request, send_from_directory

from .config import ROOT, Config
from .db import ACTIVE, DISCOUNTED, WRITTEN_OFF, Database, Product
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
            abort(401, "Сессия устарела — откройте приложение заново")
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

    # ---------- страницы ----------

    @bp.get("/app")
    def app_page():
        resp = send_from_directory(WEBAPP_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
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
            me=name, today=today.isoformat(),
            discount_days=cfg.discount_days_before, discount_percent=cfg.discount_percent,
            products=[to_json(p, today) for p in products],
        )

    @bp.post("/api/products")
    def add_product():
        chat_id, name = auth()
        today = notifier.today()
        barcode = (request.form.get("barcode") or "").replace(" ", "")
        title = (request.form.get("name") or "").strip()[:100] or None
        photo = request.files.get("photo")
        try:
            expiry = date.fromisoformat(request.form.get("expiry") or "")
        except ValueError:
            abort(400, "Укажите срок годности")
        if not BARCODE_RE.match(barcode):
            abort(400, "Штрихкод — только цифры")
        if expiry < today:
            abort(400, "Эта дата уже прошла — такой товар нужно списать")
        if photo is None:
            abort(400, "Добавьте фото товара")
        image = photo.read()
        if not image:
            abort(400, "Пустое фото")

        async def create() -> Product:
            pid = await db.add(chat_id, "", barcode, title, expiry, name)
            p = await db.get(pid)
            msg = await bot.send_photo(
                chat_id, BufferedInputFile(image, filename="photo.jpg"),
                caption="✅ <b>Товар добавлен на учёт</b> (через приложение)\n\n" + product_card(p, today),
            )
            await db.set_photo(pid, msg.photo[-1].file_id)
            p = await db.get(pid)
            await notifier.notify_product(p)
            return await db.get(pid)

        try:
            p = run(create())
        except Exception as e:
            abort(400, f"Не удалось сохранить: {e}")
        # сохраняем фото локально, чтобы не скачивать его из Telegram для показа
        with open(os.path.join(photo_dir, f"{p.id}.jpg"), "wb") as f:
            f.write(image)
        return jsonify(product=to_json(p, today))

    @bp.post("/api/products/<int:pid>/discount")
    def discount(pid: int):
        chat_id, name = auth()
        p = own_product(pid, chat_id)
        if p.status != ACTIVE or not run(db.set_discounted(pid, name)):
            abort(400, "Уже отмечено")
        run(bot.send_message(chat_id, f"🏷 {html.escape(name)} поставил(а) скидку "
                                      f"{cfg.discount_percent}%: <code>#{pid}</code> {html.escape(p.title)}"))
        return jsonify(product=to_json(run(db.get(pid)), notifier.today()))

    @bp.post("/api/products/<int:pid>/writeoff")
    def writeoff(pid: int):
        chat_id, name = auth()
        p = own_product(pid, chat_id)
        if not run(db.set_written_off(pid, name)):
            abort(400, "Товар уже списан")
        run(bot.send_message(chat_id, f"🗑 {html.escape(name)} списал(а): "
                                      f"<code>#{pid}</code> {html.escape(p.title)}"))
        return jsonify(product=to_json(run(db.get(pid)), notifier.today()))

    @bp.post("/api/products/<int:pid>/delete")
    def delete(pid: int):
        chat_id, _ = auth()
        own_product(pid, chat_id)
        run(db.delete(pid, chat_id))
        return jsonify(ok=True)

    @bp.get("/api/photo/<int:pid>/<sig>")
    def photo(pid: int, sig: str):
        if not hmac.compare_digest(sig, photo_sig(pid)):
            abort(404)
        path = os.path.join(photo_dir, f"{pid}.jpg")
        if not os.path.exists(path):
            p = run(db.get(pid))
            if not p or not p.photo_file_id:
                abort(404)

            async def fetch() -> bytes:
                import io
                buf = io.BytesIO()
                await bot.download(p.photo_file_id, destination=buf)
                return buf.getvalue()

            try:
                data = run(fetch())
            except Exception:
                abort(404)
            with open(path, "wb") as f:
                f.write(data)
        resp = send_from_directory(photo_dir, f"{pid}.jpg", mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=86400"
        return resp

    return bp


def app_link(bot_username: str, chat_id: int) -> str:
    return f"https://t.me/{bot_username}?startapp={startapp_param(chat_id)}"

