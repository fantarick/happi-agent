from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
from types import TracebackType
from typing import IO


class SecurityError(RuntimeError):
    """A fail-closed security invariant was not met."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def ensure_separate_worktree_root(
    canonical_repo: Path, common_git_dir: Path, worktree_root: Path
) -> None:
    canonical = canonical_repo.resolve()
    common = common_git_dir.resolve()
    root = worktree_root.resolve()
    if root == canonical or is_relative_to(root, canonical):
        raise SecurityError("worktree_root must be outside the canonical repository")
    if root == common or is_relative_to(root, common):
        raise SecurityError("worktree_root must be outside the shared Git directory")
    if is_relative_to(canonical, root) or is_relative_to(common, root):
        raise SecurityError("worktree_root must not contain canonical Git data")


def kill_switch_active(path: Path) -> bool:
    return path.exists()


def codex_process_environment() -> dict[str, str]:
    """Return the minimum host environment needed by the Codex client.

    Model-generated commands receive a separate, empty baseline through Codex's
    shell_environment_policy. This environment is only for the Codex client.
    """

    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


class GlobalRunLock:
    """Non-blocking, process-safe exclusive lock based on flock(2)."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> GlobalRunLock:
        if not self.acquire():
            raise BlockingIOError(f"lock busy: {self.path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
