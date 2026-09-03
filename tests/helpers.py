from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from happi_agent.collectors.base import CollectorRegistry, CollectorResult
from happi_agent.models import AppConfig, CodexExecutionResult


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


class StaticCollector:
    collector_id = "test.snapshot"

    def collect(self) -> CollectorResult:
        return CollectorResult(self.collector_id, True, {"value": "known"})


class FakeExecutor:
    def __init__(
        self,
        action: Callable[[Path], None] | None = None,
        *,
        exit_code: int = 0,
        timed_out: bool = False,
        protocol_error: str | None = None,
    ):
        self.action = action
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.protocol_error = protocol_error
        self.version_called = False
        self.execute_called = False

    def version(self) -> str:
        self.version_called = True
        return "fake-codex 0.1"

    def execute(
        self, prompt: str, workspace: Path, timeout_seconds: int
    ) -> CodexExecutionResult:
        self.execute_called = True
        if self.action is not None:
            self.action(workspace)
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "fake completed"},
        }
        return CodexExecutionResult(
            stdout_jsonl=json.dumps(event) + "\n",
            stderr="fake stderr\n" if self.exit_code else "",
            final_message="fake completed",
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            protocol_error=self.protocol_error,
        )


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
        (self.repo / "docs" / "audit.md").write_text("# Audit\n\nbase\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Canonical\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")

        self.jobs = self.root / "jobs"
        self.prompts = self.root / "prompts"
        self.jobs.mkdir()
        self.prompts.mkdir()
        (self.prompts / "test.md").write_text("Update docs from snapshot.\n", encoding="utf-8")
        self.write_job()
        self.app = AppConfig(
            state_dir=self.root / "state",
            worktree_root=self.root / "worktrees",
            canonical_repo=self.repo,
            jobs_dir=self.jobs,
            prompts_dir=self.prompts,
            codex_binary="codex",
        )
        self.registry = CollectorRegistry()
        self.registry.register(StaticCollector())

    def write_job(
        self,
        *,
        max_files: int = 5,
        max_diff_bytes: int = 65536,
        collector: str = "test.snapshot",
        allowed_paths: tuple[str, ...] = ("docs/**",),
    ) -> None:
        allowed = "\n".join(f'    - "{value}"' for value in allowed_paths)
        text = f"""version: 1
id: test-job
prompt: test.md
collectors:
  - {collector}
timeout_seconds: 5
validation:
  max_files: {max_files}
  max_diff_bytes: {max_diff_bytes}
  forbidden_paths:
    - ".github/**"
    - ".git/**"
  allowed_paths:
{allowed}
"""
        (self.jobs / "test-job.yaml").write_text(text, encoding="utf-8")

    def close(self) -> None:
        self.temporary.cleanup()

