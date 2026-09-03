from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RunState(str, Enum):
    QUEUED = "QUEUED"
    PREFLIGHT = "PREFLIGHT"
    PREPARING = "PREPARING"
    COLLECTING = "COLLECTING"
    RUNNING_AGENT = "RUNNING_AGENT"
    VALIDATING = "VALIDATING"
    SUCCESS = "SUCCESS"
    QUARANTINED = "QUARANTINED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


TERMINAL_STATES = frozenset(
    {
        RunState.SUCCESS,
        RunState.QUARANTINED,
        RunState.BLOCKED,
        RunState.FAILED,
        RunState.TIMEOUT,
    }
)


ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset(
        {RunState.PREFLIGHT, RunState.BLOCKED, RunState.FAILED}
    ),
    RunState.PREFLIGHT: frozenset(
        {RunState.PREPARING, RunState.BLOCKED, RunState.FAILED}
    ),
    RunState.PREPARING: frozenset({RunState.COLLECTING, RunState.FAILED}),
    RunState.COLLECTING: frozenset({RunState.RUNNING_AGENT, RunState.FAILED}),
    RunState.RUNNING_AGENT: frozenset(
        {RunState.VALIDATING, RunState.FAILED, RunState.TIMEOUT}
    ),
    RunState.VALIDATING: frozenset(
        {RunState.SUCCESS, RunState.QUARANTINED, RunState.FAILED}
    ),
    RunState.SUCCESS: frozenset(),
    RunState.QUARANTINED: frozenset(),
    RunState.BLOCKED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.TIMEOUT: frozenset(),
}


@dataclass(frozen=True)
class ValidationPolicy:
    max_files: int
    max_diff_bytes: int
    forbidden_paths: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_binary_extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobConfig:
    version: int
    job_id: str
    prompt_path: Path
    collectors: tuple[str, ...]
    timeout_seconds: int
    validation: ValidationPolicy
    source_path: Path


@dataclass(frozen=True)
class AppConfig:
    state_dir: Path
    worktree_root: Path
    canonical_repo: Path
    jobs_dir: Path
    prompts_dir: Path
    codex_binary: str = "codex"
    lock_file: Path | None = None
    kill_switch: Path | None = None

    @property
    def effective_lock_file(self) -> Path:
        return self.lock_file or self.state_dir / "happi-agent.lock"

    @property
    def effective_kill_switch(self) -> Path:
        return self.kill_switch or self.state_dir / "KILL_SWITCH"


@dataclass(frozen=True)
class CodexExecutionResult:
    stdout_jsonl: str
    stderr: str
    final_message: str
    exit_code: int | None
    timed_out: bool = False
    protocol_error: str | None = None


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    checks: tuple[ValidationCheck, ...]
    changed_files: tuple[str, ...]
    diff_bytes: int
    diff: bytes = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
            "changed_files": list(self.changed_files),
            "diff_bytes": self.diff_bytes,
        }


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    state: RunState
    error_code: str | None = None

