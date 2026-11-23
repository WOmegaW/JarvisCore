"""Общая подготовка тестов.

Тесты не ходят в настоящий Telegram: вместо сети подставляется FakeTelegram, который
записывает все вызовы Bot API (их можно проверить) и умеет имитировать сбои —
например, отказ прокси PythonAnywhere при отправке сообщения в группу.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# окружение — до импорта бота (config читается при импорте flask_app)
TOKEN = "123456:TEST-token"
CHAT = -1001234567890
USER_ID = 7
_tmp = tempfile.mkdtemp(prefix="expiry-tests-")
os.environ.update({
    "BOT_TOKEN": TOKEN,
    "DB_PATH": os.path.join(_tmp, "test.db"),
    "ALLOWED_CHAT_IDS": str(CHAT),
    "WEBHOOK_SECRET": "test_webhook_secret_123456",
    "STORE_TZ": "Europe/Madrid",
})
os.environ.pop("PROXY_URL", None)

from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramNetworkError  # noqa: E402
from aiogram.types import (  # noqa: E402
    Chat, ChatMemberLeft, ChatMemberMember, Message, PhotoSize, User,
)


class FakeTelegram(BaseSession):
    """Подменяет сеть: отвечает правдоподобными объектами и запоминает вызовы."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, object]] = []
        self.fail: dict[str, int] = {}      # имя метода → сколько раз подряд «упасть»
        self.members = {USER_ID}            # кто состоит в группе
        self._next_file = 0

    def reset(self):
        self.calls.clear()
        self.fail.clear()
        self.members = {USER_ID}

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def texts(self, method: str | None = None) -> list[str]:
        out = []
        for n, m in self.calls:
            if method and n != method:
                continue
            t = getattr(m, "text", None) or getattr(m, "caption", None)
            if t:
                out.append(t)
        return out

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise TelegramNetworkError(method=method, message="нет связи с Telegram (сервер на минуту перегружен)")
        now = dt.datetime.now()
        chat = Chat(id=getattr(method, "chat_id", CHAT) or CHAT, type="supergroup")
        if name == "GetMe":
            return User(id=123456, is_bot=True, first_name="MixAlmacén", username="mixalmacen_bot")
        if name == "GetChatMember":
            u = User(id=method.user_id, is_bot=False, first_name="x")
            return ChatMemberMember(user=u) if method.user_id in self.members else ChatMemberLeft(user=u)
        if name == "SendPhoto":
            self._next_file += 1
            return Message(message_id=100 + self._next_file, date=now, chat=chat,
                           photo=[PhotoSize(file_id=f"FILE{self._next_file}", file_unique_id=f"u{self._next_file}",
                                            width=1, height=1)])
        if name.startswith("Send"):
            return Message(message_id=99, date=now, chat=chat)
        if name == "GetFile":
            from aiogram.types import File
            return File(file_id=method.file_id, file_unique_id="u", file_path="photos/x.jpg")
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield JPEG

    async def close(self):
        pass


def _tiny_jpeg() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (240, 200, 120)).save(buf, "JPEG")
    return buf.getvalue()


JPEG = _tiny_jpeg()

import flask_app  # noqa: E402

FAKE = FakeTelegram()
flask_app.bot.session = FAKE


def make_init_data(user_id: int = USER_ID, start_param: str = str(CHAT), auth_date: int | None = None,
                   first_name: str = "Аня") -> str:
    """initData, подписанный так же, как это делает Telegram."""
    d = {
        "auth_date": str(auth_date or int(dt.datetime.now().timestamp())),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": first_name, "last_name": "К"}, ensure_ascii=False),
        "start_param": start_param,
    }
    check = "\n".join(f"{k}={d[k]}" for k in sorted(d))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(d)


@pytest.fixture(autouse=True)
def clean_state():
    """Каждый тест — с пустой базой и чистым журналом Telegram."""
    con = sqlite3.connect(os.environ["DB_PATH"])
    for t in ("products", "events", "fsm", "settings"):
        con.execute(f"DELETE FROM {t}")
    con.execute("DELETE FROM sqlite_sequence")   # номера товаров снова с #1
    import shutil
    shutil.rmtree(os.path.join(os.path.dirname(os.environ["DB_PATH"]), "photos"), ignore_errors=True)
    con.commit()
    con.close()
    FAKE.reset()
    yield


@pytest.fixture
def tg() -> FakeTelegram:
    return FAKE


@pytest.fixture
def client():
    flask_app.app.testing = False  # ошибки — как на настоящем сервере (500 + наш обработчик)
    return flask_app.app.test_client()


@pytest.fixture
def auth():
    return {"X-Init-Data": make_init_data()}


@pytest.fixture
def today():
    return flask_app.notifier.today()


def days(n: int) -> str:
    return (flask_app.notifier.today() + dt.timedelta(days=n)).isoformat()


def add_product(client, auth, barcode="4601234567890", name="Молоко", expiry_days=10, qty=1, photo=True):
    import base64
    body = {"barcode": barcode, "name": name, "expiry": days(expiry_days), "quantity": qty}
    if photo:
        body["photo_b64"] = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    r = client.post("/api/products", headers=auth, json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["product"]


class ChatSim:
    """Имитация группы: шлёт боту обновления так, как их прислал бы Telegram."""

    def __init__(self, client):
        self.client = client
        self.n = 0
        self.headers = {"X-Telegram-Bot-Api-Secret-Token": os.environ["WEBHOOK_SECRET"]}

    def _post(self, update):
        self.n += 1
        update["update_id"] = self.n
        r = self.client.post("/webhook", json=update, headers=self.headers)
        assert r.status_code == 200

    def message(self, text=None, photo=None, caption=None, private=False, user_id=USER_ID):
        chat = ({"id": user_id, "type": "private", "first_name": "Аня"} if private
                else {"id": CHAT, "type": "supergroup", "title": "Shop"})
        m = {"message_id": self.n + 1, "date": 1700000000, "chat": chat,
             "from": {"id": user_id, "is_bot": False, "first_name": "Аня"}}
        if text is not None:
            m["text"] = text
            if text.startswith("/"):
                m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if photo:
            m["photo"] = [{"file_id": photo, "file_unique_id": photo + "u", "width": 1, "height": 1}]
            if caption:
                m["caption"] = caption
        self._post({"message": m})

    def button(self, data):
        self._post({"callback_query": {
            "id": "cb1", "chat_instance": "x", "data": data,
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Аня"},
            "message": {"message_id": 50, "date": 1700000000, "chat": {"id": CHAT, "type": "supergroup"},
                        "photo": [{"file_id": "F", "file_unique_id": "u", "width": 1, "height": 1}],
                        "caption": "НАПОМИНАНИЕ"}}})


@pytest.fixture
def chat(client):
    return ChatSim(client)
