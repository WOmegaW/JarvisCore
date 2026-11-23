"""Хранение товаров в SQLite."""
import os
from dataclasses import dataclass
from datetime import date

import aiosqlite

# Статусы товара
ACTIVE = "active"            # на полке, срок ещё не подошёл
DISCOUNTED = "discounted"    # скидка поставлена
WRITTEN_OFF = "written_off"  # списан

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
"""


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
        )

    @property
    def title(self) -> str:
        return self.name or f"Товар {self.barcode}"


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
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

    async def add(self, chat_id: int, photo_file_id: str, barcode: str,
                  name: str | None, expiry: date, added_by: str) -> int:
        return await self._exec(
            "INSERT INTO products (chat_id, photo_file_id, barcode, name, expiry_date, added_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, photo_file_id, barcode, name, expiry.isoformat(), added_by),
        )

    async def get(self, product_id: int) -> Product | None:
        rows = await self._fetch("SELECT * FROM products WHERE id = ?", (product_id,))
        return rows[0] if rows else None

    async def list_open(self, chat_id: int) -> list[Product]:
        """Все не списанные товары чата, ближайший срок — первым."""
        return await self._fetch(
            "SELECT * FROM products WHERE chat_id = ? AND status != ? ORDER BY expiry_date, id",
            (chat_id, WRITTEN_OFF),
        )

    async def list_all_open(self) -> list[Product]:
        return await self._fetch(
            "SELECT * FROM products WHERE status != ? ORDER BY expiry_date, id", (WRITTEN_OFF,)
        )

    async def find_by_barcode(self, chat_id: int, barcode: str) -> list[Product]:
        return await self._fetch(
            "SELECT * FROM products WHERE chat_id = ? AND barcode = ? ORDER BY expiry_date DESC, id DESC",
            (chat_id, barcode),
        )

    async def mark_discount_notified(self, product_id: int) -> None:
        await self._exec("UPDATE products SET discount_notified = 1 WHERE id = ?", (product_id,))

    async def mark_writeoff_notified(self, product_id: int) -> None:
        await self._exec("UPDATE products SET writeoff_notified = 1 WHERE id = ?", (product_id,))

    async def set_discounted(self, product_id: int, by: str) -> bool:
        n = await self._exec(
            "UPDATE products SET status = ?, discount_by = ? WHERE id = ? AND status = ?",
            (DISCOUNTED, by, product_id, ACTIVE),
        )
        return n > 0

    async def set_written_off(self, product_id: int, by: str) -> bool:
        n = await self._exec(
            "UPDATE products SET status = ?, writeoff_by = ? WHERE id = ? AND status != ?",
            (WRITTEN_OFF, by, product_id, WRITTEN_OFF),
        )
        return n > 0

    async def set_photo(self, product_id: int, photo_file_id: str) -> None:
        await self._exec("UPDATE products SET photo_file_id = ? WHERE id = ?", (photo_file_id, product_id))

    async def delete(self, product_id: int, chat_id: int) -> bool:
        n = await self._exec("DELETE FROM products WHERE id = ? AND chat_id = ?", (product_id, chat_id))
        return n > 0
