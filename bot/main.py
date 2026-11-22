"""Режим polling: бот работает постоянно (VPS, свой компьютер, платный PythonAnywhere)."""
import asyncio
import logging

from .app import COMMANDS, make_bot, make_db, make_dispatcher
from .config import load_config
from .notifier import Notifier


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = await make_db(cfg)
    bot = make_bot(cfg)
    notifier = Notifier(bot, db, cfg)
    dp = await make_dispatcher(cfg, db)

    await bot.delete_webhook()  # на случай, если раньше был включён режим webhook
    await bot.set_my_commands(COMMANDS)

    loop_task = asyncio.create_task(notifier.loop())
    try:
        await dp.start_polling(bot, notifier=notifier)
    finally:
        loop_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
