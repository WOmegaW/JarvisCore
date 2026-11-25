"""Ежедневная проверка сроков — для запуска по расписанию (PythonAnywhere → Tasks).
Отправляет напоминания «поставить скидку» / «списать», сводку просроченного
и резервную копию владельцу."""
import asyncio
import logging
import sys

from .app import make_bot, make_db
from .config import load_config
from .notifier import Notifier


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = await make_db(cfg)
    bot = make_bot(cfg)
    force = "--force" in sys.argv
    try:
        notifier = Notifier(bot, db, cfg)
        report = await notifier.morning_routine(force=force)
    finally:
        await bot.session.close()
    print("Утренний запуск:", report)


if __name__ == "__main__":
    asyncio.run(main())
