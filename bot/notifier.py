"""Оповещения: за N дней — «поставить скидку», в день окончания — «списать»."""
import asyncio
import html
import logging
import os
from datetime import date, datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .backup import build_backup

from .config import Config
from .db import ACTIVE, Database, Product
from .utils import fmt_date, product_card

log = logging.getLogger(__name__)


def app_button(link: str | None) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="📱 Открыть учёт", url=link)]] if link else []


def discount_keyboard(p: Product, percent: int, link: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Скидка {percent}% поставлена", callback_data=f"disc:{p.id}")],
        [InlineKeyboardButton(text="💰 Всё продано", callback_data=f"sold:{p.id}"),
         InlineKeyboardButton(text="🗑 Списано", callback_data=f"off:{p.id}")],
        *app_button(link),
    ])


def writeoff_keyboard(p: Product, link: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Списано", callback_data=f"off:{p.id}"),
         InlineKeyboardButton(text="💰 Всё продано", callback_data=f"sold:{p.id}")],
        *app_button(link),
    ])


class Notifier:
    def __init__(self, bot: Bot, db: Database, cfg: Config):
        self.bot = bot
        self.db = db
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._last_summary: date | None = None

    def today(self) -> date:
        return datetime.now(self.cfg.tz).date()

    async def app_link(self, chat_id: int) -> str | None:
        """Ссылка на Mini App для группы (если включено в настройках)."""
        if not self.cfg.webapp_enabled:
            return None
        try:
            me = await self.bot.me()
        except Exception:
            return None
        return f"https://t.me/{me.username}?startapp={chat_id}"

    def _due_stage(self, p: Product, today: date) -> str | None:
        if p.is_closed:
            return None
        left = (p.expiry_date - today).days
        if left <= 0 and not p.writeoff_notified:
            return "writeoff"
        if 0 < left <= self.cfg.discount_days_before and p.status == ACTIVE and not p.discount_notified:
            return "discount"
        return None

    async def notify_product(self, p: Product) -> None:
        """Отправляет оповещение по товару, если оно сейчас положено и ещё не отправлялось."""
        today = self.today()
        stage = self._due_stage(p, today)
        if stage is None:
            return
        pct = self.cfg.discount_percent
        link = await self.app_link(p.chat_id)
        if stage == "discount":
            header = f"🟠 <b>ПОСТАВИТЬ СКИДКУ {pct}%</b>\nСрок годности заканчивается {fmt_date(p.expiry_date)}"
            kb = discount_keyboard(p, pct, link)
        else:
            header = "🔴 <b>СПИСАТЬ ТОВАР</b>\nСрок годности истёк — уберите с полки и спишите"
            kb = writeoff_keyboard(p, link)
        caption = f"{header}\n\n{product_card(p, today)}"
        try:
            await self.bot.send_photo(p.chat_id, p.photo_file_id, caption=caption, reply_markup=kb)
        except Exception:
            log.exception("Не удалось отправить оповещение по товару #%s", p.id)
            return
        if stage == "discount":
            await self.db.mark_discount_notified(p.id)
        else:
            await self.db.mark_writeoff_notified(p.id)

    async def run_checks(self, chat_id: int | None = None) -> None:
        async with self._lock:
            products = await self.db.list_all_open()
            for p in products:
                if chat_id is not None and p.chat_id != chat_id:
                    continue
                await self.notify_product(p)
                await asyncio.sleep(0.1)  # не упираться в лимиты Telegram

    async def send_daily_summary(self) -> None:
        """Ежедневное напоминание о товарах, которые так и не отметили как списанные."""
        today = self.today()
        by_chat: dict[int, list[Product]] = {}
        for p in await self.db.list_all_open():
            if p.expiry_date < today:
                by_chat.setdefault(p.chat_id, []).append(p)
        for chat_id, items in by_chat.items():
            lines = ["⚠️ <b>Не забудьте списать!</b> Эти товары просрочены, но не отмечены как списанные:\n"]
            for p in items:
                lines.append(f"🔴 <code>#{p.id}</code> {html.escape(p.title)} — до {fmt_date(p.expiry_date)}")
            lines.append("\nОтметить: /writeoff <code>номер</code>, показать фото: /show <code>номер</code>")
            try:
                await self.bot.send_message(chat_id, "\n".join(lines))
            except Exception:
                log.exception("Не удалось отправить сводку в чат %s", chat_id)

    async def send_backup(self, force: bool = False, max_fetch: int | None = None) -> str:
        """Резервная копия в личный чат владельца (настраивается командой /backup_here).
        Без force — не чаще раза в день. Возвращает короткий итог для логов/ответа."""
        chat = await self.db.get_setting("backup_chat_id")
        if not chat:
            return "не настроено, куда присылать копии (/backup_here в личном чате с ботом)"
        today = self.today()
        if not force and await self.db.get_setting("backup_last_day") == today.isoformat():
            return "сегодняшняя копия уже отправлена"
        loop = asyncio.get_running_loop()

        def fetch_photo(file_id: str) -> bytes | None:
            async def dl() -> bytes:
                import io
                buf = io.BytesIO()
                await self.bot.download(file_id, destination=buf)
                return buf.getvalue()
            try:
                return asyncio.run_coroutine_threadsafe(dl(), loop).result(timeout=60)
            except Exception:
                return None

        photo_dir = os.path.join(os.path.dirname(self.db.path), "photos")
        data, name, st = await asyncio.to_thread(build_backup, self.db.path, photo_dir, today, fetch_photo, max_fetch)
        caption = (f"💾 <b>Резервная копия {today.strftime('%d.%m.%Y')}</b>\n"
                   f"Товаров: {st['total']} (на полке {st['open']}), фото: {st['photos']}.\n"
                   + (f"Ещё {st['total'] - st['photos']} фото докачаю в следующих копиях.\n"
                      if st['photos'] < st['total'] and max_fetch is not None else "") +
                   "Внутри: база, таблица для Excel и фото. Как восстановить — в файле ПРОЧТИ.txt.")
        await self.bot.send_document(int(chat), BufferedInputFile(data, name), caption=caption,
                                     disable_notification=True)
        await self.db.set_setting("backup_last_day", today.isoformat())
        return f"копия отправлена: {name}, {len(data) // 1024} КБ"

    async def loop(self) -> None:
        """Раз в минуту: после NOTIFY_TIME рассылает положенные оповещения (каждое — один раз)."""
        while True:
            try:
                now = datetime.now(self.cfg.tz)
                if now.time() >= self.cfg.notify_time:
                    if self._last_summary != now.date():
                        self._last_summary = now.date()
                        await self.send_daily_summary()
                        try:
                            await self.send_backup()
                        except Exception:
                            log.exception("Не удалось отправить резервную копию")
                    await self.run_checks()
            except Exception:
                log.exception("Ошибка в цикле проверки сроков")
            await asyncio.sleep(60)
