"""Сессия для работы через HTTP-прокси PythonAnywhere.

aiohttp на бесплатном PythonAnywhere получает от прокси ошибку 503, хотя curl через тот же
прокси работает. Поэтому запросы к Telegram здесь отправляются стандартным urllib
(в отдельном потоке, чтобы не блокировать бота)."""
import asyncio
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, AsyncGenerator, cast

import certifi
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType


log = logging.getLogger(__name__)

# Прокси PythonAnywhere иногда на мгновение отвечает «503» на попытку соединиться.
# В этот момент запрос до Telegram ещё не ушёл, так что его можно смело повторить.
CONNECT_RETRIES = 4
CONNECT_DELAYS = (0.7, 1.5, 3.0)


def _is_connect_failure(e: BaseException) -> bool:
    reason = getattr(e, "reason", e)
    if isinstance(reason, (ConnectionRefusedError,)):
        return True
    text = str(reason)
    return "Tunnel connection failed" in text or "Temporary failure in name resolution" in text


def _with_retries(fn):
    for attempt in range(CONNECT_RETRIES):
        try:
            return fn()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as e:
            if attempt == CONNECT_RETRIES - 1 or not _is_connect_failure(e):
                raise
            log.warning("Прокси не дал соединиться (%s), повтор %s", e, attempt + 1)
            time.sleep(CONNECT_DELAYS[min(attempt, len(CONNECT_DELAYS) - 1)])


class UrllibProxySession(BaseSession):
    def __init__(self, proxy_url: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())),
        )

    def _post(self, url: str, body: bytes, timeout: float,
              content_type: str = "application/x-www-form-urlencoded") -> tuple[int, str]:
        def once() -> tuple[int, str]:
            # новый Request на каждую попытку: urllib меняет объект запроса при работе через прокси
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": content_type})
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8")

        try:
            return _with_retries(once)
        except urllib.error.HTTPError as e:  # Telegram отвечает 400/403 с JSON-описанием ошибки
            return e.code, e.read().decode("utf-8")

    async def make_request(self, bot: Bot, method: TelegramMethod[TelegramType],
                           timeout: int | None = None) -> TelegramType:
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        files: dict[str, Any] = {}
        fields: dict[str, str] = {}
        for key, value in method.model_dump(warnings=False).items():
            value = self.prepare_value(value, bot=bot, files=files)
            if not value:
                continue
            fields[key] = value if isinstance(value, str) else json.dumps(value)
        if files:
            body, content_type = await self._multipart(bot, fields, files)
        else:
            body, content_type = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
        try:
            status, raw = await asyncio.to_thread(
                self._post, url, body, float(self.timeout if timeout is None else timeout), content_type
            )
        except Exception as e:
            # не выводим url — в нём токен
            msg = ("нет связи с Telegram (сервер на минуту перегружен)" if _is_connect_failure(e)
                   else f"{type(e).__name__}: {e}")
            raise TelegramNetworkError(method=method, message=msg) from None
        response = self.check_response(bot=bot, method=method, status_code=status, content=raw)
        return cast(TelegramType, response.result)

    @staticmethod
    async def _multipart(bot: Bot, fields: dict[str, str], files: dict[str, Any]) -> tuple[bytes, str]:
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                + value.encode() + b"\r\n"
            )
        for key, file in files.items():
            data = b"".join([chunk async for chunk in file.read(bot)])
            filename = (file.filename or key).replace('"', "")
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None,
                             timeout: int = 30, chunk_size: int = 65536,
                             raise_for_status: bool = True) -> AsyncGenerator[bytes, None]:
        def fetch() -> bytes:
            req = urllib.request.Request(url, headers=headers or {})
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.read()

        data = await asyncio.to_thread(_with_retries, fetch)
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    async def close(self) -> None:
        pass
