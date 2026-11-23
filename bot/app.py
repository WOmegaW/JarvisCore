"""Сборка бота — общая для обоих режимов запуска:
- polling (bot/main.py): бот работает постоянно на своём сервере/VPS;
- webhook (flask_app.py + bot/daily.py): для бесплатного PythonAnywhere."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, CallbackQuery, Message, TelegramObject

from .config import Config
from .db import Database
from .handlers import router
from .http_session import UrllibProxySession
from .storage import SQLiteStorage

COMMANDS = [
    BotCommand(command="app", description="📱 Открыть приложение"),
    BotCommand(command="add", description="Добавить товар"),
    BotCommand(command="soon", description="Что уценить / списать"),
    BotCommand(command="list", description="Все товары на учёте"),
    BotCommand(command="show", description="Карточка товара: /show N"),
    BotCommand(command="find", description="Найти по штрихкоду"),
    BotCommand(command="writeoff", description="Отметить списание: /writeoff N"),
    BotCommand(command="sold", description="Отметить продажу: /sold N"),
    BotCommand(command="check", description="Проверить сроки сейчас"),
    BotCommand(command="cancel", description="Отменить добавление"),
    BotCommand(command="help", description="Справка"),
]


class AllowedChatsMiddleware(BaseMiddleware):
    """Пропускает только сообщения из разрешённых групп (если список задан).
    Команда /id работает везде — чтобы можно было узнать ID группы."""

    def __init__(self, allowed: frozenset[int]):
        self.allowed = allowed

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        if not self.allowed:
            return await handler(event, data)
        if isinstance(event, Message):
            if event.chat.id not in self.allowed and not (event.text or "").startswith("/id"):
                return None
        elif isinstance(event, CallbackQuery) and event.message:
            if event.message.chat.id not in self.allowed:
                return None
        return await handler(event, data)


def make_bot(cfg: Config) -> Bot:
    session = UrllibProxySession(cfg.proxy) if cfg.proxy else AiohttpSession()
    return Bot(cfg.bot_token, session=session,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def make_db(cfg: Config) -> Database:
    db = Database(cfg.db_path)
    await db.init()
    return db


async def make_dispatcher(cfg: Config, db: Database) -> Dispatcher:
    storage = SQLiteStorage(cfg.db_path)
    await storage.init()
    dp = Dispatcher(storage=storage, cfg=cfg, db=db)
    dp.message.outer_middleware(AllowedChatsMiddleware(cfg.allowed_chat_ids))
    dp.callback_query.outer_middleware(AllowedChatsMiddleware(cfg.allowed_chat_ids))
    dp.include_router(router)
    return dp
