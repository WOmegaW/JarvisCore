import html
import io
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Config
from .db import Database
from .notifier import Notifier
from .utils import (
    BARCODE_RE,
    decode_barcode,
    days_left_text,
    find_barcode_in_text,
    find_date,
    fmt_date,
    parse_date,
    product_card,
    status_icon,
    status_text,
)

router = Router()

HELP = (
    "🛒 <b>Бот учёта сроков годности</b>\n\n"
    "<b>Добавить товар:</b>\n"
    "• /add — бот пошагово спросит фото, штрихкод, название и срок годности\n"
    "• или быстро: отправьте <b>фото с подписью</b>\n"
    "  <code>4601234567890 25.12.2026 Молоко 3.2%</code>\n"
    "  (штрихкод, дата, название — название можно не писать)\n"
    "  Тот же товар с другим сроком — просто внесите ещё раз с новой датой.\n\n"
    "<b>Что делает бот:</b>\n"
    "🟠 за {days} дн. до окончания срока — напоминает поставить скидку {pct}%\n"
    "🔴 в день окончания срока — напоминает списать товар\n"
    "Под напоминанием есть кнопка — нажмите её, когда сделали.\n\n"
    "📱 /app — открыть удобное приложение (кнопка внутри Telegram)\n\n"
    "<b>Команды:</b>\n"
    "/list — все товары на учёте\n"
    "/soon — что нужно уценить или списать\n"
    "/show <code>N</code> — карточка товара с фото\n"
    "/find <code>штрихкод</code> — найти товар\n"
    "/writeoff <code>N</code> — списать товар №N\n"
    "/sold <code>N</code> — отметить, что товар №N продан\n"
    "/del <code>N</code> — удалить ошибочную запись\n"
    "/check — проверить сроки прямо сейчас\n"
    "/backup — резервная копия всех товаров (присылается в личный чат, настройка — /backup_here)\n"
    "/cancel — отменить добавление\n"
    "/id — ID этого чата"
)


class AddProduct(StatesGroup):
    photo = State()
    barcode = State()
    name = State()
    expiry = State()


def who(message_or_cb) -> str:
    u = message_or_cb.from_user
    return u.full_name if u else "кто-то"


async def download_photo(bot: Bot, file_id: str) -> bytes | None:
    try:
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        return buf.getvalue()
    except Exception:
        return None


# ---------- общие команды ----------

@router.message(Command("start", "help"))
async def cmd_help(message: Message, cfg: Config):
    await message.answer(HELP.format(days=cfg.discount_days_before, pct=cfg.discount_percent))


@router.message(Command("app"))
async def cmd_app(message: Message, notifier: Notifier):
    if message.chat.type == "private":
        await message.answer("Приложение открывается из рабочей группы: напишите /app там.")
        return
    link = await notifier.app_link(message.chat.id)
    if not link:
        await message.answer("Приложение выключено в настройках (WEBAPP=0).")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 Открыть учёт", url=link)]])
    await message.answer(
        "📱 <b>Учёт сроков годности</b>\n\n"
        "Нажмите кнопку, чтобы открыть приложение: список товаров, добавление с фото "
        "и сканером штрихкода, отметки о скидке и списании.\n\n"
        "Совет: закрепите это сообщение 📌",
        reply_markup=kb,
    )


# ---------- резервные копии ----------

async def is_staff(bot: Bot, cfg: Config, user_id: int) -> bool:
    """Состоит ли человек в рабочей группе (для команд в личном чате)."""
    if not cfg.allowed_chat_ids:
        return True
    for chat_id in cfg.allowed_chat_ids:
        try:
            m = await bot.get_chat_member(chat_id, user_id)
            if m.status in ("creator", "administrator", "member", "restricted"):
                return True
        except Exception:
            continue
    return False


@router.message(Command("backup_here"))
async def cmd_backup_here(message: Message, bot: Bot, cfg: Config, db: Database, notifier: Notifier):
    if message.chat.type != "private":
        await message.reply("Напишите /backup_here мне в <b>личный чат</b> — туда я буду присылать копии.")
        return
    if not await is_staff(bot, cfg, message.from_user.id):
        await message.answer("Резервные копии доступны только сотрудникам рабочей группы.")
        return
    await db.set_setting("backup_chat_id", str(message.chat.id))
    await message.answer(
        "💾 Готово! Каждое утро буду присылать сюда резервную копию: база, таблица для Excel и фото "
        "всех товаров. Telegram хранит эти файлы бессрочно.\n\nСейчас пришлю первую копию…"
    )
    try:
        await notifier.send_backup(force=True, max_fetch=40)
    except Exception as e:
        await message.answer(f"Не получилось собрать копию: {html.escape(str(e))}. Попробуйте /backup позже.")


