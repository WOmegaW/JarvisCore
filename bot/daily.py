"""Ежедневная проверка сроков — для запуска по расписанию (PythonAnywhere → Tasks).
Отправляет напоминания «поставить скидку» / «списать», сводку просроченного
и резервную копию владельцу."""
import asyncio
import logging
import sys

from .app import make_bot, make_db
from .config import load_config
from .notifier import Notifier


MAX_WAIT = 2 * 3600   # дольше не ждём (задание запустили сильно раньше — значит, его время сбито)


def seconds_until_notify(now, notify_time) -> int:
    """Сколько секунд осталось до времени утреннего плана (0 — уже пора или слишком рано ждать)."""
    target = now.replace(hour=notify_time.hour, minute=notify_time.minute, second=0, microsecond=0)
    wait = int((target - now).total_seconds())
    return wait if 0 < wait <= MAX_WAIT else 0


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = await make_db(cfg)
    bot = make_bot(cfg)
    force = "--force" in sys.argv
    try:
        notifier = Notifier(bot, db, cfg)
        wait = seconds_until_notify(notifier.now(), cfg.notify_time)
        if wait and not force:
            # Задание PythonAnywhere стоит на 07:00 UTC: зимой это 8:00 по Мадриду, летом 9:00.
            # Если пришли раньше — ждём 9:00, чтобы план приходил ровно в 9 круглый год.
            print(f"Жду до {cfg.notify_time.strftime('%H:%M')} по времени магазина: {wait // 60} мин.")
            await asyncio.sleep(wait)
        report = await notifier.morning_routine(force=force)
    finally:
        await bot.session.close()
    print("Утренний запуск:", report)


if __name__ == "__main__":
    asyncio.run(main())
