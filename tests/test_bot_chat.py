"""Бот в чате группы: добавление, автоподстановка, команды, кнопки под напоминаниями."""
from conftest import CHAT, days


def ddmmyyyy(n):
    y, m, d = days(n).split("-")
    return f"{d}.{m}.{y}"


def last_text(tg):
    return tg.texts()[-1]


def test_wrong_secret_is_rejected(client):
    r = client.post("/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert r.status_code == 403


def test_help(chat, tg):
    chat.message("/help")
    assert "Бот учёта сроков годности" in last_text(tg)


def test_quick_add_with_quantity(chat, tg, client, auth):
    chat.message(photo="P1", caption=f"4601234567890 {ddmmyyyy(10)} Молоко 3.2% 6шт")
    assert "Товар добавлен" in last_text(tg)
    [p] = client.get("/api/state", headers=auth).get_json()["products"]
    assert (p["title"], p["quantity"]) == ("Молоко 3.2%", 6)


def test_quick_add_uses_known_name(chat, tg, client, auth):
    chat.message(photo="P1", caption=f"4601234567890 {ddmmyyyy(10)} Кефир")
    chat.message(photo="P2", caption=f"4601234567890 x3 {ddmmyyyy(12)}")
    titles = sorted((p["title"], p["quantity"]) for p in client.get("/api/state", headers=auth).get_json()["products"])
    assert titles == [("Кефир", 1), ("Кефир", 3)]


def test_ordinary_photos_are_ignored(chat, tg):
    chat.message(photo="P1", caption="смотрите какая витрина")
    assert tg.texts() == []


def test_step_by_step_add_with_autofill(chat, tg):
    chat.message(photo="P0", caption=f"4601234567890 {ddmmyyyy(10)} Йогурт")
    chat.message("/add")
    assert "Шаг 1/4" in last_text(tg)
    chat.message(photo="P1")
    assert "штрихкод" in last_text(tg)
    chat.message("4601234567890")
    assert "уже вносили" in last_text(tg) and "Йогурт" in last_text(tg)
    chat.message(ddmmyyyy(5))
    assert "Товар добавлен" in last_text(tg)


def test_step_by_step_rejects_past_date(chat, tg):
    chat.message("/add")
    chat.message(photo="P1")
    chat.message("4609999999999")
    chat.message("Новый товар")
    chat.message("01.01.2020")
    assert "уже прошла" in last_text(tg)


def test_sold_and_writeoff_commands(chat, tg):
    chat.message(photo="P1", caption=f"4601234567890 {ddmmyyyy(10)} Хлеб 6шт")
    chat.message("/sold 1 2")
    assert "Продано 2 шт." in last_text(tg) and "осталось 4" in last_text(tg)
    chat.message("/writeoff 1 1")
    assert "Списано 1 шт." in last_text(tg)
    chat.message("/sold 1")
    assert "Продано 3 шт." in last_text(tg)
    chat.message("/sold 1")
    assert "уже продан" in last_text(tg)


def test_reminder_buttons(chat, tg):
    chat.message(photo="P1", caption=f"4601234567890 {ddmmyyyy(10)} Хлеб")
    chat.button("sold:1")
    assert "Всё продано" in tg.texts("EditMessageCaption")[-1]
    tg.calls.clear()
    chat.button("off:1")                        # уже закрыт — просто убираем кнопки
    assert "EditMessageReplyMarkup" in tg.names()


def test_list_and_find(chat, tg):
    chat.message(photo="P1", caption=f"4601234567890 {ddmmyyyy(10)} Сыр 2шт")
    chat.message("/list")
    assert "Сыр ×2" in last_text(tg)
    chat.message("/find 4601234567890")
    assert "Сыр" in last_text(tg)


def test_app_command_gives_link(chat, tg):
    chat.message("/app")
    btn = tg.calls[-1][1].reply_markup.inline_keyboard[0][0]
    assert btn.url == f"https://t.me/mixalmacen_bot?startapp={CHAT}"
