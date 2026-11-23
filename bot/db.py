"""Хранение товаров в SQLite."""
import os
from dataclasses import dataclass
from datetime import date

import aiosqlite

# Статусы товара
ACTIVE = "active"            # на полке, срок ещё не подошёл
DISCOUNTED = "discounted"    # скидка поставлена
WRITTEN_OFF = "written_off"  # закрыт: остаток списан
SOLD = "sold"                # закрыт: всё продано
CLOSED = (WRITTEN_OFF, SOLD)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id            INTEGER NOT NULL,
    photo_file_id      TEXT    NOT NULL,
    barcode            TEXT    NOT NULL,
    name               TEXT,
    expiry_date        TEXT    NOT NULL,   -- ISO YYYY-MM-DD
    added_by           TEXT,
    added_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    status             TEXT    NOT NULL DEFAULT 'active',
    discount_notified  INTEGER NOT NULL DEFAULT 0,
    writeoff_notified  INTEGER NOT NULL DEFAULT 0,
    discount_by        TEXT,
    writeoff_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_products_chat_status ON products(chat_id, status);
CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(chat_id, barcode);

-- журнал действий (для истории и будущих отчётов)
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,   -- added / discount / sold / writeoff / edit
    qty         INTEGER,
    by          TEXT,
    at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_chat ON events(chat_id, at);

-- настройки бота (куда слать резервные копии и т.п.)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# колонки, добавленные позже — докидываем в существующую базу
MIGRATIONS = {
    "quantity": "INTEGER NOT NULL DEFAULT 1",       # сколько осталось на полке
    "initial_qty": "INTEGER NOT NULL DEFAULT 1",    # сколько было внесено
    "sold_qty": "INTEGER NOT NULL DEFAULT 0",
    "written_off_qty": "INTEGER NOT NULL DEFAULT 0",
    "sold_by": "TEXT",
}

OPEN_SQL = f"status NOT IN ('{WRITTEN_OFF}', '{SOLD}')"


