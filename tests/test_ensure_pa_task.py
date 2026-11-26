"""Логика настройки задания PythonAnywhere (без сети)."""
import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "ensure_pa_task", os.path.join(os.path.dirname(__file__), "..", "scripts", "ensure_pa_task.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_creates_when_missing():
    assert m.plan([{"id": 1, "command": "echo hi"}])[0] == "create"


def test_fixes_wrong_time():
    action, tid, diff = m.plan([{**m.WANT, "id": 5, "hour": 6, "minute": 0}])
    assert (action, tid, diff) == ("update", 5, {"hour": 7})


def test_ok_when_right():
    assert m.plan([{**m.WANT, "id": 5}]) == ("ok", 5, {})
