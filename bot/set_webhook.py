"""Подключает бота к сайту на PythonAnywhere.
Запуск:  python -m bot.set_webhook ВАШ_ЛОГИН"""
import asyncio
import sys

from .app import COMMANDS, make_bot
from .config import load_config


async def main(username: str) -> None:
    cfg = load_config()
    bot = make_bot(cfg)
    url = f"https://{username}.pythonanywhere.com/webhook"
    try:
        await bot.set_webhook(url, secret_token=cfg.webhook_secret, drop_pending_updates=True,
                              allowed_updates=["message", "callback_query"])
        await bot.set_my_commands(COMMANDS)
        info = await bot.get_webhook_info()
        me = await bot.get_me()
    finally:
        await bot.session.close()
    print(f"Готово! Бот @{me.username} подключён к {info.url}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Укажите логин PythonAnywhere: python -m bot.set_webhook ВАШ_ЛОГИН")
    asyncio.run(main(sys.argv[1].strip()))
