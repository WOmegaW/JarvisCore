"""Разбор дат, штрихкодов и тексты."""
from datetime import date

import pytest

from bot.utils import days_left_text, find_barcode_in_text, find_date, parse_date, plural_days

TODAY = date(2026, 11, 22)


@pytest.mark.parametrize("text, expected", [
    ("25.12.2026", date(2026, 12, 25)),
    ("25.12.26", date(2026, 12, 25)),
    ("25/12", date(2026, 12, 25)),
    ("2026-12-25", date(2026, 12, 25)),
    ("01.01", date(2027, 1, 1)),          # без года и уже прошло → следующий год
    ("31.02.2026", None),
    ("абв", None),
])
def test_parse_date(text, expected):
    assert parse_date(text, TODAY) == expected


def test_full_date_wins_over_fat_percent():
    # «3.2%» в названии не должно считаться датой 3 февраля
    d, (a, b) = find_date("Молоко 3.2% 25.12.2026", TODAY)
    assert d == date(2026, 12, 25)


def test_barcode_in_caption():
    assert find_barcode_in_text("4601234567890 25.12.2026 Молоко") == "4601234567890"
    assert find_barcode_in_text("Молоко 3.2% 6шт") is None


@pytest.mark.parametrize("n, word", [(1, "1 день"), (2, "2 дня"), (5, "5 дней"), (11, "11 дней"), (21, "21 день"), (22, "22 дня")])
def test_plural(n, word):
    assert plural_days(n) == word


def test_days_left_text():
    assert days_left_text(date(2026, 11, 22), TODAY) == "истекает СЕГОДНЯ"
    assert days_left_text(date(2026, 11, 23), TODAY) == "истекает завтра"
    assert "просрочен" in days_left_text(date(2026, 11, 20), TODAY)
