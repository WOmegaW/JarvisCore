"""API мини-приложения: доступ, добавление, автоподстановка, продажа/списание, сбои Telegram."""
import base64

from conftest import CHAT, JPEG, add_product, days, make_init_data


# ---------- доступ ----------

def test_requires_telegram_signature(client):
    assert client.get("/api/state").status_code == 401
    tampered = make_init_data().replace(f"start_param={CHAT}", "start_param=-100999")
    assert client.get("/api/state", headers={"X-Init-Data": tampered}).status_code == 401


def test_stranger_is_rejected(client, tg):
    r = client.get("/api/state", headers={"X-Init-Data": make_init_data(user_id=8)})
    assert r.status_code == 403 and "не состоите" in r.get_json()["error"]


def test_other_group_is_rejected(client):
    r = client.get("/api/state", headers={"X-Init-Data": make_init_data(start_param="-100555")})
    assert r.status_code == 403


def test_old_session_is_rejected(client):
    r = client.get("/api/state", headers={"X-Init-Data": make_init_data(auth_date=1700000000)})
    assert r.status_code == 401 and "давно" in r.get_json()["error"]


# ---------- добавление ----------

def test_add_posts_card_to_group(client, auth, tg):
    p = add_product(client, auth, qty=6)
    assert p["quantity"] == 6 and p["photo"]
    assert any("Товар добавлен" in t for t in tg.texts("SendPhoto"))


def test_add_multipart_form_still_works(client, auth):
    import io
    r = client.post("/api/products", headers=auth, data={
        "barcode": "4600000000017", "name": "Хлеб", "expiry": days(3), "quantity": "2",
        "photo": (io.BytesIO(JPEG), "p.jpg")})
    assert r.status_code == 200 and r.get_json()["product"]["quantity"] == 2


def test_add_validation(client, auth):
    bad = [
        ({"barcode": "abc", "expiry": days(3)}, "Штрихкод"),
        ({"barcode": "1234", "expiry": "2020-01-01"}, "дата уже прошла"),
        ({"barcode": "1234", "expiry": days(3), "quantity": 0}, "Количество"),
        ({"barcode": "1234", "expiry": days(3)}, "фото"),   # новый товар без фото
    ]
    for body, msg in bad:
        r = client.post("/api/products", headers=auth, json=body)
        assert r.status_code == 400 and msg in r.get_json()["error"], body


def test_autofill_and_reuse_photo(client, auth):
    add_product(client, auth, barcode="4601234567890", name="Молоко 3.2%")
    r = client.get("/api/lookup/4601234567890", headers=auth).get_json()
    assert r["found"] and r["name"] == "Молоко 3.2%" and len(r["on_shelf"]) == 1
    # второй раз — без фото и без названия
    p = add_product(client, auth, barcode="4601234567890", name="", photo=False, expiry_days=9)
    assert p["title"] == "Молоко 3.2%" and p["photo"]
    assert client.get("/api/lookup/4609999999999", headers=auth).get_json() == {"found": False}


def test_failed_photo_upload_leaves_nothing(client, auth, tg):
    tg.fail["SendPhoto"] = 1
    body = {"barcode": "4820000000001", "name": "Crisps", "expiry": days(5),
            "photo_b64": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()}
    r = client.post("/api/products", headers=auth, json=body)
    assert r.status_code == 400 and "ещё раз" in r.get_json()["error"]
    assert client.get("/api/state", headers=auth).get_json()["products"] == []


# ---------- продажа и списание ----------

def test_store_scenario_two_dates_of_one_product(client, auth):
    """Тот же товар, два срока: на ранний — скидка и списание, поздний остаётся на полке."""
    early = add_product(client, auth, barcode="4820000000001", name="Crisps", expiry_days=2)["id"]
    late = add_product(client, auth, barcode="4820000000001", name="", photo=False, expiry_days=9)["id"]
    assert client.post(f"/api/products/{early}/discount", headers=auth).status_code == 200
    r = client.post(f"/api/products/{early}/writeoff", headers=auth, json={}).get_json()["product"]
    assert (r["quantity"], r["status"]) == (0, "written_off")          # вся партия целиком
    [left] = client.get("/api/state", headers=auth).get_json()["products"]
    assert (left["id"], left["title"]) == (late, "Crisps")


def test_old_quantities_are_reset_to_one(tmp_path):
    """Один раз при обновлении: всё, что на полке, — по 1 шт. (количество больше не ведём)."""
    import asyncio
    import sqlite3
    from bot.db import Database
    path = tmp_path / "old.db"
    db = Database(str(path))
    asyncio.run(db.init())
    con = sqlite3.connect(path)
    con.execute("DELETE FROM settings WHERE key = 'qty_removed_v1'")     # как база до обновления
    con.execute("INSERT INTO products (chat_id, photo_file_id, barcode, expiry_date, quantity, initial_qty) "
                "VALUES (-1, 'F', '1', '2030-01-01', 6, 6)")
    con.commit()
    con.close()
    asyncio.run(db.init())
    [p] = asyncio.run(db.list_open(-1))
    assert (p.quantity, p.initial_qty) == (1, 1)


def test_group_message_failure_is_not_an_error(client, auth, tg):
    """Если не ушло сообщение в группу — продажа всё равно засчитана и ответ успешный (раньше был 500)."""
    pid = add_product(client, auth, qty=2)["id"]
    tg.fail["SendMessage"] = 1
    r = client.post(f"/api/products/{pid}/sold", headers=auth, json={"qty": 2})
    assert r.status_code == 200 and r.get_json()["product"]["status"] == "sold"
    again = client.post(f"/api/products/{pid}/sold", headers=auth, json={"qty": 2})
    assert again.status_code == 400 and "продан полностью" in again.get_json()["error"]


