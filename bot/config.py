import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_chat_ids: frozenset[int]
    tz: ZoneInfo
    notify_time: time
    discount_days_before: int
    discount_percent: int
    db_path: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")

    raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
    ids = frozenset(int(x) for x in raw_ids.replace(" ", "").split(",") if x)

    hh, mm = os.getenv("NOTIFY_TIME", "09:00").split(":")

    return Config(
        bot_token=token,
        allowed_chat_ids=ids,
        tz=ZoneInfo(os.getenv("TZ", "Europe/Moscow")),
        notify_time=time(int(hh), int(mm)),
        discount_days_before=int(os.getenv("DISCOUNT_DAYS_BEFORE", "2")),
        discount_percent=int(os.getenv("DISCOUNT_PERCENT", "50")),
        db_path=os.getenv("DB_PATH", "data/expiry.db"),
    )
