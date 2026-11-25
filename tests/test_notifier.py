"""Напоминания: за 2 дня — скидка 50%, в день окончания — списать; без повторов."""
import flask_app
from conftest import add_product


def run_checks():
    flask_app.run(flask_app.notifier.run_checks())


def reminders(tg):
    return [t.splitlines()[0] for t in tg.texts("SendPhoto") if "ПОСТАВИТЬ" in t or "СПИСАТЬ" in t]


def test_stages_and_no_duplicates(client, auth, tg):
    for d in (10, 3, 2, 1, 0):
        add_product(client, auth, barcode=f"46000000000{d:02d}", name=f"d{d}", expiry_days=d)
    first = reminders(tg)          # при добавлении положенные напоминания уходят сразу
    assert sum("СКИДКУ 50%" in r for r in first) == 2       # за 2 дня и за 1 день
    assert sum("СПИСАТЬ" in r for r in first) == 1          # срок сегодня
    tg.calls.clear()
    run_checks()
    run_checks()
    assert reminders(tg) == []      # повторно не присылает


def test_reminder_has_buttons_and_app_link(client, auth, tg):
    add_product(client, auth, expiry_days=1)
    [(_, m)] = [(n, m) for n, m in tg.calls if n == "SendPhoto" and "СКИДКУ" in (m.caption or "")]
    rows = m.reply_markup.inline_keyboard
    assert rows[0][0].callback_data.startswith("disc:")
    assert {b.callback_data.split(":")[0] for b in rows[1]} == {"sold", "off"}
    assert rows[-1][0].url.startswith("https://t.me/mixalmacen_bot?startapp=")


def test_daily_summary_lists_unconfirmed_overdue(client, auth, tg):
    import os
    import sqlite3
    pid = add_product(client, auth, expiry_days=1)["id"]
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE products SET expiry_date = date('now', '-3 day') WHERE id = ?", (pid,))
    con.commit()
    con.close()
    flask_app.run(flask_app.notifier.send_daily_summary())
    assert any("Просрочено и ещё не списано" in t for t in tg.texts("SendMessage"))


# ---------- утренний «План на день» ----------

import datetime as dt  # noqa: E402

from conftest import CHAT  # noqa: E402


def at(monkeypatch, hour, day_shift=0):
    """Подменить «сейчас» у бота: hour:00 по Мадриду (сегодня + day_shift)."""
    n = flask_app.notifier
    base = dt.datetime.now(n.cfg.tz).replace(hour=hour, minute=5, second=0, microsecond=0) + dt.timedelta(days=day_shift)
    monkeypatch.setattr(n, "now", lambda: base)
    monkeypatch.setattr(n, "today", lambda: base.date())


def plans(tg):
    return [t for t in tg.texts("SendMessage") if "План на" in t]


def test_morning_plan_lists_what_to_do(client, auth, tg):
    add_product(client, auth, barcode="4600000000001", name="Йогурт", qty=3, expiry_days=0)
    add_product(client, auth, barcode="4600000000002", name="Хлеб", expiry_days=2)
    pid = add_product(client, auth, barcode="4600000000003", name="Сыр", expiry_days=1)["id"]
    client.post(f"/api/products/{pid}/discount", headers=auth)
    add_product(client, auth, barcode="4600000000004", name="Масло", expiry_days=20)
    tg.calls.clear()
    assert flask_app.run(flask_app.notifier.send_morning_plan()) == 1
    [text] = plans(tg)
    assert "Списать сегодня (1)" in text and "Йогурт — 3 шт." in text
    assert "Поставить скидку 50% (1)" in text and "Хлеб" in text
    assert "Уже со скидкой (1)" in text and "Сыр" in text
    assert "Масло" not in text.split("На полке")[0]          # в порядке — в план не попадает
    assert "На полке: 4 товаров" in text
    msg = [m for n, m in tg.calls if n == "SendMessage"][-1]
    assert msg.chat_id == CHAT and msg.reply_markup.inline_keyboard[0][0].url.startswith("https://t.me/")


def test_morning_plan_when_nothing_to_do(client, auth, tg):
    add_product(client, auth, expiry_days=30)
    tg.calls.clear()
    flask_app.run(flask_app.notifier.send_morning_plan())
    assert "ничего списывать и уценять не нужно" in plans(tg)[0]


def test_routine_runs_once_a_day_and_not_before_nine(client, auth, chat, tg, monkeypatch):
    add_product(client, auth, expiry_days=0)
    chat.message("/backup_here", private=True)
    tg.calls.clear()
    at(monkeypatch, 8)
    r = flask_app.run(flask_app.notifier.morning_routine())
    assert "рано" in r["skipped"] and plans(tg) == []
    at(monkeypatch, 9)
    r = flask_app.run(flask_app.notifier.morning_routine())
    assert r["plan_sent_to"] == 1 and "уже отправлена" in r["backup"]   # копию сегодня уже прислал /backup_here
    assert len(plans(tg)) == 1
    tg.calls.clear()
    at(monkeypatch, 10)                                        # второй запуск (другой cron) — ничего
    assert "уже отправлено" in flask_app.run(flask_app.notifier.morning_routine())["skipped"]
    assert tg.calls == []
    at(monkeypatch, 9, day_shift=1)                             # на следующий день — снова, и с копией
    r = flask_app.run(flask_app.notifier.morning_routine())
    assert len(plans(tg)) == 1 and "копия отправлена" in r["backup"] and "SendDocument" in tg.names()


def test_daily_endpoint(client, auth, tg, monkeypatch):
    monkeypatch.delenv("DEPLOY_SECRET", raising=False)
    assert client.post("/__daily").status_code == 404
    monkeypatch.setenv("DEPLOY_SECRET", "s3cret-value-123456")
    assert client.post("/__daily", headers={"X-Deploy-Secret": "nope"}).status_code == 403
    add_product(client, auth, expiry_days=1)
    tg.calls.clear()
    r = client.post("/__daily?force=1", headers={"X-Deploy-Secret": "s3cret-value-123456"})
    assert r.status_code == 200 and r.get_json()["plan_sent_to"] == 1
    assert len(plans(tg)) == 1

