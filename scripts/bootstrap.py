"""Prepare a checkout using only the Python standard library; never install globally."""

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_DIR = ROOT / ".venv"
ENV_PYTHON = ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = ENV_DIR / ".claim-sync-setup.json"


class SetupError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def locked_packages(path: Path, platform: str) -> dict[str, str]:
    """The runtime lock intentionally permits exact pins and a simple platform marker only."""
    result = {}
    pattern = r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?:\s*;\s*sys_platform\s*==\s*[\'"]([^\'"]+)[\'"])?'
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(pattern, line)
        if not match:
            raise SetupError(f"Unsupported requirements.lock entry: {line}")
        name, version, required_platform = match.groups()
        if required_platform is None or required_platform == platform:
            result[name] = version
    return result


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def inspect_environment() -> dict:
    missing = [
        f"{name}=={version}"
        for name, version in locked_packages(ROOT / "requirements.lock", sys.platform).items()
        if installed_version(name) != version
    ]
    setuptools = installed_version("setuptools")
    return {
        "missing": missing,
        "project_version": installed_version("claim-sync"),
        "build_tools_missing": not setuptools
        or int(setuptools.split(".", 1)[0]) < 77
        or not installed_version("wheel"),
        "prefix": str(Path(sys.prefix).resolve()),
    }


@contextmanager
def setup_lock():
    # A stdlib OS lock also works before third-party filelock has been installed.
    with (ROOT / ".claim-sync-setup.lock").open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 300
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise SetupError(
                        "Another setup is still running. Wait for it to finish and try again."
                    ) from None
                time.sleep(0.5)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute(arguments: list[str], *, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def ensure_venv():
    if not ENV_PYTHON.exists():
        print("[setup] Creating .venv...", flush=True)
        result = execute([sys.executable, "-m", "venv", str(ENV_DIR)], capture=True)
        if result.returncode:
            output = result.stdout + result.stderr
            print(output, file=sys.stderr)
            needs_venv = "ensurepip" in output or "No module named venv" in output
            raise SetupError(
                "Could not create .venv. Check Python venv support and folder permissions.",
                20 if needs_venv else 1,
            )
    if execute(
        [str(ENV_PYTHON), "-c", "import sys; sys.exit(sys.version_info < (3,11))"], capture=True
    ).returncode:
        raise SetupError(
            "The existing .venv is unusable or too old. Rename it, then rerun with Python 3.11+."
        )
    if execute([str(ENV_PYTHON), "-m", "pip", "--version"], capture=True).returncode:
        print("[setup] Restoring pip in .venv...", flush=True)
        if execute([str(ENV_PYTHON), "-m", "ensurepip", "--upgrade"]).returncode:
            raise SetupError("pip is unavailable. Install the matching Python venv package and retry.", 20)


def probe() -> dict:
    result = execute([str(ENV_PYTHON), str(Path(__file__).resolve()), "--inspect-environment"], capture=True)
    if result.returncode:
        raise SetupError("Could not inspect .venv. Check Python version and requirements.lock.")
    info = json.loads(result.stdout)
    if Path(info["prefix"]) != ENV_DIR.resolve():
        raise SetupError(
            "The selected interpreter is not using the project .venv; refusing a global install."
        )
    return info


def pip_install(*arguments):
    command = [str(ENV_PYTHON), "-m", "pip", "--disable-pip-version-check", "install"]
    if wheelhouse := os.environ.get("CLAIM_SYNC_WHEELHOUSE"):
        folder = Path(wheelhouse).expanduser().resolve()
        if not folder.is_dir():
            raise SetupError("CLAIM_SYNC_WHEELHOUSE must point to an existing wheel directory.")
        command += ["--no-index", "--find-links", str(folder)]
    result = execute([*command, *arguments])
    if result.returncode:
        raise SetupError(
            "Package installation failed. Check network/proxy access or the offline wheelhouse; then rerun."
        )


def manifest_digest() -> str:
    digest = hashlib.sha256(str(ROOT).encode())
    for name in ("pyproject.toml", "requirements.lock"):
        digest.update((ROOT / name).read_bytes())
    digest.update(f"{sys.platform}:{sys.version_info[:2]}".encode())
    return digest.hexdigest()


def create_default_env():
    # Exclusive creation preserves the user's existing settings, including on repeated launches.
    try:
        descriptor = os.open(ROOT / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "wb") as destination:
        destination.write((ROOT / ".env.example").read_bytes())
    print("[setup] Created .env from .env.example (mock mode).", flush=True)


def prepare():
    with setup_lock():
        ensure_venv()
        info = probe()
        fingerprint = manifest_digest()
        try:
            previous = json.loads(STAMP.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        install_project = (
            previous.get("digest") != fingerprint or info["project_version"] != project["version"]
        )
        if info["missing"]:
            print(
                f"[setup] Installing {len(info['missing'])} missing or mismatched runtime packages...",
                flush=True,
            )
            pip_install("-r", str(ROOT / "requirements.lock"))
        if install_project:
            if info["build_tools_missing"]:
                print("[setup] Installing Python build tools...", flush=True)
                pip_install("setuptools>=77", "wheel")
            print("[setup] Installing the project in .venv...", flush=True)
            # Avoid build isolation so an offline wheelhouse can supply build tools too.
            pip_install("--no-deps", "--no-build-isolation", "--editable", str(ROOT))
        if probe()["missing"]:
            raise SetupError(
                "Installed versions do not match requirements.lock. Review the installation output."
            )
        if execute([str(ENV_PYTHON), "-m", "pip", "--disable-pip-version-check", "check"]).returncode:
            raise SetupError(
                "Dependency conflicts remain in .venv. Fix the reported conflicts before launching."
            )
        create_default_env()
        temporary = STAMP.with_suffix(".tmp")
        temporary.write_text(json.dumps({"digest": fingerprint}), encoding="utf-8")
        temporary.replace(STAMP)
        print("[setup] Ready. Existing .env settings were preserved.", flush=True)


def main() -> int:
    try:
        if sys.argv[1:] == ["--inspect-environment"]:
            print(json.dumps(inspect_environment()))
        else:
            prepare()
        return 0
    except SetupError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(
            f"[error] Setup could not finish ({type(exc).__name__}). Check folder permissions and configuration.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\n[setup] Interrupted. Rerun the launcher to finish installation.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
