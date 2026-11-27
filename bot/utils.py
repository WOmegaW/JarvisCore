import html
import io
import re
from datetime import date

from .db import ACTIVE, DISCOUNTED, SOLD, WRITTEN_OFF, Product

BARCODE_RE = re.compile(r"^\d{4,20}$")
ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
FULL_RE = re.compile(r"(?<![\d.])(\d{1,2})[./\-](\d{1,2})[./\-](\d{4}|\d{2})(?![\d.])")
SHORT_RE = re.compile(r"(?<![\d.])(\d{1,2})[./\-](\d{1,2})(?![\d.%])")


def find_date(text: str, today: date) -> tuple[date, tuple[int, int]] | None:
    """Ищет дату в тексте. Понимает 25.12.2026, 25.12.26, 25/12, 25-12-2026, 2026-12-25.
    Сначала даты с годом, потом без. Если год не указан и дата уже прошла — берётся
    следующий год. Возвращает (дата, позиция в тексте)."""
    for regex, kind in ((ISO_RE, "iso"), (FULL_RE, "full"), (SHORT_RE, "short")):
        for m in regex.finditer(text):
            try:
                if kind == "iso":
                    y, mo, d = map(int, m.groups())
                    result = date(y, mo, d)
                elif kind == "full":
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    result = date(y + 2000 if y < 100 else y, mo, d)
                else:
                    d, mo = int(m.group(1)), int(m.group(2))
                    result = date(today.year, mo, d)
                    if result < today:
                        result = date(today.year + 1, mo, d)
            except ValueError:
                continue
            return result, m.span()
    return None


def parse_date(text: str, today: date) -> date | None:
    found = find_date(text, today)
    return found[0] if found else None


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def plural_days(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


def days_left_text(expiry: date, today: date) -> str:
    left = (expiry - today).days
    if left > 1:
        return f"осталось {plural_days(left)}"
    if left == 1:
        return "истекает завтра"
    if left == 0:
        return "истекает СЕГОДНЯ"
    return f"просрочен {plural_days(left)} назад"


def status_icon(p: Product, today: date, discount_days: int) -> str:
    if p.status == WRITTEN_OFF:
        return "🗑"
    if p.status == SOLD:
        return "💰"
    left = (p.expiry_date - today).days
    if left <= 0:
        return "🔴"
    if p.status == DISCOUNTED:
        return "🏷"
    if left <= discount_days:
        return "🟠"
    return "🟢"


def status_text(p: Product) -> str:
    return {
        ACTIVE: "на полке",
        DISCOUNTED: "скидка поставлена",
        WRITTEN_OFF: "списан",
        SOLD: "продан",
    }.get(p.status, p.status)


def product_card(p: Product, today: date) -> str:
    e = html.escape
    lines = [
        f"📦 <b>{e(p.title)}</b>  <code>#{p.id}</code>",
        f"🔢 Штрихкод: <code>{e(p.barcode)}</code>",
        f"📅 Годен до: <b>{fmt_date(p.expiry_date)}</b> ({days_left_text(p.expiry_date, today)})",
        f"📌 Статус: {status_text(p)}",
    ]
    if p.added_by:
        lines.append(f"👤 Добавил(а): {e(p.added_by)}")
    if p.discount_by:
        lines.append(f"🏷 Скидку поставил(а): {e(p.discount_by)}")
    if p.sold_by:
        lines.append(f"💰 Продажу отметил(а): {e(p.sold_by)}")
    if p.writeoff_by:
        lines.append(f"🗑 Списал(а): {e(p.writeoff_by)}")
    return "\n".join(lines)


def find_barcode_in_text(text: str) -> str | None:
    """Ищет в тексте число из 8–14 цифр (EAN-8, EAN-13, UPC, ITF-14)."""
    for token in re.findall(r"\d+", text):
        if 8 <= len(token) <= 14:
            return token
    return None


def decode_barcode(image_bytes: bytes) -> str | None:
    """Пытается прочитать штрихкод с фото. Работает, только если установлены
    pyzbar + Pillow и системная libzbar0. Иначе просто возвращает None."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        for result in decode(img):
            value = result.data.decode("utf-8", "ignore").strip()
            if value.isdigit():
                return value
    except Exception:
        return None
    return None
