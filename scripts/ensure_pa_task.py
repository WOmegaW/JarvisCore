"""Создаёт или исправляет ежедневное задание PythonAnywhere для утреннего плана
(по просьбе владельца; запускается вручную из GitHub Actions → «Setup PythonAnywhere task»).

Задание: каждый день в 07:00 UTC — `python -m bot.daily`. Бот сам дожидается 9:00 по Мадриду
(зимой 07:00 UTC = 8:00), так что план приходит в 9:00 и зимой, и летом.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

USER = os.getenv("PA_USER", "Carbonara")
HOST = os.getenv("PA_HOST", "www.pythonanywhere.com")
COMMAND = f"cd /home/{USER}/JarvisCore && /home/{USER}/venv/bin/python -m bot.daily"
WANT = {"command": COMMAND, "enabled": True, "interval": "daily", "hour": 7, "minute": 0}


def api(method: str, path: str = "", data: dict | None = None):
    url = f"https://{HOST}/api/v0/user/{USER}/schedule/{path}"
    body = None
    if data is not None:
        body = urllib.parse.urlencode(
            {k: ("true" if v is True else "false" if v is False else v) for k, v in data.items()}).encode()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Token {os.environ['PA_API_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "null")


def plan(tasks: list[dict]) -> tuple[str, int | None, dict]:
    """Что сделать: ('ok'|'create'|'update', id задания, какие поля поменять)."""
    ours = [t for t in tasks if "bot.daily" in (t.get("command") or "")]
    if not ours:
        return "create", None, dict(WANT)
    t = ours[0]
    diff = {k: v for k, v in WANT.items() if t.get(k) != v}
    return ("update", t["id"], diff) if diff else ("ok", t["id"], {})


def main() -> int:
    try:
        tasks = api("GET")
        print("Задания сейчас:", json.dumps(tasks, ensure_ascii=False))
        action, tid, fields = plan(tasks)
        if action == "create":
            print("✅ Создано:", json.dumps(api("POST", "", fields), ensure_ascii=False))
        elif action == "update":
            print("✅ Исправлено:", json.dumps(api("PATCH", f"{tid}/", fields), ensure_ascii=False))
        else:
            print("✅ Задание уже настроено правильно")
        return 0
    except urllib.error.HTTPError as e:
        print(f"::error::PythonAnywhere API: HTTP {e.code} {e.read().decode()[:500]}")
    except Exception as e:
        print(f"::error::Не удалось настроить задание: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
