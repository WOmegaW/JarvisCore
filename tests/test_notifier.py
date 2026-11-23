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
    assert any("Не забудьте списать" in t for t in tg.texts("SendMessage"))
