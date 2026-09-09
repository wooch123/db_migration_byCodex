import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("update_script", REPO / "scripts/update.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def git(folder, *args):
    result = subprocess.run(
        [
            "git",
            "-C",
            str(folder),
            "-c",
            "user.name=Updater Test",
            "-c",
            "user.email=updater@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def checkouts(tmp_path, monkeypatch):
    # All test commits exist only in temporary local repos. No test uploads to any remote.
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "local origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "app.txt").write_text("version 1")
    (origin / "obsolete.txt").write_text("removed in next release")
    (origin / ".env.example").write_text("APP_MODE=live\n")
    (origin / ".gitignore").write_text(
        ".env\n.env.*\n!.env.example\n.venv/\ndata/\nwheelhouse/\n.claim-sync-setup.lock\n"
    )
    (origin / "scripts").mkdir()
    shutil.copy2(REPO / "scripts/update.py", origin / "scripts/update.py")
    for name in ("update.bat", "update.sh"):
        shutil.copy2(REPO / name, origin / name)
    git(origin, "add", ".")
    git(origin, "commit", "-m", "fixture baseline")
    checkout = tmp_path / "checkout with spaces & symbols!"
    git(tmp_path, "clone", str(origin), str(checkout))
    return origin, checkout


def next_version(origin):
    (origin / "app.txt").write_text("version 2")
    (origin / "obsolete.txt").unlink()
    git(origin, "add", "-A")
    git(origin, "commit", "-m", "fixture next version")
    return git(origin, "rev-parse", "HEAD")


def test_update_replaces_local_code_without_upload_or_data_loss(checkouts, monkeypatch, tmp_path):
    origin, checkout = checkouts
    (checkout / "app.txt").write_text("local commit")
    git(checkout, "add", "app.txt")
    git(checkout, "commit", "-m", "fixture local-only change")
    (checkout / "app.txt").write_text("staged edits")
    git(checkout, "add", "app.txt")
    (checkout / "app.txt").write_text("unstaged edits")
    preserved = {
        ".env": "API_TOKEN=keep-local-only",
        ".env.office": "keep environment",
        "data/store.sqlite": "keep database",
        ".venv/installed.txt": "keep installed packages",
        "wheelhouse/package.whl": "keep offline packages",
        "notes.txt": "keep untracked note",
    }
    for name, value in preserved.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    expected = next_version(origin)
    hook = checkout / ".git/hooks/post-index-change"
    hook.write_text("#!/bin/sh\necho ran > hook-ran.txt\n")
    hook.chmod(0o755)
    trace = tmp_path / "update.trace"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    assert updater.update(checkout) == expected
    monkeypatch.delenv("GIT_TRACE")
    assert git(checkout, "rev-parse", "HEAD") == expected
    assert git(origin, "rev-parse", "HEAD") == expected
    assert (checkout / "app.txt").read_text() == "version 2"
    assert not (checkout / "obsolete.txt").exists()
    assert not (checkout / "hook-ran.txt").exists()
    assert all((checkout / name).read_text() == value for name, value in preserved.items())
    commands = trace.read_text().lower()
    assert " push " not in commands and " commit " not in commands and " clean " not in commands


def test_failed_fetch_keeps_local_edits_even_with_old_fetch_head(checkouts):
    _, checkout = checkouts
    git(checkout, "fetch", "origin", "main")
    git(checkout, "remote", "set-url", "origin", str(checkout / "unavailable.git"))
    old_head = git(checkout, "rev-parse", "HEAD")
    (checkout / "app.txt").write_text("local edits must survive")
    with pytest.raises(updater.UpdateError, match="fetch failed"):
        updater.update(checkout)
    assert git(checkout, "rev-parse", "HEAD") == old_head
    assert (checkout / "app.txt").read_text() == "local edits must survive"


@pytest.mark.parametrize("name", [".env", ".env.office", "data/store.sqlite", ".venv/file.txt"])
def test_remote_cannot_overwrite_protected_files(checkouts, name):
    origin, checkout = checkouts
    old_head = git(checkout, "rev-parse", "HEAD")
    path = origin / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("remote content")
    git(origin, "add", "-f", name)
    git(origin, "commit", "-m", "fixture protected file")
    with pytest.raises(updater.UpdateError, match="settings/data"):
        updater.update(checkout)
    assert git(checkout, "rev-parse", "HEAD") == old_head


def test_ignored_directory_cannot_be_replaced_by_a_tracked_file(checkouts):
    origin, checkout = checkouts
    with (checkout / ".gitignore").open("a") as handle:
        handle.write("private-cache/\n")
    cache = checkout / "private-cache"
    cache.mkdir()
    (cache / "local.db").write_text("keep data")
    (origin / "private-cache").write_text("remote file")
    git(origin, "add", "private-cache")
    git(origin, "commit", "-m", "fixture directory collision")
    with pytest.raises(updater.UpdateError, match="settings/data"):
        updater.update(checkout)
    assert (cache / "local.db").read_text() == "keep data"


def test_refuses_tracked_env_and_non_main_branch(checkouts):
    _, checkout = checkouts
    (checkout / ".env").write_text("local settings")
    git(checkout, "add", "-f", ".env")
    with pytest.raises(updater.UpdateError, match="tracked by Git"):
        updater.update(checkout)
    git(checkout, "switch", "-c", "development")
    with pytest.raises(updater.UpdateError, match="main checkout"):
        updater.update(checkout)


def test_zip_folder_and_concurrent_setup_are_rejected(checkouts, tmp_path):
    _, checkout = checkouts
    with pytest.raises(updater.UpdateError, match="not a Git checkout"):
        updater.update(tmp_path)
    with updater.update_lock(checkout):
        with pytest.raises(updater.UpdateError, match="Another update"):
            updater.update(checkout)


@pytest.mark.parametrize("command", ["commit", "push"])
def test_command_gateway_rejects_upload_commands(tmp_path, command):
    repository = updater.Repository(tmp_path, tmp_path)
    with pytest.raises(updater.UpdateError, match="does not permit"):
        repository.git(command)


def test_launcher_can_replace_itself_from_a_different_working_directory(checkouts, tmp_path):
    origin, checkout = checkouts
    (origin / "update.bat").write_bytes(b"@echo off\r\necho WRONG > unexpected-batch-tail.txt\r\n")
    expected = next_version(origin)
    environment = {**os.environ, "CLAIM_SYNC_PYTHON": sys.executable, "CLAIM_SYNC_NO_PAUSE": "1"}
    if os.name == "nt":
        command = subprocess.list2cmdline([str(checkout / "update.bat")])
        invocation = f'cmd.exe /d /s /c "{command}"'
    else:
        invocation = ["bash", str(checkout / "update.sh")]
    result = subprocess.run(
        invocation, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=45, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected[:12] in result.stdout
    assert not (tmp_path / "unexpected-batch-tail.txt").exists()
    assert git(checkout, "rev-parse", "HEAD") == expected
