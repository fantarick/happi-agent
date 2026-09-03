from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from happi_agent.codex import CodexExecutor, SubprocessCodexExecutor
from happi_agent.collectors.base import CollectorRegistry, default_registry
from happi_agent.config import load_job_config, resolved_config_hash
from happi_agent.models import (
    TERMINAL_STATES,
    AppConfig,
    CodexExecutionResult,
    RunOutcome,
    RunState,
    ValidationPolicy,
    ValidationResult,
)
from happi_agent.security import GlobalRunLock, kill_switch_active, sha256_bytes
from happi_agent.state import StateStore, utc_now
from happi_agent.validator import Validator
from happi_agent.workspace import Workspace, WorkspaceManager


class RunnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactWriter:
    def __init__(self, root: Path, run_id: str, state: StateStore):
        artifact_root = root.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(artifact_root, 0o700)
        self.run_dir = artifact_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(self.run_dir, 0o700)
        self.state = state
        self.run_id = run_id

    def write_bytes(self, name: str, data: bytes) -> Path:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise RunnerError("INVALID_ARTIFACT_NAME", f"unsafe artifact name: {name!r}")
        path = self.run_dir / name
        temporary = self.run_dir / f".{name}.tmp"
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        digest = sha256_bytes(data)
        self.state.add_artifact(self.run_id, name, path, digest, len(data))
        return path

    def write_text(self, name: str, text: str) -> Path:
        return self.write_bytes(name, text.encode("utf-8"))

    def write_json(self, name: str, value: object) -> Path:
        data = json.dumps(
            value, sort_keys=True, indent=2, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        return self.write_bytes(name, data)


class Runner:
    def __init__(
        self,
        app: AppConfig,
        *,
        state: StateStore | None = None,
        executor: CodexExecutor | None = None,
        collectors: CollectorRegistry | None = None,
        validator: Validator | None = None,
    ):
        self.app = app
        self.state = state or StateStore(app.state_dir / "state.sqlite3")
        self.executor = executor or SubprocessCodexExecutor(app.codex_binary)
        self.collectors = collectors or default_registry()
        self.validator = validator or Validator()
        self.workspaces = WorkspaceManager(app.canonical_repo, app.worktree_root)

    def run(self, job_id: str) -> RunOutcome:
        self.state.initialize()
        job = load_job_config(job_id, self.app, self.collectors.ids())
        config_sha256 = resolved_config_hash(self.app, job)
        run_id = uuid.uuid4().hex
        self.state.create_run(run_id, job_id, config_sha256)
        artifacts = ArtifactWriter(self.app.state_dir / "artifacts", run_id, self.state)
        lock = GlobalRunLock(self.app.effective_lock_file)
        if not lock.acquire():
            self.state.transition(
                run_id,
                RunState.BLOCKED,
                code="GLOBAL_LOCK_BUSY",
                error_code="GLOBAL_LOCK_BUSY",
                error_detail="another run owns the global lock",
            )
            return RunOutcome(run_id, RunState.BLOCKED, "GLOBAL_LOCK_BUSY")

        workspace: Workspace | None = None
        try:
            if kill_switch_active(self.app.effective_kill_switch):
                self.state.transition(
                    run_id,
                    RunState.BLOCKED,
                    code="KILL_SWITCH_ACTIVE",
                    details={"sentinel": str(self.app.effective_kill_switch)},
                    error_code="KILL_SWITCH_ACTIVE",
                    error_detail="global kill switch is present",
                )
                return RunOutcome(run_id, RunState.BLOCKED, "KILL_SWITCH_ACTIVE")

            self.state.transition(run_id, RunState.PREFLIGHT, code="PREFLIGHT_STARTED")
            base_commit, _ = self.workspaces.preflight()
            codex_version = self.executor.version()
            self.state.update_run(
                run_id, base_commit=base_commit, codex_version=codex_version
            )

            self.state.transition(run_id, RunState.PREPARING, code="WORKTREE_PREPARING")
            workspace = self.workspaces.create(run_id)
            self.state.update_run(
                run_id,
                base_commit=workspace.base_commit,
                workspace_path=str(workspace.path),
            )

            self.state.transition(run_id, RunState.COLLECTING, code="COLLECTION_STARTED")
            collector_results = self.collectors.collect(job.collectors)
            snapshot = {
                "schema_version": 1,
                "run_id": run_id,
                "job_id": job.job_id,
                "collected_at": utc_now(),
                "collectors": [result.to_dict() for result in collector_results],
            }
            snapshot_path = artifacts.write_json("collector-snapshot.json", snapshot)
            prompt_template = job.prompt_path.read_text(encoding="utf-8")
            prompt = self._build_prompt(
                prompt_template,
                snapshot,
                snapshot_path,
                workspace,
                job.timeout_seconds,
            )
            self.state.update_run(
                run_id, prompt_sha256=sha256_bytes(prompt.encode("utf-8"))
            )

            self.state.transition(
                run_id, RunState.RUNNING_AGENT, code="CODEX_STARTED"
            )
            execution = self.executor.execute(
                prompt, workspace.path, job.timeout_seconds
            )
            self._save_codex_artifacts(artifacts, execution)
            self.state.update_run(run_id, exit_code=execution.exit_code)

            if execution.timed_out:
                self._save_diagnostic_validation(artifacts, workspace, job.validation)
                self.state.transition(
                    run_id,
                    RunState.TIMEOUT,
                    code="CODEX_TIMEOUT",
                    error_code="CODEX_TIMEOUT",
                    error_detail=f"Codex exceeded {job.timeout_seconds} seconds",
                )
                return RunOutcome(run_id, RunState.TIMEOUT, "CODEX_TIMEOUT")

            if execution.exit_code != 0:
                self._save_diagnostic_validation(artifacts, workspace, job.validation)
                error = "CODEX_EXIT_NONZERO"
                detail = f"Codex exited with code {execution.exit_code}"
                self._fail_and_cleanup(run_id, artifacts, workspace, error, detail)
                workspace = None
                return RunOutcome(run_id, RunState.FAILED, error)

            if execution.protocol_error:
                self._save_diagnostic_validation(artifacts, workspace, job.validation)
                error = "CODEX_PROTOCOL_ERROR"
                self._fail_and_cleanup(
                    run_id, artifacts, workspace, error, execution.protocol_error
                )
                workspace = None
                return RunOutcome(run_id, RunState.FAILED, error)

            self.state.transition(
                run_id, RunState.VALIDATING, code="VALIDATION_STARTED"
            )
            validation = self.validator.validate(workspace, job.validation)
            self._save_validation_artifacts(artifacts, validation)
            if not validation.ok:
                failed_codes = [
                    check.code for check in validation.checks if not check.passed
                ]
                self.state.transition(
                    run_id,
                    RunState.QUARANTINED,
                    code="VALIDATION_REJECTED",
                    details={"failed_checks": failed_codes},
                    error_code="VALIDATION_REJECTED",
                    error_detail=",".join(failed_codes),
                )
                return RunOutcome(run_id, RunState.QUARANTINED, "VALIDATION_REJECTED")

            self.workspaces.cleanup(workspace)
            workspace = None
            self.state.transition(
                run_id, RunState.SUCCESS, code="VALIDATION_ACCEPTED_AND_CLEANED"
            )
            return RunOutcome(run_id, RunState.SUCCESS)
        except Exception as exc:
            code = getattr(exc, "code", "UNEXPECTED_ERROR")
            detail = getattr(exc, "message", str(exc)) or exc.__class__.__name__
            artifacts.write_json(
                "error.json",
                {"error_code": code, "message": detail, "type": exc.__class__.__name__},
            )
            if workspace is not None:
                self._save_diagnostic_validation(artifacts, workspace, job.validation)
            current = self.state.current_state(run_id)
            if current not in TERMINAL_STATES:
                self.state.transition(
                    run_id,
                    RunState.FAILED,
                    code=code,
                    error_code=code,
                    error_detail=detail,
                )
            if workspace is not None:
                try:
                    self.workspaces.cleanup(workspace)
                except Exception as cleanup_exc:
                    artifacts.write_json(
                        "cleanup-error.json",
                        {
                            "error_code": getattr(
                                cleanup_exc, "code", "WORKSPACE_CLEANUP_FAILED"
                            ),
                            "message": str(cleanup_exc),
                        },
                    )
            return RunOutcome(run_id, RunState.FAILED, str(code))
        finally:
            lock.release()

    @staticmethod
    def _build_prompt(
        template: str,
        snapshot: dict[str, Any],
        snapshot_path: Path,
        workspace: Workspace,
        timeout_seconds: int,
    ) -> str:
        snapshot_json = json.dumps(snapshot, sort_keys=True, indent=2, ensure_ascii=False)
        contract = f"""

## Vincoli imposti dall'orchestratore

- Opera soltanto nel worktree corrente: `{workspace.path}`.
- Lo snapshot seguente e l'artifact auditabile `{snapshot_path.name}` sono gli unici dati host autorizzati.
- Non raccogliere autonomamente informazioni di sistema.
- Non usare rete, browser, MCP, app, plugin, subagent o altri agenti.
- Non eseguire `git commit`, `git push`, creare PR, fare merge o cambiare `HEAD`.
- Non usare sudo e non tentare escalation di privilegi.
- Non modificare `.git`, configurazioni dell'orchestratore o file fuori dal worktree.
- Apporta solo gli aggiornamenti documentali richiesti e termina entro {timeout_seconds} secondi.

## Snapshot collector (JSON)

```json
{snapshot_json}
```
"""
        return template.rstrip() + contract

    @staticmethod
    def _save_codex_artifacts(
        artifacts: ArtifactWriter, execution: CodexExecutionResult
    ) -> None:
        artifacts.write_text("codex.stdout.jsonl", execution.stdout_jsonl)
        artifacts.write_text("codex.stderr.log", execution.stderr)
        artifacts.write_text("codex.final.txt", execution.final_message)
        artifacts.write_json(
            "codex-result.json",
            {
                "exit_code": execution.exit_code,
                "timed_out": execution.timed_out,
                "protocol_error": execution.protocol_error,
            },
        )

    @staticmethod
    def _save_validation_artifacts(
        artifacts: ArtifactWriter, validation: ValidationResult, *, diagnostic: bool = False
    ) -> None:
        prefix = "diagnostic-" if diagnostic else ""
        artifacts.write_json(f"{prefix}validation.json", validation.to_dict())
        artifacts.write_bytes(f"{prefix}diff.patch", validation.diff)

    def _save_diagnostic_validation(
        self,
        artifacts: ArtifactWriter,
        workspace: Workspace,
        policy: ValidationPolicy,
    ) -> None:
        try:
            validation = self.validator.validate(workspace, policy)
        except Exception as exc:
            artifacts.write_json(
                "diagnostic-validation-error.json",
                {"error_code": "DIAGNOSTIC_VALIDATION_FAILED", "message": str(exc)},
            )
            return
        self._save_validation_artifacts(artifacts, validation, diagnostic=True)

    def _fail_and_cleanup(
        self,
        run_id: str,
        artifacts: ArtifactWriter,
        workspace: Workspace,
        code: str,
        detail: str,
    ) -> None:
        artifacts.write_json("error.json", {"error_code": code, "message": detail})
        self.workspaces.cleanup(workspace)
        self.state.transition(
            run_id,
            RunState.FAILED,
            code=code,
            error_code=code,
            error_detail=detail,
        )
