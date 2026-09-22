from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from happi_agent.models import AppConfig


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class ProjectFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "canonical"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "tests@example.invalid")
        git(self.repo, "config", "user.name", "happi tests")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "audit.md").write_text(
            "# Audit\n\nbase\n", encoding="utf-8"
        )
        (self.repo / "README.md").write_text("# Canonical\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")

        self.policies = self.root / "policies"
        self.policies.mkdir()
        self.write_policy()
        self.app = AppConfig(
            state_dir=self.root / "state",
            worktree_root=self.root / "worktrees",
            canonical_repo=self.repo,
            policies_dir=self.policies,
        )

    def write_policy(
        self,
        *,
        profile_id: str = "test-policy",
        max_files: int = 5,
        max_diff_bytes: int = 65536,
        allowed_paths: tuple[str, ...] = ("docs/**",),
        forbidden_paths: tuple[str, ...] = (".github/**", ".git/**"),
    ) -> None:
        payload = {
            "version": 1,
            "id": profile_id,
            "max_files": max_files,
            "max_diff_bytes": max_diff_bytes,
            "forbidden_paths": list(forbidden_paths),
            "allowed_paths": list(allowed_paths),
            "allowed_binary_extensions": [],
        }
        (self.policies / f"{profile_id}.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        self.temporary.cleanup()
