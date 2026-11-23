import hashlib
import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_chat_ids: frozenset[int]
    tz: ZoneInfo
    notify_time: time
    discount_days_before: int
    discount_percent: int
    db_path: str
    proxy: str | None
    webhook_secret: str
    webapp_enabled: bool


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")

    raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
    ids = frozenset(int(x) for x in raw_ids.replace(" ", "").split(",") if x)

    db_path = os.getenv("DB_PATH", "data/expiry.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(ROOT, db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    hh, mm = os.getenv("NOTIFY_TIME", "09:00").split(":")

    return Config(
        bot_token=token,
        allowed_chat_ids=ids,
        tz=ZoneInfo(os.getenv("STORE_TZ", "Europe/Madrid")),
        notify_time=time(int(hh), int(mm)),
        discount_days_before=int(os.getenv("DISCOUNT_DAYS_BEFORE", "2")),
        discount_percent=int(os.getenv("DISCOUNT_PERCENT", "50")),
        db_path=db_path,
        proxy=os.getenv("PROXY_URL", "").strip() or None,
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip()
        or hashlib.sha256(token.encode()).hexdigest()[:32],
        webapp_enabled=os.getenv("WEBAPP", "1").strip() not in {"0", "false", "no", ""},
    )