@router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot, cfg: Config, db: Database, notifier: Notifier):
    if message.chat.type == "private" and not await is_staff(bot, cfg, message.from_user.id):
        await message.answer("Резервные копии доступны только сотрудникам рабочей группы.")
        return
    if not await db.get_setting("backup_chat_id"):
        await message.reply("Сначала напишите мне /backup_here в <b>личный чат</b> — туда я пришлю копию.")
        return
    try:
        await notifier.send_backup(force=True, max_fetch=40)
    except Exception as e:
        await message.reply(f"Не получилось собрать копию: {html.escape(str(e))}")
        return
    if message.chat.type != "private":
        await message.reply("💾 Резервная копия отправлена владельцу в личный чат.")


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"ID этого чата: <code>{message.chat.id}</code>")


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("❌ Добавление отменено.")


# ---------- пошаговое добавление ----------

@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddProduct.photo)
    await message.reply("📸 Шаг 1/4. Пришлите <b>фото товара</b>.\n(/cancel — отменить)")


async def after_barcode(message: Message, state: FSMContext, db: Database, barcode: str, prefix: str = "") -> None:
    """Штрихкод известен: если такой товар уже вносили — подставляем название и сразу спрашиваем срок."""
    await state.update_data(barcode=barcode)
    known = await db.last_by_barcode(message.chat.id, barcode)
    if known and known.name:
        await state.update_data(name=known.name)
        await state.set_state(AddProduct.expiry)
        await message.reply(
            f"{prefix}🔁 Этот товар уже вносили: <b>{html.escape(known.name)}</b>\n\n"
            "📅 Введите <b>срок годности</b>, например <code>25.12.2026</code>\n"
            "(другое название можно поправить потом в /app)"
        )
        return
    await state.set_state(AddProduct.name)
    await message.reply(f"{prefix}✏️ Шаг 3/4. Напишите <b>название товара</b> (или «-», чтобы пропустить).")


@router.message(AddProduct.photo, F.photo)
async def add_photo(message: Message, state: FSMContext, bot: Bot, db: Database):
    file_id = message.photo[-1].file_id
    await state.update_data(photo=file_id)

    barcode = find_barcode_in_text(message.caption or "")
    if not barcode:
        data = await download_photo(bot, file_id)
        barcode = decode_barcode(data) if data else None
    if barcode:
        await after_barcode(message, state, db, barcode, f"🔢 Штрихкод распознан: <code>{barcode}</code>\n\n")
        return
    await state.set_state(AddProduct.barcode)
    await message.reply(
        "🔢 Шаг 2/4. Введите <b>штрихкод</b> цифрами (под полосками на упаковке)\n"
        "или пришлите чёткое фото штрихкода."
    )


@router.message(AddProduct.photo)
async def add_photo_wrong(message: Message):
    await message.reply("Нужно именно фото товара 📸 (или /cancel).")


@router.message(AddProduct.barcode, F.photo)
async def add_barcode_photo(message: Message, state: FSMContext, bot: Bot, db: Database):
    data = await download_photo(bot, message.photo[-1].file_id)
    barcode = decode_barcode(data) if data else None
    if not barcode:
        await message.reply("Не получилось прочитать штрихкод с фото 😔 Введите цифры вручную.")
        return
    await after_barcode(message, state, db, barcode, f"🔢 Штрихкод: <code>{barcode}</code>\n\n")


@router.message(AddProduct.barcode, F.text)
async def add_barcode_text(message: Message, state: FSMContext, db: Database):
    barcode = message.text.replace(" ", "").strip()
    if not BARCODE_RE.match(barcode):
        await message.reply("Штрихкод — это только цифры, например <code>4601234567890</code>. Попробуйте ещё раз.")
        return
    await after_barcode(message, state, db, barcode)


@router.message(AddProduct.name, F.text)
async def add_name(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=None if name in {"-", "—", ""} else name[:100])
    await state.set_state(AddProduct.expiry)
    await message.reply(
        "📅 Шаг 4/4. Введите <b>срок годности</b> (до какого числа годен).\n"
        "Например: <code>25.12.2026</code> или <code>25.12</code>"
    )


@router.message(AddProduct.expiry, F.text)
async def add_expiry(message: Message, state: FSMContext, db: Database, notifier: Notifier):
    today = notifier.today()
    expiry = parse_date(message.text, today)
    if expiry is None:
        await message.reply("Не понял дату 🤔 Напишите так: <code>25.12.2026</code>")
        return
    if expiry < today:
        await message.reply(
            f"Эта дата уже прошла ({fmt_date(expiry)}). Такой товар нужно сразу списать.\n"
            "Если ошиблись — введите дату ещё раз."
        )
        return
    data = await state.get_data()
    await state.clear()
    await save_product(message, db, notifier, data["photo"], data["barcode"], data.get("name"), expiry)


