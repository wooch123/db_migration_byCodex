import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
module_spec = importlib.util.spec_from_file_location("bootstrap", REPO / "scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(bootstrap)


@pytest.fixture
def setup_project(tmp_path, monkeypatch):
    env_dir = tmp_path / ".venv"
    env_dir.mkdir()
    (tmp_path / "requirements.lock").write_text("sample==1.0.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="claim-sync"\nversion="1.0.0"\n', encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text("APP_MODE=mock\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "ENV_DIR", env_dir)
    monkeypatch.setattr(bootstrap, "STAMP", env_dir / ".claim-sync-setup.json")
    monkeypatch.setattr(bootstrap, "ensure_venv", lambda: None)
    monkeypatch.setattr(bootstrap, "execute", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        bootstrap, "probe", lambda: {"missing": [], "project_version": "1.0.0", "build_tools_missing": False}
    )
    installed = []
    monkeypatch.setattr(bootstrap, "pip_install", lambda *args: installed.append(args))
    return tmp_path, installed


def test_runtime_lock_platform_markers(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text('# comment\nhttpx==0.28.1\ncolorama==0.4.6; sys_platform == "win32"\n', encoding="utf-8")
    assert bootstrap.locked_packages(lock, "linux") == {"httpx": "0.28.1"}
    assert bootstrap.locked_packages(lock, "win32") == {"httpx": "0.28.1", "colorama": "0.4.6"}


def test_unknown_lock_syntax_fails_instead_of_silently_skipping(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("httpx>=0.28\n", encoding="utf-8")
    with pytest.raises(bootstrap.SetupError, match="Unsupported"):
        bootstrap.locked_packages(lock, sys.platform)


def test_first_setup_creates_env_and_second_run_does_not_install(setup_project):
    root, installed = setup_project
    bootstrap.prepare()
    assert len(installed) == 1 and "--editable" in installed[0]
    assert (root / ".env").read_text(encoding="utf-8") == "APP_MODE=mock\n"
    (root / ".env").write_text("APP_MODE=live\nCUSTOM_VALUE=keep me\n", encoding="utf-8")
    installed.clear()
    bootstrap.prepare()
    assert installed == []
    assert (root / ".env").read_text(encoding="utf-8") == "APP_MODE=live\nCUSTOM_VALUE=keep me\n"


def test_missing_packages_are_repaired_even_with_existing_stamp(setup_project, monkeypatch):
    root, installed = setup_project
    bootstrap.prepare()
    installed.clear()
    answers = iter(
        [
            {"missing": ["sample==1.0.0"], "project_version": "1.0.0", "build_tools_missing": False},
            {"missing": []},
        ]
    )
    monkeypatch.setattr(bootstrap, "probe", lambda: next(answers))
    bootstrap.prepare()
    assert installed == [("-r", str(root / "requirements.lock"))]


def test_manifest_change_refreshes_project_install(setup_project):
    root, installed = setup_project
    bootstrap.prepare()
    installed.clear()
    with (root / "pyproject.toml").open("a", encoding="utf-8") as config:
        config.write('description="updated metadata"\n')
    bootstrap.prepare()
    assert len(installed) == 1 and "--editable" in installed[0]


def test_install_failure_does_not_mark_ready_or_launch(setup_project, monkeypatch):
    root, _ = setup_project

    def fail(*args):
        raise bootstrap.SetupError("Network unavailable")

    monkeypatch.setattr(bootstrap, "pip_install", fail)
    with pytest.raises(bootstrap.SetupError, match="Network"):
        bootstrap.prepare()
    assert not bootstrap.STAMP.exists()
    assert not (root / ".env").exists()


def test_offline_install_uses_only_wheelhouse(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setenv("CLAIM_SYNC_WHEELHOUSE", str(tmp_path))
    monkeypatch.setattr(
        bootstrap, "execute", lambda args: recorded.append(args) or SimpleNamespace(returncode=0)
    )
    bootstrap.pip_install("httpx==0.28.1")
    assert "--no-index" in recorded[0]
    assert recorded[0][recorded[0].index("--find-links") + 1] == str(tmp_path.resolve())


def test_refuses_non_virtual_interpreter(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "execute",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({"prefix": "/unrelated-system-python"})
        ),
    )
    with pytest.raises(bootstrap.SetupError, match="global install"):
        bootstrap.probe()


@pytest.fixture
def launcher_checkout(tmp_path):
    # Exercise shell quoting and invocation from a different working directory without installing packages.
    root = tmp_path / "checkout with spaces & symbols!"
    root.mkdir()
    for file in ("run.bat", "run.sh"):
        shutil.copy2(REPO / file, root / file)
    (root / "scripts").mkdir()
    (root / "scripts/bootstrap.py").write_text(
        'import os,sys\nfrom pathlib import Path\nPath("setup-called").touch()\nsys.exit(int(os.environ.get("TEST_SETUP_EXIT", "0")))\n',
        encoding="utf-8",
    )
    (root / "claim_sync").mkdir()
    (root / "claim_sync/__init__.py").write_text("", encoding="utf-8")
    (root / "claim_sync/cli.py").write_text(
        'import json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7 if "--fail" in sys.argv else 0)\n',
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")], check=True, capture_output=True
    )
    return root


def run_launcher(root, args=(), **variables):
    env = {**os.environ, "CLAIM_SYNC_NO_SYSTEM_INSTALL": "1", "CLAIM_SYNC_NO_PAUSE": "1", **variables}
    env.pop("CLAIM_SYNC_PYTHON", None)
    if os.name == "nt":
        command = subprocess.list2cmdline([str(root / "run.bat"), *args])
        # Pass cmd's command tail as a raw Windows command line: list2cmdline on an
        # already quoted /c tail would insert literal backslash-quote sequences.
        invocation = f'cmd.exe /d /s /c "{command}"'
    else:
        invocation = ["bash", str(root / "run.sh"), *args]
    return subprocess.run(
        invocation, cwd=root.parent, env=env, capture_output=True, text=True, timeout=30, check=False
    )


def test_launcher_defaults_to_web_and_runs_setup(launcher_checkout):
    result = run_launcher(launcher_checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == ["web"]
    assert (launcher_checkout / "setup-called").exists()


def test_launcher_forwards_quoted_arguments_and_exit_code(launcher_checkout):
    args = ["--env-file", "settings with spaces & symbols!.env", "run", "--spec", "a b.json", "--fail"]
    result = run_launcher(launcher_checkout, args)
    assert result.returncode == 7, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == args


def test_setup_only_does_not_start_application(launcher_checkout):
    result = run_launcher(launcher_checkout, ["--setup-only"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "web" not in result.stdout
    assert (launcher_checkout / "setup-called").exists()


def test_setup_failure_does_not_start_application(launcher_checkout):
    result = run_launcher(launcher_checkout, ["worker"], TEST_SETUP_EXIT="1")
    assert result.returncode != 0
    assert (launcher_checkout / "setup-called").exists()
    assert '["worker"]' not in result.stdout
