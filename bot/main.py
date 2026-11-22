import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, CallbackQuery, Message, TelegramObject

from .config import load_config
from .db import Database
from .handlers import router
from .notifier import Notifier


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
            chat_id = event.chat.id
            if chat_id not in self.allowed and not (event.text or "").startswith("/id"):
                return None
        elif isinstance(event, CallbackQuery) and event.message:
            if event.message.chat.id not in self.allowed:
                return None
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    db = Database(cfg.db_path)
    await db.init()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    notifier = Notifier(bot, db, cfg)

    dp = Dispatcher(cfg=cfg, db=db, notifier=notifier)
    dp.message.outer_middleware(AllowedChatsMiddleware(cfg.allowed_chat_ids))
    dp.callback_query.outer_middleware(AllowedChatsMiddleware(cfg.allowed_chat_ids))
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="add", description="Добавить товар"),
        BotCommand(command="soon", description="Что уценить / списать"),
        BotCommand(command="list", description="Все товары на учёте"),
        BotCommand(command="show", description="Карточка товара: /show N"),
        BotCommand(command="find", description="Найти по штрихкоду"),
        BotCommand(command="writeoff", description="Отметить списание: /writeoff N"),
        BotCommand(command="check", description="Проверить сроки сейчас"),
        BotCommand(command="cancel", description="Отменить добавление"),
        BotCommand(command="help", description="Справка"),
    ])

    loop_task = asyncio.create_task(notifier.loop())
    try:
        await dp.start_polling(bot)
    finally:
        loop_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
