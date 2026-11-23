"""Автообновление сервера из GitHub (без ручного `git pull`).

GitHub Actions после успешных тестов вызывает POST /__deploy с секретом DEPLOY_SECRET.
Сервер подтягивает код (`git pull --ff-only`), при изменении requirements.txt ставит
библиотеки, а затем GitHub нажимает «Reload» через API PythonAnywhere и проверяет /version.

Безопасность: без верного секрета — 403; при пустом DEPLOY_SECRET адрес выключен (404).
Тянется только тот же репозиторий и ветка, что уже на сервере; .env и data/ не трогаются.
"""
import hashlib
import os
import subprocess
import sys

TIMEOUT = 240


def _git(root: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout.strip()


def _hash(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""


def venv_python() -> str:
    """Python из venv. В веб-процессе PythonAnywhere sys.executable — это uWSGI, не python."""
    for name in ("python3", "python"):
        p = os.path.join(sys.prefix, "bin", name)
        if os.path.exists(p):
            return p
    return sys.executable


def current_version(root: str) -> str:
    try:
        return _git(root, "rev-parse", "HEAD")
    except Exception:
        return "unknown"


def update_code(root: str, python: str | None = None) -> dict:
    """git pull + pip install (если поменялись зависимости). Возвращает отчёт."""
    req = os.path.join(root, "requirements.txt")
    before_req = _hash(req)
    before = current_version(root)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise RuntimeError("На сервере не выбрана ветка (detached HEAD) — обновите вручную один раз")
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status:
        raise RuntimeError("На сервере изменены файлы проекта вручную — обновление остановлено, "
                           f"чтобы их не потерять:\n{status[:500]}")
    _git(root, "pull", "--ff-only", "origin", branch)
    after = current_version(root)
    report = {"branch": branch, "before": before, "after": after, "updated": before != after,
              "requirements_installed": False}
    if _hash(req) != before_req:
        r = subprocess.run([python or venv_python(), "-m", "pip", "install", "-q", "-r", req],
                           capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"pip install: {(r.stderr or r.stdout).strip()[-500:]}")
        report["requirements_installed"] = True
    return report
