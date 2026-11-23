"""Оповещения: за N дней — «поставить скидку», в день окончания — «списать»."""
import asyncio
import html
import logging
from datetime import date, datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config
from .db import ACTIVE, WRITTEN_OFF, Database, Product
from .utils import fmt_date, product_card

log = logging.getLogger(__name__)


def app_button(link: str | None) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="📱 Открыть учёт", url=link)]] if link else []


def discount_keyboard(p: Product, percent: int, link: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Скидка {percent}% поставлена", callback_data=f"disc:{p.id}")],
        [InlineKeyboardButton(text="🗑 Товара уже нет / списан", callback_data=f"off:{p.id}")],
        *app_button(link),
    ])


def writeoff_keyboard(p: Product, link: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Списано", callback_data=f"off:{p.id}")],
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
        if p.status == WRITTEN_OFF:
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

    async def loop(self) -> None:
        """Раз в минуту: после NOTIFY_TIME рассылает положенные оповещения (каждое — один раз)."""
        while True:
            try:
                now = datetime.now(self.cfg.tz)
                if now.time() >= self.cfg.notify_time:
                    if self._last_summary != now.date():
                        self._last_summary = now.date()
                        await self.send_daily_summary()
                    await self.run_checks()
            except Exception:
                log.exception("Ошибка в цикле проверки сроков")
            await asyncio.sleep(60)
