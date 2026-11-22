"""Веб-приложение для PythonAnywhere: Telegram присылает сюда сообщения (webhook)."""
import asyncio
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiogram.types import Update  # noqa: E402
from flask import Flask, abort, request  # noqa: E402

from bot.app import make_bot, make_db, make_dispatcher  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.notifier import Notifier  # noqa: E402

logging.basicConfig(level=logging.INFO)

cfg = load_config()
loop = asyncio.new_event_loop()
lock = threading.Lock()
db = loop.run_until_complete(make_db(cfg))
dp = loop.run_until_complete(make_dispatcher(cfg, db))
bot = make_bot(cfg)
notifier = Notifier(bot, db, cfg)

app = Flask(__name__)


@app.get("/")
def index():
    return "Бот учёта сроков годности работает ✅"


@app.post("/webhook")
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.webhook_secret:
        abort(403)
    update = Update.model_validate(request.get_json(force=True), context={"bot": bot})
    with lock:
        try:
            loop.run_until_complete(dp.feed_update(bot, update, notifier=notifier))
        except Exception:
            logging.exception("Ошибка при обработке сообщения")
    return "ok"
