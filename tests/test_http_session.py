"""Запросы к Telegram через прокси PythonAnywhere: повтор при отказе «503» и понятная ошибка."""
import asyncio
import json
import select
import socket
import ssl
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiogram import Bot
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BufferedInputFile

import bot.http_session as hs
from bot.webapp import friendly_error


@pytest.fixture(scope="module")
def fake_telegram(tmp_path_factory):
    """HTTPS-сервер, отвечающий как Bot API."""
    d = tmp_path_factory.mktemp("tls")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", d / "k.pem",
                    "-out", d / "c.pem", "-days", "1", "-subj", "/CN=127.0.0.1"], check=True, capture_output=True)
    got = []

    class TG(BaseHTTPRequestHandler):
        def setup(self):
            self.request.do_handshake()
            super().setup()

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.path.rsplit("/", 1)[-1], b"filename=" in body))
            res = {"ok": True, "result": {"message_id": 5, "date": 0, "chat": {"id": -1, "type": "group"},
                                          "photo": [{"file_id": "NEW", "file_unique_id": "u", "width": 1, "height": 1}]}}
            out = json.dumps(res).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), TG)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port, got
    srv.shutdown()


@pytest.fixture(scope="module")
def flaky_proxy():
    """HTTP-прокси с CONNECT, который первые N раз отвечает 503 — как прокси PythonAnywhere."""
    state = {"fail": 0}

    def handle(c):
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(4096)
        if state["fail"] > 0:
            state["fail"] -= 1
            c.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            c.close()
            return
        host, port = req.split()[1].decode().split(":")
        u = socket.create_connection((host, int(port)))
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        while True:
            r, _, _ = select.select([c, u], [], [], 5)
            if not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    c.close()
                    u.close()
                    return
                (u if s is c else c).sendall(data)
        c.close()
        u.close()

    ps = socket.socket()
    ps.bind(("127.0.0.1", 0))
    ps.listen(20)

    def accept():
        while True:
            try:
                c, _ = ps.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(c,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{ps.getsockname()[1]}", state
    ps.close()


@pytest.fixture
def bot(fake_telegram, flaky_proxy, monkeypatch):
    monkeypatch.delenv("no_proxy", raising=False)   # иначе urllib идёт к 127.0.0.1 мимо прокси
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(hs, "CONNECT_DELAYS", (0.01, 0.01, 0.01))
    port, _ = fake_telegram
    proxy, _ = flaky_proxy
    s = hs.UrllibProxySession(proxy, api=TelegramAPIServer.from_base(f"https://127.0.0.1:{port}"))
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    s._opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}),
                                            urllib.request.HTTPSHandler(context=insecure))
    return Bot("1:x", session=s)


def test_photo_upload_survives_two_refusals(bot, fake_telegram, flaky_proxy):
    _, got = fake_telegram
    _, state = flaky_proxy
    got.clear()
    state["fail"] = 2
    m = asyncio.run(bot.send_photo(-1, BufferedInputFile(b"\xff\xd8" * 20000, "p.jpg"), caption="тест"))
    assert m.photo[-1].file_id == "NEW"
    assert got == [("sendPhoto", True)]        # дошло ровно один раз, как multipart


def test_permanent_refusal_gives_friendly_error(bot, flaky_proxy):
    _, state = flaky_proxy
    state["fail"] = 100
    with pytest.raises(TelegramNetworkError) as e:
        asyncio.run(bot.send_message(-1, "x"))
    state["fail"] = 0
    assert "нет связи с Telegram" in str(e.value)
    assert "нажмите «Сохранить» ещё раз" in friendly_error(e.value)
