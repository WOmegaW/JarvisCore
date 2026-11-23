"""Резервная копия: состав архива, восстановление, команды /backup_here и /backup."""
import csv
import io
import os
import zipfile

import flask_app
from bot.backup import build_backup, restore_from_zip
from conftest import USER_ID, add_product


def ddmmyyyy(n):
    from conftest import days
    y, m, d = days(n).split("-")
    return f"{d}.{m}.{y}"


def photo_dir():
    return os.path.join(os.path.dirname(os.environ["DB_PATH"]), "photos")


def zip_of(tg):
    """Последний архив, отправленный ботом."""
    [(_, doc)] = [(n, m) for n, m in tg.calls if n == "SendDocument"][-1:]
    return doc, zipfile.ZipFile(io.BytesIO(doc.document.data))


def test_archive_has_everything(client, auth, chat):
    add_product(client, auth, barcode="4601234567890", name="Молоко", qty=6)           # фото есть на диске
    chat.message(photo="CHATPHOTO", caption=f"4600000000017 {ddmmyyyy(5)} Хлеб 2шт")    # фото только в Telegram
    data, name, st = build_backup(os.environ["DB_PATH"], photo_dir(),
                                  fetch_photo=lambda fid: open(os.path.join(photo_dir(), "1.jpg"), "rb").read())
    assert name.startswith("mixalmacen-backup-") and name.endswith(".zip")
    assert (st["total"], st["open"], st["photos"]) == (2, 2, 2)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert {"expiry.db", "товары.csv", "ПРОЧТИ.txt", "фото/1_4601234567890.jpg", "фото/2_4600000000017.jpg"} <= set(z.namelist())
    text = z.read("товары.csv").decode("utf-8")
    assert text.startswith("﻿")                       # Excel поймёт кириллицу
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    assert rows[0][:3] == ["Номер", "Штрихкод", "Название"]
    assert [r[2] for r in rows[1:]] == ["Молоко", "Хлеб"] and rows[1][4] == "6"


def test_restore_brings_everything_back(client, auth, tmp_path):
    add_product(client, auth, barcode="4601234567890", name="Молоко", qty=3)
    data, name, _ = build_backup(os.environ["DB_PATH"], photo_dir())
    zpath = tmp_path / name
    zpath.write_bytes(data)
    new_db, new_photos = tmp_path / "new" / "expiry.db", tmp_path / "new" / "photos"
    r = restore_from_zip(str(zpath), str(new_db), str(new_photos))
    assert (r["products"], r["photos"], r["previous"]) == (1, 1, None)
    assert (new_photos / "1.jpg").exists()
    # повторное восстановление сохраняет прежнюю базу рядом
    r2 = restore_from_zip(str(zpath), str(new_db), str(new_photos))
    assert r2["previous"] and os.path.exists(r2["previous"])


def test_backup_here_and_command(client, auth, chat, tg):
    add_product(client, auth, name="Сыр")
    chat.message("/backup")                                       # ещё не настроено
    assert "backup_here" in tg.texts()[-1]
    chat.message("/backup_here")                                  # в группе — подсказка про личку
    assert "личный чат" in tg.texts()[-1]
    chat.message("/backup_here", private=True)
    doc, z = zip_of(tg)
    assert doc.chat_id == USER_ID and doc.disable_notification
    assert "Резервная копия" in doc.caption and "expiry.db" in z.namelist()
    tg.calls.clear()
    chat.message("/backup")                                       # из группы — копия уходит в личку
    doc, _ = zip_of(tg)
    assert doc.chat_id == USER_ID
    assert "отправлена владельцу" in tg.texts("SendMessage")[-1]


def test_stranger_cannot_get_backups(chat, tg):
    chat.message("/backup_here", private=True, user_id=999)
    assert "только сотрудникам" in tg.texts()[-1]
    assert "SendDocument" not in tg.names()


def test_private_chat_ignores_other_messages(chat, tg):
    chat.message("/list", private=True)
    chat.message(photo="P", caption="4601234567890 25.12.2030", private=True)
    assert tg.calls == []


def test_daily_backup_once_a_day(client, auth, chat, tg):
    add_product(client, auth)
    chat.message("/backup_here", private=True)
    tg.calls.clear()
    assert "уже отправлена" in flask_app.run(flask_app.notifier.send_backup())
    assert "SendDocument" not in tg.names()
    flask_app.run(flask_app.notifier.send_backup(force=True))
    assert "SendDocument" in tg.names()


def test_backup_survives_failed_photo_download(client, auth, chat, tg):
    chat.message(photo="LOST", caption=f"4600000000017 {ddmmyyyy(5)} Хлеб")
    data, _, st = build_backup(os.environ["DB_PATH"], photo_dir(), fetch_photo=lambda fid: None)
    assert (st["total"], st["photos"]) == (1, 0)
    assert "expiry.db" in zipfile.ZipFile(io.BytesIO(data)).namelist()