@router.message(StateFilter(AddProduct.barcode, AddProduct.name, AddProduct.expiry))
async def add_wrong_input(message: Message):
    await message.reply("Ожидаю текст. Или /cancel, чтобы отменить.")


# ---------- быстрое добавление: фото с подписью ----------

@router.message(StateFilter(None), F.photo, F.caption)
async def quick_add(message: Message, db: Database, notifier: Notifier):
    caption = message.caption
    barcode = find_barcode_in_text(caption)
    today = notifier.today()
    # убираем штрихкод из подписи, чтобы его цифры не мешали распознать дату
    rest = caption.replace(barcode, " ", 1) if barcode else caption
    found = find_date(rest, today)
    if not barcode or not found:
        # не похоже на товар — молча игнорируем обычные фото в группе
        return
    expiry, (a, b) = found
    if expiry < today:
        await message.reply(f"Дата {fmt_date(expiry)} уже прошла — такой товар нужно списать, а не добавлять.")
        return
    # название — всё, что осталось после штрихкода и даты
    rest = rest[:a] + " " + rest[b:]
    m = QTY_RE.search(rest)          # «6шт» / «x6» по привычке — просто убираем из названия
    if m:
        rest = rest[:m.start()] + " " + rest[m.end():]
    name = " ".join(rest.split())[:100] or None
    if not name:
        known = await db.last_by_barcode(message.chat.id, barcode)
        name = known.name if known else None
    await save_product(message, db, notifier, message.photo[-1].file_id, barcode, name, expiry)


QTY_RE = re.compile(r"(?:^|\s)(\d{1,4})\s*шт\.?(?=\s|$)|(?:^|\s)[xх×*](\d{1,4})(?=\s|$)", re.IGNORECASE)


async def save_product(message: Message, db: Database, notifier: Notifier,
                       photo: str, barcode: str, name: str | None, expiry, qty: int = 1) -> None:
    pid = await db.add(message.chat.id, photo, barcode, name, expiry, who(message), qty)
    product = await db.get(pid)
    today = notifier.today()
    await message.reply("✅ <b>Товар добавлен на учёт</b>\n\n" + product_card(product, today))
    # если срок уже близко — оповещение сразу, не дожидаясь утра
    await notifier.notify_product(product)


# ---------- просмотр ----------

@router.message(Command("list"))
async def cmd_list(message: Message, db: Database, notifier: Notifier, cfg: Config):
    items = await db.list_open(message.chat.id)
    if not items:
        await message.answer("Список пуст. Добавьте товар: /add")
        return
    today = notifier.today()
    lines = [f"📋 <b>Товары на учёте ({len(items)})</b>\n"]
    for p in items:
        icon = status_icon(p, today, cfg.discount_days_before)
        lines.append(
            f"{icon} <code>#{p.id}</code> {html.escape(p.title)} — {fmt_date(p.expiry_date)} "
            f"({days_left_text(p.expiry_date, today)})"
        )
    lines.append("\n🟢 в порядке  🟠 пора уценить  🏷 уценён  🔴 списать")
    await send_long(message, "\n".join(lines))


@router.message(Command("soon"))
async def cmd_soon(message: Message, db: Database, notifier: Notifier, cfg: Config):
    today = notifier.today()
    items = [p for p in await db.list_open(message.chat.id)
             if (p.expiry_date - today).days <= cfg.discount_days_before]
    if not items:
        await message.answer("👍 Ничего уценять или списывать не нужно.")
        return
    lines = ["⏰ <b>Требуют внимания</b>\n"]
    for p in items:
        left = (p.expiry_date - today).days
        if left <= 0:
            action = "СПИСАТЬ"
        elif p.status == "discounted":
            action = "уценён"
        else:
            action = f"поставить скидку {cfg.discount_percent}%"
        icon = status_icon(p, today, cfg.discount_days_before)
        lines.append(f"{icon} <code>#{p.id}</code> {html.escape(p.title)} — {fmt_date(p.expiry_date)} → {action}")
    lines.append("\nФото товара: /show <code>номер</code>")
    await send_long(message, "\n".join(lines))


async def send_long(message: Message, text: str) -> None:
    while text:
        if len(text) <= 4000:
            await message.answer(text)
            return
        cut = text.rfind("\n", 0, 4000)
        await message.answer(text[:cut])
        text = text[cut + 1:]


def parse_id(command: CommandObject) -> int | None:
    arg = (command.args or "").strip().lstrip("#")
    return int(arg) if arg.isdigit() else None


