"""Ежедневная проверка сроков — для запуска по расписанию (PythonAnywhere → Tasks).
Отправляет напоминания «поставить скидку» / «списать» и сводку просроченного."""
import asyncio
import logging

from .app import make_bot, make_db
from .config import load_config
from .notifier import Notifier


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = await make_db(cfg)
    bot = make_bot(cfg)
    try:
        notifier = Notifier(bot, db, cfg)
        await notifier.send_daily_summary()
        await notifier.run_checks()
    finally:
        await bot.session.close()
    print("Проверка сроков выполнена")


if __name__ == "__main__":
    asyncio.run(main())
