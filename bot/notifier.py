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

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    async def group_chats(self) -> list[int]:
        """Группы, куда слать утренний план: из настроек, а если их нет — где есть товары."""
        if self.cfg.allowed_chat_ids:
            return sorted(self.cfg.allowed_chat_ids)
        return sorted({p.chat_id for p in await self.db.list_all_open()})

    def morning_text(self, items: list[Product], today: date) -> str:
        """«План на день» для группы: что списать, на что поставить скидку, что уже уценено."""
        pct, n = self.cfg.discount_percent, self.cfg.discount_days_before
        overdue = [p for p in items if p.expiry_date < today]
        writeoff = [p for p in items if p.expiry_date == today]
        discount = [p for p in items if 0 < (p.expiry_date - today).days <= n and p.status == ACTIVE]
        discounted = [p for p in items if 0 < (p.expiry_date - today).days <= n and p.status != ACTIVE]

        def line(p: Product) -> str:
            return f"• {html.escape(p.title)} <i>(до {p.expiry_date.strftime('%d.%m')})</i>"

        parts = [f"☀️ <b>Доброе утро! План на {today.strftime('%d.%m.%Y')}</b>"]
        if writeoff:
            parts.append(f"\n🔴 <b>Списать сегодня ({len(writeoff)}):</b>\n" + "\n".join(map(line, writeoff)))
        if overdue:
            parts.append(f"\n⚠️ <b>Просрочено и ещё не списано ({len(overdue)}):</b>\n" + "\n".join(map(line, overdue)))
        if discount:
            parts.append(f"\n🟠 <b>Поставить скидку {pct}% ({len(discount)}):</b>\n" + "\n".join(map(line, discount)))
        if discounted:
            parts.append(f"\n🏷 <b>Уже со скидкой ({len(discounted)}):</b>\n" + "\n".join(map(line, discounted)))
        if not (writeoff or overdue or discount):
            parts.append("\n✅ Сегодня ничего списывать и уценять не нужно.")
        else:
            parts.append("\nОтметить — кнопками под напоминаниями или в приложении 📱")
        n = len({p.barcode for p in items})
        parts.append(f"\n📦 На полке: {n} {'товар' if n % 10 == 1 and n % 100 != 11 else 'товара' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'товаров'}")
        return "\n".join(parts)

    async def send_morning_plan(self) -> int:
        """Утренний план в каждую группу. Возвращает, в сколько групп отправлено."""
        today = self.today()
        products = await self.db.list_all_open()
        sent = 0
        for chat_id in await self.group_chats():
            items = [p for p in products if p.chat_id == chat_id]
            link = await self.app_link(chat_id)
            kb = InlineKeyboardMarkup(inline_keyboard=app_button(link)) if link else None
            try:
                await self.bot.send_message(chat_id, self.morning_text(items, today)[:4000], reply_markup=kb)
                sent += 1
            except Exception:
                log.exception("Не удалось отправить утренний план в чат %s", chat_id)
        return sent

    MORNING_WINDOW_HOURS = 3   # «План на день» — только с NOTIFY_TIME до +3 часов (9:00–12:00)

    def window_end(self):
        t = self.cfg.notify_time
        return t.replace(hour=min(23, t.hour + self.MORNING_WINDOW_HOURS))

    async def morning_routine(self, force: bool = False, max_fetch: int | None = None,
                              with_backup: bool = True) -> dict:
        """Всё утреннее разом — и ровно один раз за день, кто бы ни запустил
        (GitHub по расписанию, задание PythonAnywhere или цикл бота):
        напоминания с фото по партиям, «План на день» в группу, резервная копия владельцу."""
        now = self.now()
        today = now.date().isoformat()
        report: dict = {"date": today, "local_time": now.strftime("%H:%M")}
        if not force:
            if now.time() < self.cfg.notify_time:
                report["skipped"] = f"ещё рано — утренний план в {self.cfg.notify_time.strftime('%H:%M')}"
                return report
            if now.time() >= self.window_end():
                # запуск опоздал (GitHub бывает задерживает расписание на часы) — «Доброе утро»
                # вечером никому не нужно. Напоминания по партиям и копия — всё равно.
                report["skipped"] = (f"поздно — утренний план отправляется только до "
                                     f"{self.window_end().strftime('%H:%M')}")
                await self.run_checks()
                try:
                    report["backup"] = await self.send_backup(max_fetch=max_fetch)
                except Exception as e:
                    log.exception("Не удалось отправить резервную копию")
                    report["backup"] = f"ошибка: {e}"
                return report
            if await self.db.get_setting("morning_last_day") == today:
                report["skipped"] = "сегодня уже отправлено"
                return report
        await self.db.set_setting("morning_last_day", today)   # сразу — чтобы второй запуск не задублировал
        await self.run_checks()
        report["plan_sent_to"] = await self.send_morning_plan()
        if not with_backup:          # быстрый режим для внешнего будильника (лимит 30 с)
            return report
        try:
            report["backup"] = await self.send_backup(max_fetch=max_fetch)
        except Exception as e:
            log.exception("Не удалось отправить резервную копию")
            report["backup"] = f"ошибка: {e}"
        return report

    async def send_daily_summary(self) -> None:
        """Совместимость: раньше — сводка просроченного; теперь это часть утреннего плана."""
        await self.send_morning_plan()

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
        """Режим polling: раз в минуту — утренний план (один раз в день после NOTIFY_TIME)
        и напоминания по партиям, у которых подошёл срок."""
        while True:
            try:
                await self.morning_routine()
                if self.now().time() >= self.cfg.notify_time:
                    await self.run_checks()
            except Exception:
                log.exception("Ошибка в цикле проверки сроков")
            await asyncio.sleep(60)
