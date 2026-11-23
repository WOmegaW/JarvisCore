"""База: количество, «продано»/«списано», изменение, переход со старой версии базы."""
import asyncio
import sqlite3
from datetime import date, timedelta

from bot.db import DISCOUNTED, SOLD, WRITTEN_OFF, Database

OLD_SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, photo_file_id TEXT NOT NULL,
    barcode TEXT NOT NULL, name TEXT, expiry_date TEXT NOT NULL, added_by TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')), status TEXT NOT NULL DEFAULT 'active',
    discount_notified INTEGER NOT NULL DEFAULT 0, writeoff_notified INTEGER NOT NULL DEFAULT 0,
    discount_by TEXT, writeoff_by TEXT
);
INSERT INTO products (chat_id, photo_file_id, barcode, name, expiry_date) VALUES (-1, 'F', '111', 'Старое молоко', '2030-01-01');
"""


def run(coro):
    return asyncio.run(coro)


def test_migration_from_first_version(tmp_path):
    """Товары, внесённые до появления количества, не теряются и получают 1 шт."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.close()
    db = Database(str(path))
    run(db.init())
    run(db.init())  # повторная инициализация безопасна
    [p] = run(db.list_open(-1))
    assert (p.title, p.quantity, p.initial_qty, p.status) == ("Старое молоко", 1, 1, "active")


def test_partial_sell_and_writeoff(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    run(db.init())
    pid = run(db.add(-1, "F", "123", "Хлеб", date.today() + timedelta(days=3), "Аня", 6))
    assert run(db.set_sold(pid, "Аня", 2))
    assert run(db.set_written_off(pid, "Ваня", 1))
    p = run(db.get(pid))
    assert (p.quantity, p.sold_qty, p.written_off_qty, p.status) == (3, 2, 1, "active")
    assert run(db.set_sold(pid, "Аня"))          # без количества — весь остаток
    p = run(db.get(pid))
    assert (p.quantity, p.status, p.is_closed) == (0, SOLD, True)
    assert not run(db.set_sold(pid, "Аня"))      # закрытый товар больше не трогается
    assert not run(db.set_written_off(pid, "Аня"))
    assert run(db.list_open(-1)) == []


def test_discount_then_partial_then_close(tmp_path):
    """Сценарий из магазина: 3 шт. → скидка → списал 1 → продал 2."""
    db = Database(str(tmp_path / "t.db"))
    run(db.init())
    pid = run(db.add(-1, "F", "4820000000001", "Crisps", date.today() + timedelta(days=2), "Аня", 3))
    assert run(db.set_discounted(pid, "Аня"))
    assert run(db.get(pid)).status == DISCOUNTED
    assert run(db.set_written_off(pid, "Аня", 1))
    assert run(db.get(pid)).status == DISCOUNTED      # частичное списание не закрывает
    assert run(db.set_sold(pid, "Аня", 2))
    assert run(db.get(pid)).status == SOLD


def test_writeoff_all_closes_as_written_off(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    run(db.init())
    pid = run(db.add(-1, "F", "1", "Йогурт", date.today(), "Аня", 2))
    assert run(db.set_written_off(pid, "Аня"))
    assert run(db.get(pid)).status == WRITTEN_OFF


def test_update_resets_reminders_when_date_changes(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    run(db.init())
    today = date.today()
    pid = run(db.add(-1, "F", "1", "Сыр", today + timedelta(days=1), "Аня"))
    run(db.mark_discount_notified(pid))
    run(db.set_discounted(pid, "Аня"))
    run(db.update(pid, "Аня", name="Сыр", barcode="1", expiry=today + timedelta(days=10), quantity=4,
                  today=today, discount_days=2))
    p = run(db.get(pid))
    assert (p.quantity, p.initial_qty, p.discount_notified, p.status, p.discount_by) == (4, 4, False, "active", None)


def test_last_by_barcode_prefers_named(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    run(db.init())
    run(db.add(-1, "F1", "777", "Кефир 1%", date.today(), "Аня"))
    run(db.add(-1, "F2", "777", None, date.today(), "Аня"))
    assert run(db.last_by_barcode(-1, "777")).name == "Кефир 1%"
    assert run(db.last_by_barcode(-2, "777")) is None   # другая группа — свои данные