@router.message(Command("show"))
async def cmd_show(message: Message, command: CommandObject, db: Database, notifier: Notifier):
    pid = parse_id(command)
    p = await db.get(pid) if pid else None
    if not p or p.chat_id != message.chat.id:
        await message.reply("Укажите номер товара, например: /show 12")
        return
    await message.answer_photo(p.photo_file_id, caption=product_card(p, notifier.today()))


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject, db: Database, notifier: Notifier):
    code = (command.args or "").replace(" ", "")
    if not code.isdigit():
        await message.reply("Укажите штрихкод: /find 4601234567890")
        return
    items = await db.find_by_barcode(message.chat.id, code)
    if not items:
        await message.reply("Такой штрихкод не найден.")
        return
    today = notifier.today()
    lines = [f"🔎 Найдено: {len(items)}\n"]
    for p in items:
        lines.append(f"<code>#{p.id}</code> {html.escape(p.title)} — {fmt_date(p.expiry_date)}, "
                     f"{status_text(p) if p.is_closed else days_left_text(p.expiry_date, today)}")
    await message.answer("\n".join(lines))


@router.message(Command("check"))
async def cmd_check(message: Message, notifier: Notifier):
    await message.answer("🔄 Проверяю сроки...")
    await notifier.run_checks(chat_id=message.chat.id)
    await message.answer("Готово. Список того, что требует внимания: /soon")


# ---------- изменение статуса ----------

def parse_id_qty(command: CommandObject) -> tuple[int | None, int | None]:
    parts = (command.args or "").replace("#", " ").split()
    pid = int(parts[0]) if parts and parts[0].isdigit() else None
    qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0 else None
    return pid, qty


async def take_command(message: Message, command: CommandObject, db: Database, kind: str) -> None:
    pid, _ = parse_id_qty(command)
    p = await db.get(pid) if pid else None
    if not p or p.chat_id != message.chat.id:
        cmd = "writeoff" if kind == "writeoff" else "sold"
        await message.reply(f"Укажите номер товара: /{cmd} 12")
        return
    if p.is_closed:
        await message.reply(f"Этот товар уже {status_text(p)}.")
        return
    name = who(message)
    ok = await (db.set_written_off(p.id, name) if kind == "writeoff" else db.set_sold(p.id, name))
    if not ok:
        await message.reply("Не получилось — попробуйте ещё раз.")
        return
    verb = "🗑 Списано" if kind == "writeoff" else "💰 Продано"
    await message.reply(f"{verb}: <code>#{p.id}</code> {html.escape(p.title)} "
                        f"(до {fmt_date(p.expiry_date)}, {html.escape(name)})")


@router.message(Command("writeoff"))
async def cmd_writeoff(message: Message, command: CommandObject, db: Database):
    await take_command(message, command, db, "writeoff")


@router.message(Command("sold"))
async def cmd_sold(message: Message, command: CommandObject, db: Database):
    await take_command(message, command, db, "sold")


@router.message(Command("del"))
async def cmd_del(message: Message, command: CommandObject, db: Database):
    pid = parse_id(command)
    if not pid or not await db.delete(pid, message.chat.id):
        await message.reply("Укажите номер товара, например: /del 12")
        return
    await message.reply(f"Запись <code>#{pid}</code> удалена.")


@router.callback_query(F.data.startswith("disc:") | F.data.startswith("off:") | F.data.startswith("sold:"))
async def on_button(cb: CallbackQuery, db: Database):
    action, raw_id = cb.data.split(":", 1)
    pid = int(raw_id)
    p = await db.get(pid)
    if not p or p.chat_id != cb.message.chat.id:
        await cb.answer("Товар не найден", show_alert=True)
        return
    name = who(cb)
    if action == "disc":
        ok = await db.set_discounted(pid, name)
        note = f"✅ Скидку поставил(а): {html.escape(name)}"
        fail = "Уже отмечено ранее"
    elif action == "sold":
        ok = await db.set_sold(pid, name)
        note = f"💰 Всё продано — отметил(а): {html.escape(name)}"
        fail = f"Товар уже {status_text(p)}"
    else:
        ok = await db.set_written_off(pid, name)
        note = f"🗑 Списал(а): {html.escape(name)}"
        fail = f"Товар уже {status_text(p)}"
    msg = cb.message if isinstance(cb.message, Message) else None  # старые сообщения бывают недоступны
    if not ok:
        await cb.answer(fail)
        if msg:
            await msg.edit_reply_markup(reply_markup=None)
        return
    await cb.answer("Спасибо! Отмечено 👍")
    if msg:
        caption = (msg.html_text or "") + f"\n\n<b>{note}</b>"
        await msg.edit_caption(caption=caption[:1024], reply_markup=None)
