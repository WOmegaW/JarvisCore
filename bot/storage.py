"""Хранение шагов диалога (/add) в SQLite, чтобы они не терялись между запросами
в режиме webhook (PythonAnywhere) и при перезапуске бота."""
import json
from typing import Any, Mapping

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm (
    k     TEXT PRIMARY KEY,
    state TEXT,
    data  TEXT NOT NULL DEFAULT '{}'
);
"""


def _key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"


class SQLiteStorage(BaseStorage):
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO fsm (k, state) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET state = excluded.state",
                (_key(key), value),
            )
            await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT state FROM fsm WHERE k = ?", (_key(key),)) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO fsm (k, data) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET data = excluded.data",
                (_key(key), json.dumps(dict(data), ensure_ascii=False)),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT data FROM fsm WHERE k = ?", (_key(key),)) as cur:
                row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    async def close(self) -> None:
        pass
