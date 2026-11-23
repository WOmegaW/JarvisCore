"""Резервная копия: всё, чтобы не сканировать товары заново, если сервер или бот «лягут».

Архив `mixalmacen-backup-ГГГГ-ММ-ДД.zip`:
- `expiry.db`        — полная копия базы (из неё бот восстанавливается одной командой);
- `товары.csv`       — все товары таблицей (открывается в Excel / Google Таблицах / Numbers);
- `фото/`            — фото каждого товара (уменьшенные, чтобы архив был небольшим);
- `ПРОЧТИ.txt`       — что это и как восстановить.

Архив бот присылает в личный чат владельцу (команда /backup_here) — Telegram хранит его
бессрочно, независимо от PythonAnywhere. Восстановление: `python -m bot.restore <архив.zip>`.
"""
import csv
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import date

MAX_ZIP_BYTES = 45 * 1024 * 1024   # Telegram принимает от бота файлы до 50 МБ
PHOTO_SIZE = 480

README = """MixAlmacén — резервная копия учёта сроков ({day})

Что внутри:
  expiry.db     — полная база бота (товары, количества, кто что отметил).
  товары.csv    — те же товары таблицей. Открывается в Excel, Google Таблицах, Numbers.
  фото/         — фото товаров: «номер_штрихкод.jpg».

Товаров в копии: {total} (на полке: {open}). Фото: {photos}.

Как восстановить бота из этой копии (PythonAnywhere → Bash):
  1. Загрузите этот zip в папку ~/JarvisCore (вкладка Files → Upload a file).
  2. Выполните:
       cd ~/JarvisCore && ~/venv/bin/python -m bot.restore {name}
  3. Web → Reload.
Текущая база перед восстановлением сохраняется рядом (expiry.db.before-restore-...).
"""


def _consistent_copy(db_path: str, dst: str) -> None:
    """Копия базы «на ходу» без риска поймать её на середине записи."""
    src = sqlite3.connect(db_path)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    src.close()
    out.close()


def _thumb(path: str) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        with open(path, "rb") as f:
            return f.read()
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=78)
            return buf.getvalue()
    except Exception:
        return None


STATUS_RU = {"active": "на полке", "discounted": "уценён", "written_off": "списан", "sold": "продан"}


def build_backup(db_path: str, photo_dir: str, today: date | None = None,
                 fetch_photo=None, max_fetch: int | None = None) -> tuple[bytes, str, dict]:
    """Собирает архив. fetch_photo(file_id) -> bytes | None — докачать фото, которого нет
    на диске (например, у товаров, внесённых через чат). Возвращает (zip, имя, статистика)."""
    today = today or date.today()
    name = f"mixalmacen-backup-{today.isoformat()}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = os.path.join(tmp, "expiry.db")
        _consistent_copy(db_path, db_copy)
        con = sqlite3.connect(db_copy)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM products ORDER BY id").fetchall()
        con.close()

        buf = io.BytesIO()
        stats = {"total": len(rows), "open": 0, "photos": 0, "photos_skipped": 0, "fetched": 0}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "expiry.db")

            table = io.StringIO()
            w = csv.writer(table, delimiter=";")
            w.writerow(["Номер", "Штрихкод", "Название", "Годен до", "На полке, шт", "Внесено, шт",
                        "Продано, шт", "Списано, шт", "Статус", "Добавил(а)", "Добавлено",
                        "Скидку поставил(а)", "Продажу отметил(а)", "Списал(а)", "Фото"])
            for r in rows:
                if r["status"] not in ("written_off", "sold"):
                    stats["open"] += 1
                photo_name = f"фото/{r['id']}_{r['barcode']}.jpg"
                w.writerow([r["id"], r["barcode"], r["name"] or "", _ru_date(r["expiry_date"]), r["quantity"],
                            r["initial_qty"], r["sold_qty"], r["written_off_qty"],
                            STATUS_RU.get(r["status"], r["status"]), r["added_by"] or "", r["added_at"] or "",
                            r["discount_by"] or "", r["sold_by"] or "", r["writeoff_by"] or "", photo_name])
            # «﻿» — чтобы Excel сразу понял кириллицу
            z.writestr("товары.csv", "﻿" + table.getvalue())

            for r in rows:
                data = None
                local = os.path.join(photo_dir, f"{r['id']}.jpg")
                if os.path.exists(local):
                    data = _thumb(local)
                elif fetch_photo and r["photo_file_id"] and (max_fetch is None or stats["fetched"] < max_fetch):
                    stats["fetched"] += 1
                    raw = fetch_photo(r["photo_file_id"])
                    if raw:
                        os.makedirs(photo_dir, exist_ok=True)
                        with open(local, "wb") as f:   # заодно кэшируем на диске
                            f.write(raw)
                        data = _thumb(local)
                if not data:
                    continue
                if buf.tell() + len(data) > MAX_ZIP_BYTES - 200_000:
                    stats["photos_skipped"] += 1
                    continue
                z.writestr(f"фото/{r['id']}_{r['barcode']}.jpg", data)
                stats["photos"] += 1

            z.writestr("ПРОЧТИ.txt", README.format(day=today.strftime("%d.%m.%Y"), name=name, **stats))
        return buf.getvalue(), name, stats


def _ru_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return iso or ""


def restore_from_zip(zip_path: str, db_path: str, photo_dir: str) -> dict:
    """Возвращает базу (и фото) из архива. Текущая база сохраняется рядом."""
    from datetime import datetime
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "expiry.db" not in names:
            raise ValueError("В архиве нет expiry.db — это не резервная копия бота")
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("expiry.db", tmp)
            restored = os.path.join(tmp, "expiry.db")
            con = sqlite3.connect(restored)
            n = con.execute("SELECT count(*) FROM products").fetchone()[0]
            con.close()
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            saved = None
            if os.path.exists(db_path):
                saved = f"{db_path}.before-restore-{datetime.now():%Y%m%d-%H%M%S}"
                _consistent_copy(db_path, saved)
            _consistent_copy(restored, db_path)
        photos = 0
        os.makedirs(photo_dir, exist_ok=True)
        for item in names:
            if item.startswith("фото/") and item.endswith(".jpg"):
                pid = os.path.basename(item).split("_", 1)[0]
                if pid.isdigit():
                    with open(os.path.join(photo_dir, f"{pid}.jpg"), "wb") as f:
                        f.write(z.read(item))
                    photos += 1
    return {"products": n, "photos": photos, "previous": saved}