def test_qty_is_clamped_to_shelf(client, auth):
    pid = add_product(client, auth, qty=2)["id"]
    r = client.post(f"/api/products/{pid}/writeoff", headers=auth, json={"qty": 99}).get_json()["product"]
    assert (r["quantity"], r["written_off_qty"], r["status"]) == (0, 2, "written_off")


def test_discount_only_once(client, auth):
    pid = add_product(client, auth, expiry_days=2)["id"]
    assert client.post(f"/api/products/{pid}/discount", headers=auth).status_code == 200
    r = client.post(f"/api/products/{pid}/discount", headers=auth)
    assert r.status_code == 400 and "уже отмечена" in r.get_json()["error"]


def test_cannot_touch_other_groups_product(client, auth):
    pid = add_product(client, auth)["id"]
    other = {"X-Init-Data": make_init_data(start_param=str(CHAT))}
    assert client.post(f"/api/products/{pid + 1000}/sold", headers=other).status_code == 404


# ---------- изменение и удаление ----------

def test_edit_changes_and_announces(client, auth, tg):
    pid = add_product(client, auth, name="Молоко", qty=6)["id"]
    r = client.post(f"/api/products/{pid}/edit", headers=auth,
                    json={"barcode": "4601234567890", "name": "Молоко Простоквашино", "expiry": days(4), "quantity": 5})
    p = r.get_json()["product"]
    assert (p["title"], p["quantity"]) == ("Молоко Простоквашино", 5)
    assert any("изменил(а)" in t for t in tg.texts("SendMessage"))


def test_name_and_photo_are_shared_by_all_dates_of_one_barcode(client, auth):
    """Одна карточка на штрихкод: новое название (и фото) — у всех партий на полке."""
    a = add_product(client, auth, barcode="4601234567890", name="Молоко", expiry_days=1)
    b = add_product(client, auth, barcode="4601234567890", name="", photo=False, expiry_days=5)
    client.post(f"/api/products/{b['id']}/edit", headers=auth,
                json={"barcode": "4601234567890", "name": "Молоко Простоквашино", "expiry": days(5), "quantity": 1,
                      "photo_b64": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()})
    ps = {p["id"]: p for p in client.get("/api/state", headers=auth).get_json()["products"]}
    assert ps[a["id"]]["title"] == ps[b["id"]]["title"] == "Молоко Простоквашино"
    assert ps[a["id"]]["expiry"] != ps[b["id"]]["expiry"]          # сроки у партий свои


def test_edit_survives_group_message_failure(client, auth, tg):
    pid = add_product(client, auth)["id"]
    tg.fail["SendMessage"] = 1
    r = client.post(f"/api/products/{pid}/edit", headers=auth,
                    json={"barcode": "4601234567890", "name": "Новое", "expiry": days(6), "quantity": 1})
    assert r.status_code == 200 and r.get_json()["product"]["title"] == "Новое"


def test_delete(client, auth):
    pid = add_product(client, auth)["id"]
    assert client.post(f"/api/products/{pid}/delete", headers=auth).get_json() == {"ok": True}
    assert client.get("/api/state", headers=auth).get_json()["products"] == []


# ---------- фото ----------

def test_photo_urls_are_signed_and_thumbnails_small(client, auth):
    p = add_product(client, auth)
    full = client.get(p["photo"])
    thumb = client.get(p["photo"] + "?thumb=1")
    assert full.status_code == 200 and thumb.status_code == 200
    assert len(thumb.data) < len(full.data)
    assert client.get(p["photo"][:-4] + "zzzz").status_code == 404
    b64 = client.get(p["photo"].replace("/api/photo/", "/api/photo64/") + "?thumb=1").get_json()
    assert b64["data"].startswith("data:image/jpeg;base64,")


def test_app_page_served(client):
    r = client.get("/app")
    assert r.status_code == 200 and b"MixMarkt" in r.data
    assert client.get("/app/static/html5-qrcode.min.js").status_code == 200


# ---------- каталог ----------

def test_catalog_lists_every_barcode_once(client, auth):
    a = add_product(client, auth, barcode="4601234567890", name="Молоко", qty=2, expiry_days=1)
    add_product(client, auth, barcode="4601234567890", name="", photo=False, qty=6, expiry_days=5)
    gone = add_product(client, auth, barcode="4600000000017", name="Хлеб", qty=1)
    client.post(f"/api/products/{gone['id']}/sold", headers=auth, json={})       # продан — с полки ушёл
    items = {i["barcode"]: i for i in client.get("/api/catalog", headers=auth).get_json()["items"]}
    assert set(items) == {"4601234567890", "4600000000017"}
    milk, bread = items["4601234567890"], items["4600000000017"]
    assert (milk["title"], milk["on_shelf"], milk["dates_on_shelf"], milk["times_added"]) == ("Молоко", 8, 2, 2)
    assert (bread["title"], bread["on_shelf"]) == ("Хлеб", 0)                    # в каталоге остаётся
    assert milk["photo"] and client.get(milk["photo"]).status_code == 200
    assert a["id"]


def test_catalog_requires_auth(client):
    assert client.get("/api/catalog").status_code == 401


def test_page_carries_server_version(client, auth):
    """Страница знает свою версию и сравнивает с сервером — старая страница из памяти Telegram перезагрузится."""
    v = client.get("/api/state", headers=auth).get_json()["app_version"]
    page = client.get("/app")
    assert len(v) >= 7 and f'const PAGE_VERSION = "{v}"'.encode() in page.data
    assert "no-store" in page.headers["Cache-Control"]
    assert b"f-qty" not in page.data and "Количество".encode() not in page.data

