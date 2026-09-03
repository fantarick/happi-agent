from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from happi_agent.security import (
    SecurityError,
    ensure_separate_worktree_root,
    is_relative_to,
    sha256_bytes,
)


RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class WorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Workspace:
    path: Path
    base_commit: str
    common_git_dir: Path
    git_file_content: bytes
    git_file_sha256: str


def _git(
    repo: Path, argv: tuple[str, ...], *, timeout: int = 60
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError("GIT_UNAVAILABLE", f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceError("GIT_FAILED", detail or "Git command failed")
    return completed


class WorkspaceManager:
    def __init__(self, canonical_repo: Path, worktree_root: Path):
        self.canonical_repo = canonical_repo.resolve()
        self.worktree_root = worktree_root.resolve()

    def preflight(self) -> tuple[str, Path]:
        if not self.canonical_repo.is_dir():
            raise WorkspaceError(
                "CANONICAL_REPO_MISSING",
                f"canonical repository does not exist: {self.canonical_repo}",
            )
        top = Path(
            _git(self.canonical_repo, ("rev-parse", "--show-toplevel"))
            .stdout.decode()
            .strip()
        ).resolve()
        if top != self.canonical_repo:
            raise WorkspaceError(
                "CANONICAL_REPO_MISMATCH",
                f"configured canonical repository resolves to {top}",
            )
        base_commit = (
            _git(self.canonical_repo, ("rev-parse", "--verify", "HEAD^{commit}"))
            .stdout.decode()
            .strip()
        )
        common_raw = (
            _git(self.canonical_repo, ("rev-parse", "--path-format=absolute", "--git-common-dir"))
            .stdout.decode()
            .strip()
        )
        common_git_dir = Path(common_raw).resolve()
        try:
            ensure_separate_worktree_root(
                self.canonical_repo, common_git_dir, self.worktree_root
            )
        except SecurityError as exc:
            raise WorkspaceError("UNSAFE_WORKTREE_ROOT", str(exc)) from exc
        return base_commit, common_git_dir

    def create(self, run_id: str) -> Workspace:
        if not RUN_ID_RE.fullmatch(run_id):
            raise WorkspaceError("INVALID_RUN_ID", "unsafe run id for workspace path")
        base_commit, common_git_dir = self.preflight()
        self.worktree_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.worktree_root, 0o700)
        workspace_path = (self.worktree_root / run_id).resolve()
        if workspace_path.parent != self.worktree_root:
            raise WorkspaceError("UNSAFE_WORKSPACE_PATH", "workspace escaped root")
        if workspace_path.exists():
            raise WorkspaceError(
                "WORKSPACE_EXISTS", f"workspace already exists: {workspace_path}"
            )
        _git(
            self.canonical_repo,
            ("worktree", "add", "--detach", str(workspace_path), base_commit),
        )
        os.chmod(workspace_path, 0o700)
        git_file = workspace_path / ".git"
        try:
            content = git_file.read_bytes()
        except OSError as exc:
            self._cleanup_path(workspace_path)
            raise WorkspaceError(
                "INVALID_WORKTREE", "worktree .git marker is unavailable"
            ) from exc
        if not content.startswith(b"gitdir: "):
            self._cleanup_path(workspace_path)
            raise WorkspaceError(
                "INVALID_WORKTREE", "worktree .git marker has unexpected format"
            )
        referenced = Path(content[8:].decode("utf-8").strip()).resolve()
        if not is_relative_to(referenced, common_git_dir):
            self._cleanup_path(workspace_path)
            raise WorkspaceError(
                "UNSAFE_GITDIR", "worktree does not point into the shared Git directory"
            )
        os.chmod(git_file, 0o444)
        return Workspace(
            path=workspace_path,
            base_commit=base_commit,
            common_git_dir=common_git_dir,
            git_file_content=content,
            git_file_sha256=sha256_bytes(content),
        )

    def cleanup(self, workspace: Workspace) -> None:
        self._validate_cleanup_target(workspace.path)
        error: WorkspaceError | None = None
        try:
            _git(
                self.canonical_repo,
                ("worktree", "remove", "--force", str(workspace.path)),
            )
        except WorkspaceError as exc:
            error = exc
            self._cleanup_path(workspace.path)
        try:
            _git(self.canonical_repo, ("worktree", "prune"))
        except WorkspaceError as exc:
            if error is None:
                error = exc
        if workspace.path.exists():
            raise WorkspaceError(
                "WORKSPACE_CLEANUP_FAILED", f"workspace remains: {workspace.path}"
            )
        if error is not None and error.code != "GIT_FAILED":
            raise error

    def _validate_cleanup_target(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.worktree_root or not RUN_ID_RE.fullmatch(resolved.name):
            raise WorkspaceError("UNSAFE_CLEANUP_TARGET", f"refusing cleanup: {path}")

    def _cleanup_path(self, path: Path) -> None:
        self._validate_cleanup_target(path)
        if path.exists():
            shutil.rmtree(path)
