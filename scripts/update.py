"""Download origin/main and replace tracked code. Never create or upload commits."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "main"
# The command gateway deliberately has no commit, push, merge, clean or hook execution.
GIT_COMMANDS = {"rev-parse", "symbolic-ref", "ls-files", "ls-tree", "fetch", "reset"}


class UpdateError(Exception):
    pass


class Repository:
    def __init__(self, root: Path, hooks: Path):
        self.root = root.resolve()
        self.hooks = hooks
        self.environment = os.environ.copy()
        # Keep network/auth/proxy settings, but never let a shell redirect this operation to another repo.
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            self.environment.pop(name, None)

    def git(self, command, *arguments):
        if command not in GIT_COMMANDS:
            raise UpdateError(f"This updater does not permit git {command}.")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                f"core.hooksPath={self.hooks}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "gc.auto=0",
                "-c",
                "maintenance.auto=false",
                "-c",
                "submodule.recurse=false",
                command,
                *arguments,
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            detail = re.sub(r"(?:https?|ssh|file)://\S+", "[remote]", result.stderr.strip())
            raise UpdateError(f"git {command} failed (exit {result.returncode}). {detail}")
        return result.stdout.rstrip("\r\n")

    def paths(self, command, *arguments):
        return [path for path in self.git(command, *arguments).split("\0") if path]


@contextmanager
def update_lock(root: Path):
    # Shared with run.bat/run.sh setup; no third-party packages are needed.
    with (root / ".claim-sync-setup.lock").open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError(
                "Another update or package setup is running. Try again after it finishes."
            ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def protected(path: str, ignored: list[str]) -> bool:
    candidate = path.rstrip("/").casefold()
    top = candidate.split("/", 1)[0]
    if top in {".env", ".venv", "data", "wheelhouse", ".claim-sync-setup.lock"}:
        return True
    if top.startswith(".env.") and top != ".env.example":
        return True
    return any(
        candidate == item.rstrip("/").casefold()
        or candidate.startswith(item.rstrip("/").casefold() + "/")
        or item.rstrip("/").casefold().startswith(candidate + "/")
        for item in ignored
    )


def update(root: Path = ROOT) -> str:
    root = root.resolve()
    if not shutil.which("git"):
        raise UpdateError("Git is required. Install Git for Windows or your Linux Git package first.")
    if not (root / ".git").exists():
        raise UpdateError(
            "This folder is not a Git checkout. Use a git clone of the project, not a ZIP download."
        )
    # Git hooks are disabled for every command; a local hook cannot turn this into an upload.
    with TemporaryDirectory(prefix="claim-sync-update-hooks-") as empty_hooks:
        repo = Repository(root, Path(empty_hooks))
        actual_root = Path(repo.git("rev-parse", "--show-toplevel")).resolve()
        if actual_root != root:
            raise UpdateError("Repository root does not match the update script folder; nothing was changed.")
        if repo.git("symbolic-ref", "--quiet", "--short", "HEAD") != BRANCH:
            raise UpdateError("This updater only replaces the main checkout. Switch to main first.")
        with update_lock(root):
            tracked = repo.paths("ls-files", "--cached", "-z")
            tracked += repo.paths("ls-tree", "-r", "--name-only", "-z", "HEAD")
            if any(protected(path, []) for path in tracked):
                raise UpdateError("Local settings/data are tracked by Git. Refusing to overwrite them.")
            ignored = repo.paths(
                "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"
            )
            print(
                "[update] Downloading origin/main. Existing tracked code edits will be replaced.", flush=True
            )
            # An explicit ref avoids pull configuration, local commits and merge commits.
            repo.git("fetch", "--no-tags", "--no-recurse-submodules", "origin", "refs/heads/main")
            target = repo.git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
            incoming = repo.paths("ls-tree", "-r", "--name-only", "-z", target)
            if any(protected(path, ignored) for path in incoming):
                raise UpdateError(
                    "The remote revision would overwrite local settings/data. Update stopped before reset."
                )
            # Both repository root and protected paths have been checked before this destructive operation.
            repo.git("reset", "--hard", target)
            if repo.git("rev-parse", "HEAD") != target:
                raise UpdateError("The checkout could not be verified after updating.")
    print(f"[update] Updated to {target[:12]}. No commits were created or uploaded.", flush=True)
    print(
        "[update] .env, data and installed packages were preserved. Restart with run.bat or ./run.sh.",
        flush=True,
    )
    return target


def main():
    parser = argparse.ArgumentParser(
        description="Download origin/main and overwrite tracked code only; no uploads."
    )
    parser.parse_args()
    try:
        update()
        return 0
    except (UpdateError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[update] Interrupted. Rerun the updater to finish.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