@dataclass
class Product:
    id: int
    chat_id: int
    photo_file_id: str
    barcode: str
    name: str | None
    expiry_date: date
    added_by: str | None
    status: str
    discount_notified: bool
    writeoff_notified: bool
    discount_by: str | None
    writeoff_by: str | None
    quantity: int
    initial_qty: int
    sold_qty: int
    written_off_qty: int
    sold_by: str | None

    @classmethod
    def from_row(cls, r: aiosqlite.Row) -> "Product":
        return cls(
            id=r["id"],
            chat_id=r["chat_id"],
            photo_file_id=r["photo_file_id"],
            barcode=r["barcode"],
            name=r["name"],
            expiry_date=date.fromisoformat(r["expiry_date"]),
            added_by=r["added_by"],
            status=r["status"],
            discount_notified=bool(r["discount_notified"]),
            writeoff_notified=bool(r["writeoff_notified"]),
            discount_by=r["discount_by"],
            writeoff_by=r["writeoff_by"],
            quantity=r["quantity"],
            initial_qty=r["initial_qty"],
            sold_qty=r["sold_qty"],
            written_off_qty=r["written_off_qty"],
            sold_by=r["sold_by"],
        )

    @property
    def title(self) -> str:
        return self.name or f"Товар {self.barcode}"

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            async with db.execute("PRAGMA table_info(products)") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for col, ddl in MIGRATIONS.items():
                if col not in existing:
                    await db.execute(f"ALTER TABLE products ADD COLUMN {col} {ddl}")
            await db.commit()

    async def _fetch(self, sql: str, params: tuple = ()) -> list[Product]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                return [Product.from_row(r) for r in await cur.fetchall()]

    async def _exec(self, sql: str, params: tuple = ()) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    async def log(self, p: Product, kind: str, qty: int | None, by: str | None) -> None:
        await self._exec("INSERT INTO events (product_id, chat_id, kind, qty, by) VALUES (?, ?, ?, ?, ?)",
                         (p.id, p.chat_id, kind, qty, by))

    # ---------- настройки ----------

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._exec("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ---------- создание и чтение ----------

    async def add(self, chat_id: int, photo_file_id: str, barcode: str,
                  name: str | None, expiry: date, added_by: str, quantity: int = 1) -> int:
        pid = await self._exec(
            "INSERT INTO products (chat_id, photo_file_id, barcode, name, expiry_date, added_by, "
            "quantity, initial_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, photo_file_id, barcode, name, expiry.isoformat(), added_by, quantity, quantity),
        )
        await self._exec("INSERT INTO events (product_id, chat_id, kind, qty, by) VALUES (?, ?, 'added', ?, ?)",
                         (pid, chat_id, quantity, added_by))
        return pid

    async def get(self, product_id: int) -> Product | None:
        rows = await self._fetch("SELECT * FROM products WHERE id = ?", (product_id,))
        return rows[0] if rows else None

    async def list_open(self, chat_id: int) -> list[Product]:
        """Все товары чата, которые ещё на полке, ближайший срок — первым."""
        return await self._fetch(
            f"SELECT * FROM products WHERE chat_id = ? AND {OPEN_SQL} ORDER BY expiry_date, id", (chat_id,)
        )

    async def list_all_open(self) -> list[Product]:
        return await self._fetch(f"SELECT * FROM products WHERE {OPEN_SQL} ORDER BY expiry_date, id")

    async def find_by_barcode(self, chat_id: int, barcode: str) -> list[Product]:
        return await self._fetch(
            "SELECT * FROM products WHERE chat_id = ? AND barcode = ? ORDER BY id DESC", (chat_id, barcode),
        )

    async def last_by_barcode(self, chat_id: int, barcode: str) -> Product | None:
        """Последний внесённый товар с таким штрихкодом — для автоподстановки названия и фото."""
        rows = await self._fetch(
            "SELECT * FROM products WHERE chat_id = ? AND barcode = ? "
            "ORDER BY (name IS NOT NULL) DESC, id DESC LIMIT 1", (chat_id, barcode),
        )
        return rows[0] if rows else None

    # ---------- оповещения ----------

    async def mark_discount_notified(self, product_id: int) -> None:
        await self._exec("UPDATE products SET discount_notified = 1 WHERE id = ?", (product_id,))

    async def mark_writeoff_notified(self, product_id: int) -> None:
        await self._exec("UPDATE products SET writeoff_notified = 1 WHERE id = ?", (product_id,))

    # ---------- действия ----------

    async def set_discounted(self, product_id: int, by: str) -> bool:
        n = await self._exec(
            "UPDATE products SET status = ?, discount_by = ? WHERE id = ? AND status = ?",
            (DISCOUNTED, by, product_id, ACTIVE),
        )
        if n:
            await self.log(await self.get(product_id), "discount", None, by)
        return n > 0

    async def _take(self, product_id: int, qty: int | None, by: str, kind: str) -> tuple[bool, int]:
        """Убирает qty штук с полки (None — весь остаток). Возвращает (успех, сколько убрано)."""
        p = await self.get(product_id)
        if not p or p.is_closed:
            return False, 0
        qty = p.quantity if qty is None else max(1, min(qty, p.quantity))
        left = p.quantity - qty
        if kind == "sold":
            sets = "sold_qty = sold_qty + ?, sold_by = ?"
            final = SOLD
        else:
            sets = "written_off_qty = written_off_qty + ?, writeoff_by = ?"
            final = WRITTEN_OFF
        status = final if left == 0 else p.status
        n = await self._exec(
            f"UPDATE products SET quantity = ?, status = ?, {sets} "
            f"WHERE id = ? AND quantity = ? AND {OPEN_SQL}",
            (left, status, qty, by, product_id, p.quantity),
        )
        if n:
            await self.log(p, kind, qty, by)
        return n > 0, qty

    async def set_written_off(self, product_id: int, by: str, qty: int | None = None) -> bool:
        ok, _ = await self._take(product_id, qty, by, "writeoff")
        return ok

    async def set_sold(self, product_id: int, by: str, qty: int | None = None) -> bool:
        ok, _ = await self._take(product_id, qty, by, "sold")
        return ok

    async def update(self, product_id: int, by: str, *, name: str | None, barcode: str,
                     expiry: date, quantity: int, today: date, discount_days: int,
                     photo_file_id: str | None = None) -> None:
        p = await self.get(product_id)
        if p is None:
            return
        fields = {"name": name, "barcode": barcode, "expiry_date": expiry.isoformat(), "quantity": quantity}
        # если сдвинули количество вверх — растёт и «внесено»
        if quantity > p.quantity:
            fields["initial_qty"] = p.initial_qty + (quantity - p.quantity)
        if photo_file_id:
            fields["photo_file_id"] = photo_file_id
        if expiry != p.expiry_date:
            # новая дата — напоминания пересчитываются заново
            fields["discount_notified"] = 0
            fields["writeoff_notified"] = 0
            if p.status == DISCOUNTED and (expiry - today).days > discount_days:
                fields["status"] = ACTIVE
                fields["discount_by"] = None
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE products SET {cols} WHERE id = ?", (*fields.values(), product_id))
        await self.log(p, "edit", quantity, by)

    async def set_photo(self, product_id: int, photo_file_id: str) -> None:
        await self._exec("UPDATE products SET photo_file_id = ? WHERE id = ?", (photo_file_id, product_id))

    async def delete(self, product_id: int, chat_id: int) -> bool:
        n = await self._exec("DELETE FROM products WHERE id = ? AND chat_id = ?", (product_id, chat_id))
        if n:
            await self._exec("DELETE FROM events WHERE product_id = ?", (product_id,))
        return n > 0
