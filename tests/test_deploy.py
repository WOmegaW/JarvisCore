"""Автообновление сервера из GitHub: git pull, pip install при новых зависимостях, защита секретом."""
import os
import stat
import subprocess

import pytest

import flask_app
from bot.deploy import update_code


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repos(tmp_path):
    """origin (как GitHub), server (как ~/JarvisCore на PythonAnywhere), dev (откуда приходят изменения)."""
    origin, server, dev = tmp_path / "origin.git", tmp_path / "server", tmp_path / "dev"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(dev)], check=True, capture_output=True)
    (dev / "requirements.txt").write_text("flask\n")
    (dev / "app.py").write_text("v1\n")
    git(dev, "add", "-A")
    git(dev, "commit", "-m", "v1")
    git(dev, "push", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-b", "main", str(origin), str(server)], check=True, capture_output=True)
    (server / ".env").write_text("BOT_TOKEN=secret\n")          # не в git — не должно пострадать
    return origin, server, dev


def push(dev, name, text):
    (dev / name).write_text(text)
    git(dev, "add", "-A")
    git(dev, "commit", "-m", name)
    git(dev, "push", "origin", "HEAD:main")


def fake_python(tmp_path):
    log = tmp_path / "pip.log"
    script = tmp_path / "python"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def test_pull_new_code(repos, tmp_path):
    _, server, dev = repos
    push(dev, "app.py", "v2\n")
    py, log = fake_python(tmp_path)
    r = update_code(str(server), python=py)
    assert r["updated"] and not r["requirements_installed"]
    assert (server / "app.py").read_text() == "v2\n"
    assert (server / ".env").read_text() == "BOT_TOKEN=secret\n"
    assert not log.exists()


def test_new_requirements_are_installed(repos, tmp_path):
    _, server, dev = repos
    push(dev, "requirements.txt", "flask\nPillow\n")
    py, log = fake_python(tmp_path)
    r = update_code(str(server), python=py)
    assert r["requirements_installed"]
    assert "pip install -q -r" in log.read_text()


def test_nothing_new(repos, tmp_path):
    _, server, _ = repos
    r = update_code(str(server), python=fake_python(tmp_path)[0])
    assert not r["updated"] and r["before"] == r["after"]


def test_local_edits_on_server_are_not_overwritten(repos, tmp_path):
    _, server, dev = repos
    (server / "app.py").write_text("правка руками на сервере\n")
    push(dev, "app.py", "v2\n")
    with pytest.raises(RuntimeError, match="изменены файлы проекта вручную"):
        update_code(str(server), python=fake_python(tmp_path)[0])
    assert (server / "app.py").read_text() == "правка руками на сервере\n"


# ---------- адрес /__deploy ----------

def test_deploy_endpoint_is_off_without_secret(client, monkeypatch):
    monkeypatch.delenv("DEPLOY_SECRET", raising=False)
    assert client.post("/__deploy").status_code == 404


def test_deploy_endpoint_requires_secret(client, monkeypatch):
    monkeypatch.setenv("DEPLOY_SECRET", "s3cret-value-123456")
    calls = []
    monkeypatch.setattr(flask_app, "update_code", lambda root: calls.append(root) or {"updated": True})
    assert client.post("/__deploy", headers={"X-Deploy-Secret": "wrong"}).status_code == 403
    r = client.post("/__deploy", headers={"X-Deploy-Secret": "s3cret-value-123456"})
    assert r.status_code == 200 and r.get_json()["ok"] and calls == [flask_app.ROOT]


def test_deploy_endpoint_reports_failure(client, monkeypatch):
    monkeypatch.setenv("DEPLOY_SECRET", "s3cret-value-123456")

    def boom(root):
        raise RuntimeError("git pull: нет сети")
    monkeypatch.setattr(flask_app, "update_code", boom)
    r = client.post("/__deploy", headers={"X-Deploy-Secret": "s3cret-value-123456"})
    assert r.status_code == 500 and "нет сети" in r.get_json()["error"]


def test_version(client):
    assert len(client.get("/version").get_json()["version"]) >= 7
