from __future__ import annotations

import fnmatch
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from happi_agent.models import ValidationCheck, ValidationPolicy, ValidationResult
from happi_agent.security import sha256_bytes
from happi_agent.workspace import Workspace


@dataclass(frozen=True)
class _ChangedPath:
    path: str
    status: str


class Validator:
    def validate(self, workspace: Workspace, policy: ValidationPolicy) -> ValidationResult:
        checks: list[ValidationCheck] = []
        metadata_ok = self._metadata_intact(workspace)
        checks.append(
            ValidationCheck(
                "git_metadata",
                metadata_ok,
                "OK" if metadata_ok else "GIT_METADATA_CHANGED",
                "worktree Git marker is intact"
                if metadata_ok
                else "worktree Git marker was changed or removed",
            )
        )

        head_ok, head_detail = self._head_is_base(workspace)
        checks.append(
            ValidationCheck(
                "base_commit",
                head_ok,
                "OK" if head_ok else "HEAD_CHANGED",
                head_detail,
            )
        )
        changed, status_error = self._changed_paths(workspace.path)
        if status_error:
            checks.append(
                ValidationCheck(
                    "git_status",
                    False,
                    "GIT_STATUS_FAILED",
                    status_error,
                )
            )
            return ValidationResult(False, tuple(checks), (), 0, b"")
        changed_files = tuple(sorted(item.path for item in changed))
        checks.append(
            ValidationCheck(
                "max_files",
                len(changed_files) <= policy.max_files,
                "OK" if len(changed_files) <= policy.max_files else "MAX_FILES_EXCEEDED",
                f"{len(changed_files)} changed file(s), limit {policy.max_files}",
                {"actual": len(changed_files), "limit": policy.max_files},
            )
        )

        forbidden = tuple(
            path
            for path in changed_files
            if any(self._matches(path, pattern) for pattern in policy.forbidden_paths)
        )
        checks.append(
            ValidationCheck(
                "forbidden_paths",
                not forbidden,
                "OK" if not forbidden else "FORBIDDEN_PATH",
                "no forbidden paths changed"
                if not forbidden
                else "forbidden paths changed",
                {"paths": list(forbidden)},
            )
        )

        outside_allowed = tuple(
            path
            for path in changed_files
            if policy.allowed_paths
            and not any(self._matches(path, pattern) for pattern in policy.allowed_paths)
        )
        checks.append(
            ValidationCheck(
                "allowed_paths",
                not outside_allowed,
                "OK" if not outside_allowed else "PATH_NOT_ALLOWED",
                "all changed paths are allowed"
                if not outside_allowed
                else "changes found outside allowed paths",
                {"paths": list(outside_allowed)},
            )
        )

        new_symlinks = self._new_symlinks(workspace, changed)
        checks.append(
            ValidationCheck(
                "new_symlinks",
                not new_symlinks,
                "OK" if not new_symlinks else "NEW_SYMLINK",
                "no new symlinks"
                if not new_symlinks
                else "new symlinks are forbidden",
                {"paths": list(new_symlinks)},
            )
        )

        unexpected_types = self._unexpected_file_types(workspace.path, changed)
        checks.append(
            ValidationCheck(
                "file_types",
                not unexpected_types,
                "OK" if not unexpected_types else "UNEXPECTED_FILE_TYPE",
                "all changed paths have supported file types"
                if not unexpected_types
                else "special files are forbidden",
                {"paths": list(unexpected_types)},
            )
        )

        unexpected_binaries = self._unexpected_binaries(
            workspace.path, changed, policy.allowed_binary_extensions
        )
        checks.append(
            ValidationCheck(
                "unexpected_binaries",
                not unexpected_binaries,
                "OK" if not unexpected_binaries else "UNEXPECTED_BINARY",
                "no unexpected binary files"
                if not unexpected_binaries
                else "unexpected binary files found",
                {"paths": list(unexpected_binaries)},
            )
        )

        candidate_bytes = self._candidate_bytes(workspace, changed)
        candidate_limit = max(policy.max_diff_bytes * 4, policy.max_diff_bytes + 4096)
        candidate_ok = candidate_bytes <= candidate_limit
        checks.append(
            ValidationCheck(
                "candidate_size",
                candidate_ok,
                "OK" if candidate_ok else "CANDIDATE_TOO_LARGE",
                f"candidate content is {candidate_bytes} bytes",
                {"actual": candidate_bytes, "limit": candidate_limit},
            )
        )

        if unexpected_types:
            diff_check_ok = False
            diff_check_message = "diff check skipped because special files were found"
        else:
            diff_check_ok, diff_check_message = self._diff_check(workspace.path, changed)
        checks.append(
            ValidationCheck(
                "git_diff_check",
                diff_check_ok,
                "OK" if diff_check_ok else "GIT_DIFF_CHECK_FAILED",
                diff_check_message,
            )
        )

        if candidate_ok and not unexpected_types:
            diff, diff_error = self._build_diff(workspace.path, changed)
        else:
            diff = b""
            diff_error = "diff generation skipped after a fail-closed precheck"
        checks.append(
            ValidationCheck(
                "diff_generation",
                diff_error is None,
                "OK" if diff_error is None else "DIFF_GENERATION_FAILED",
                "diff generated successfully" if diff_error is None else diff_error,
            )
        )
        diff_size_ok = (
            candidate_ok and diff_error is None and len(diff) <= policy.max_diff_bytes
        )
        checks.append(
            ValidationCheck(
                "max_diff",
                diff_size_ok,
                "OK" if diff_size_ok else "MAX_DIFF_EXCEEDED",
                f"diff is {len(diff)} bytes, limit {policy.max_diff_bytes}",
                {"actual": len(diff), "limit": policy.max_diff_bytes},
            )
        )
        return ValidationResult(
            ok=all(check.passed for check in checks),
            checks=tuple(checks),
            changed_files=changed_files,
            diff_bytes=len(diff),
            diff=diff,
        )

    @staticmethod
    def _run_git(
        workspace: Path, argv: tuple[str, ...], timeout: int = 60
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ("git", "-C", str(workspace), *argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(argv, 127, b"", str(exc).encode())

    @staticmethod
    def _metadata_intact(workspace: Workspace) -> bool:
        git_file = workspace.path / ".git"
        try:
            current = git_file.read_bytes()
            mode = git_file.lstat().st_mode
        except OSError:
            return False
        return (
            stat.S_ISREG(mode)
            and current == workspace.git_file_content
            and sha256_bytes(current) == workspace.git_file_sha256
        )

    def _head_is_base(self, workspace: Workspace) -> tuple[bool, str]:
        result = self._run_git(workspace.path, ("rev-parse", "--verify", "HEAD^{commit}"))
        if result.returncode != 0:
            return False, result.stderr.decode("utf-8", errors="replace").strip()
        actual = result.stdout.decode().strip()
        return (
            actual == workspace.base_commit,
            "HEAD remains at the base commit"
            if actual == workspace.base_commit
            else f"HEAD changed from {workspace.base_commit} to {actual}",
        )

    def _changed_paths(
        self, workspace: Path
    ) -> tuple[tuple[_ChangedPath, ...], str | None]:
        result = self._run_git(
            workspace, ("status", "--porcelain=v1", "-z", "--untracked-files=all")
        )
        if result.returncode != 0:
            return (), result.stderr.decode("utf-8", errors="replace").strip()
        records = result.stdout.split(b"\0")
        changed: dict[str, _ChangedPath] = {}
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4 or record[2:3] != b" ":
                return (), "malformed git status output"
            status_code = record[:2].decode("ascii", errors="replace")
            path = record[3:].decode("utf-8", errors="surrogateescape")
            if "R" in status_code or "C" in status_code:
                if index >= len(records) or not records[index]:
                    return (), "malformed rename in git status output"
                index += 1  # old path does not count as an independently writable target
            if not self._safe_relative_path(path):
                return (), f"unsafe path in git status: {path!r}"
            changed[path] = _ChangedPath(path, status_code)
        return tuple(changed[path] for path in sorted(changed)), None

    @staticmethod
    def _safe_relative_path(path: str) -> bool:
        pure = PurePosixPath(path)
        return bool(path) and not pure.is_absolute() and ".." not in pure.parts

    @staticmethod
    def _matches(path: str, pattern: str) -> bool:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return fnmatch.fnmatchcase(path, pattern)

    def _new_symlinks(
        self, workspace: Workspace, changed: Iterable[_ChangedPath]
    ) -> tuple[str, ...]:
        new: list[str] = []
        for item in changed:
            path = workspace.path / item.path
            try:
                is_symlink = stat.S_ISLNK(path.lstat().st_mode)
            except FileNotFoundError:
                continue
            if not is_symlink:
                continue
            base = self._run_git(
                workspace.path, ("ls-tree", workspace.base_commit, "--", item.path)
            )
            base_mode = base.stdout.split(b" ", 1)[0] if base.returncode == 0 else b""
            if base_mode != b"120000":
                new.append(item.path)
        return tuple(new)

    def _unexpected_binaries(
        self,
        workspace: Path,
        changed: Iterable[_ChangedPath],
        allowed_extensions: tuple[str, ...],
    ) -> tuple[str, ...]:
        allowed = {
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in allowed_extensions
        }
        unexpected: list[str] = []
        for item in changed:
            path = workspace / item.path
            if not path.is_file() or path.is_symlink():
                continue
            try:
                with path.open("rb") as handle:
                    sample = handle.read(8192)
            except OSError:
                unexpected.append(item.path)
                continue
            if b"\0" in sample and path.suffix.lower() not in allowed:
                unexpected.append(item.path)
        numstat = self._run_git(workspace, ("diff", "--numstat", "HEAD", "--"))
        if numstat.returncode != 0:
            return tuple(sorted(set(unexpected) | {"<git-numstat-failed>"}))
        for line in numstat.stdout.decode("utf-8", errors="surrogateescape").splitlines():
            fields = line.split("\t", 2)
            if len(fields) != 3 or fields[0:2] != ["-", "-"]:
                continue
            changed_path = fields[2]
            extension = Path(changed_path).suffix.lower()
            if extension not in allowed:
                unexpected.append(changed_path)
        return tuple(unexpected)

    @staticmethod
    def _unexpected_file_types(
        workspace: Path, changed: Iterable[_ChangedPath]
    ) -> tuple[str, ...]:
        unexpected: list[str] = []
        for item in changed:
            path = workspace / item.path
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue  # A tracked deletion has no current file type.
            except OSError:
                unexpected.append(item.path)
                continue
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                unexpected.append(item.path)
        return tuple(unexpected)

    def _candidate_bytes(
        self, workspace: Workspace, changed: Iterable[_ChangedPath]
    ) -> int:
        total = 0
        for item in changed:
            path = workspace.path / item.path
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                total += 1
            base_size = self._run_git(
                workspace.path,
                ("cat-file", "-s", f"{workspace.base_commit}:{item.path}"),
            )
            if base_size.returncode == 0:
                try:
                    total += int(base_size.stdout.strip())
                except ValueError:
                    total += 1
        return total

    def _diff_check(
        self, workspace: Path, changed: Iterable[_ChangedPath]
    ) -> tuple[bool, str]:
        tracked = self._run_git(workspace, ("diff", "--check", "HEAD", "--"))
        if tracked.returncode != 0:
            return False, tracked.stdout.decode("utf-8", errors="replace").strip()
        for item in changed:
            if item.status != "??":
                continue
            result = self._run_git(
                workspace, ("diff", "--no-index", "--check", "/dev/null", f"./{item.path}")
            )
            if result.returncode not in {0, 1} or result.stdout:
                message = (result.stdout + result.stderr).decode(
                    "utf-8", errors="replace"
                ).strip()
                return False, message or f"diff check failed for {item.path}"
        return True, "git diff --check passed"

    def _build_diff(
        self, workspace: Path, changed: Iterable[_ChangedPath]
    ) -> tuple[bytes, str | None]:
        tracked = self._run_git(
            workspace,
            ("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"),
        )
        if tracked.returncode != 0:
            return b"", tracked.stderr.decode("utf-8", errors="replace").strip()
        parts = [tracked.stdout]
        for item in changed:
            if item.status != "??":
                continue
            result = self._run_git(
                workspace,
                (
                    "diff",
                    "--no-index",
                    "--binary",
                    "--no-ext-diff",
                    "/dev/null",
                    f"./{item.path}",
                ),
            )
            if result.returncode in {0, 1}:
                parts.append(result.stdout)
            else:
                detail = result.stderr.decode("utf-8", errors="replace").strip()
                return b"", detail or f"cannot generate diff for {item.path}"
        return b"".join(parts), None
