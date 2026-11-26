"""Веб-приложение для PythonAnywhere: Telegram присылает сюда сообщения (webhook)."""
import asyncio
import hmac
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiogram.types import Update  # noqa: E402
from flask import Flask, abort, jsonify, request  # noqa: E402

from bot.app import make_bot, make_db, make_dispatcher  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.deploy import current_version, update_code  # noqa: E402
from bot.notifier import Notifier  # noqa: E402
from bot.webapp import create_blueprint  # noqa: E402

logging.basicConfig(level=logging.INFO)

cfg = load_config()
loop = asyncio.new_event_loop()
lock = threading.RLock()
db = loop.run_until_complete(make_db(cfg))
dp = loop.run_until_complete(make_dispatcher(cfg, db))
bot = make_bot(cfg)
notifier = Notifier(bot, db, cfg)



def run(coro):
    """Выполняет асинхронный код бота из синхронного Flask."""
    with lock:
        return loop.run_until_complete(coro)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
app.register_blueprint(create_blueprint(cfg, db, bot, notifier, run))


@app.get("/")
def index():
    return "Бот учёта сроков годности работает ✅"


# ---------- автообновление из GitHub (см. bot/deploy.py) ----------
ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = current_version(ROOT)   # версия кода, которая сейчас запущена


@app.get("/version")
def version():
    return jsonify(version=VERSION)


def _check_deploy_secret():
    secret = os.getenv("DEPLOY_SECRET", "").strip()
    if not secret:
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Deploy-Secret", ""), secret):
        abort(403)


@app.post("/__daily")
def daily():
    """Утренний запуск по расписанию GitHub (см. .github/workflows/morning.yml).
    Можно вызывать сколько угодно раз: до 9:00 по Мадриду и после уже сделанного — пропуск."""
    _check_deploy_secret()
    force = request.args.get("force") == "1"
    plan_only = request.args.get("part") == "plan"   # cron-job.org: только план, быстро (< 30 с)
    try:
        report = run(notifier.morning_routine(force=force, max_fetch=60, with_backup=not plan_only))
    except Exception as e:
        logging.exception("Утренний запуск не удался")
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, **report)


@app.post("/__deploy")
def deploy():
    _check_deploy_secret()
    try:
        report = update_code(ROOT)
    except Exception as e:
        logging.exception("Автообновление не удалось")
        return jsonify(ok=False, error=str(e)), 500
    report["running"] = VERSION
    return jsonify(ok=True, **report)


@app.post("/webhook")
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.webhook_secret:
        abort(403)
    update = Update.model_validate(request.get_json(force=True), context={"bot": bot})
    try:
        run(dp.feed_update(bot, update, notifier=notifier))
    except Exception:
        logging.exception("Ошибка при обработке сообщения")
    return "ok"
