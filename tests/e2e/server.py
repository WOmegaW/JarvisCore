"""Сервер для браузерных тестов: настоящее приложение + поддельный Telegram (из conftest).

Запуск: python tests/e2e/server.py <порт>
Служебные адреса (только для тестов):
  GET  /__test/init?user=7      — подписанный initData
  POST /__test/fail/<Метод>      — следующий вызов этого метода Bot API «упадёт»
  POST /__test/reset             — пустая база
  GET  /__test/calls             — какие методы Bot API вызывались
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import conftest  # noqa: E402  (окружение, поддельный Telegram)
from flask import jsonify, request  # noqa: E402

app = conftest.flask_app.app
FAKE = conftest.FAKE


@app.get("/__test/init")
def _init():
    return conftest.make_init_data(user_id=int(request.args.get("user", conftest.USER_ID)))


@app.post("/__test/fail/<method>")
def _fail(method):
    FAKE.fail[method] = FAKE.fail.get(method, 0) + 1
    return "ok"


@app.post("/__test/reset")
def _reset():
    con = sqlite3.connect(os.environ["DB_PATH"])
    for t in ("products", "events", "fsm", "settings", "sqlite_sequence"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
    import shutil
    shutil.rmtree(os.path.join(os.path.dirname(os.environ["DB_PATH"]), "photos"), ignore_errors=True)
    FAKE.reset()
    return "ok"


@app.get("/__test/calls")
def _calls():
    return jsonify([[n, getattr(m, "text", None) or getattr(m, "caption", None)] for n, m in FAKE.calls])


if __name__ == "__main__":
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(port=int(sys.argv[1]), threaded=True)
