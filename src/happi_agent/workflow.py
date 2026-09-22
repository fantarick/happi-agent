from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from happi_agent.models import AppConfig, ValidationPolicy, ValidationResult
from happi_agent.protocol import (
    DEFAULT_MAX_ITERATIONS,
    PROTOCOL_VERSION,
    ProtocolError,
    WorkflowSnapshot,
    WorkflowState,
    parse_architect_review,
    parse_engineer_handoff,
    parse_feature_contract,
    transition as protocol_transition,
)
from happi_agent.security import GlobalRunLock, kill_switch_active, sha256_bytes
from happi_agent.state import utc_now
from happi_agent.validator import Validator
from happi_agent.workspace import Workspace, WorkspaceManager


WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    protocol_version TEXT NOT NULL,
    state TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    max_iterations INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    repository TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    workspace_id TEXT,
    workspace_path TEXT,
    common_git_dir TEXT,
    git_file_content BLOB,
    git_file_sha256 TEXT,
    merged_commit TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_id, name)
);

CREATE INDEX IF NOT EXISTS idx_workflows_updated_at
    ON workflows(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events
    ON workflow_events(workflow_id, event_id);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts
    ON workflow_artifacts(workflow_id, artifact_id);
"""


class WorkflowStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.database_path.parent, 0o700)
        with self._connect() as connection:
            connection.executescript(WORKFLOW_SCHEMA)
        os.chmod(self.database_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def create(
        self,
        workflow_id: str,
        repository: Path,
        base_commit: str,
        *,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> WorkflowSnapshot:
        if not WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise WorkflowError(
                "INVALID_WORKFLOW_ID",
                "workflow id must match [a-z0-9][a-z0-9._-]{0,63}",
            )
        snapshot = WorkflowSnapshot(
            workflow_id,
            WorkflowState.INTENT,
            iteration=0,
            max_iterations=max_iterations,
        )
        now = utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO workflows(
                        workflow_id, protocol_version, state, iteration,
                        max_iterations, created_at, updated_at, repository,
                        base_commit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        PROTOCOL_VERSION,
                        snapshot.state.value,
                        snapshot.iteration,
                        snapshot.max_iterations,
                        now,
                        now,
                        str(repository.resolve()),
                        base_commit,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events(
                        workflow_id, occurred_at, event_type, from_state,
                        to_state, details_json
                    ) VALUES (?, ?, ?, NULL, ?, '{}')
                    """,
                    (
                        workflow_id,
                        now,
                        "WORKFLOW_CREATED",
                        WorkflowState.INTENT.value,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkflowError(
                "WORKFLOW_EXISTS", f"workflow already exists: {workflow_id}"
            ) from exc
        return snapshot

    def snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state, iteration, max_iterations
                FROM workflows WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise WorkflowError(
                "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
            )
        return WorkflowSnapshot(
            workflow_id,
            WorkflowState(row["state"]),
            int(row["iteration"]),
            int(row["max_iterations"]),
        )

    def transition(
        self,
        workflow_id: str,
        target: WorkflowState,
        event_type: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> WorkflowSnapshot:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state, iteration, max_iterations
                FROM workflows WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
            if row is None:
                raise WorkflowError(
                    "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
                )
            current = WorkflowSnapshot(
                workflow_id,
                WorkflowState(row["state"]),
                int(row["iteration"]),
                int(row["max_iterations"]),
            )
            updated = protocol_transition(current, target)
            connection.execute(
                """
                UPDATE workflows
                SET state = ?, iteration = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (updated.state.value, updated.iteration, now, workflow_id),
            )
            connection.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, occurred_at, event_type, from_state,
                    to_state, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    now,
                    event_type,
                    current.state.value,
                    updated.state.value,
                    json.dumps(
                        details or {}, sort_keys=True, separators=(",", ":")
                    ),
                ),
            )
        return updated

    def set_workspace(self, workflow_id: str, workspace: Workspace, workspace_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflows
                SET workspace_id = ?, workspace_path = ?, common_git_dir = ?,
                    git_file_content = ?, git_file_sha256 = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (
                    workspace_id,
                    str(workspace.path),
                    str(workspace.common_git_dir),
                    workspace.git_file_content,
                    workspace.git_file_sha256,
                    utc_now(),
                    workflow_id,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkflowError(
                    "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
                )

    def workspace(self, workflow_id: str) -> Workspace:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT workspace_path, base_commit, common_git_dir,
                       git_file_content, git_file_sha256
                FROM workflows WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise WorkflowError(
                "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
            )
        required = (
            row["workspace_path"],
            row["base_commit"],
            row["common_git_dir"],
            row["git_file_content"],
            row["git_file_sha256"],
        )
        if any(value is None for value in required):
            raise WorkflowError(
                "WORKSPACE_NOT_PREPARED",
                f"workflow has no prepared workspace: {workflow_id}",
            )
        return Workspace(
            path=Path(row["workspace_path"]),
            base_commit=row["base_commit"],
            common_git_dir=Path(row["common_git_dir"]),
            git_file_content=bytes(row["git_file_content"]),
            git_file_sha256=row["git_file_sha256"],
        )

    def base_commit(self, workflow_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT base_commit FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise WorkflowError(
                "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
            )
        return str(row["base_commit"])

    def set_merged_commit(self, workflow_id: str, commit: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflows SET merged_commit = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (commit, utc_now(), workflow_id),
            )
            if cursor.rowcount != 1:
                raise WorkflowError(
                    "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
                )

    def add_artifact(
        self,
        workflow_id: str,
        name: str,
        path: Path,
        sha256: str,
        size_bytes: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_artifacts(
                    workflow_id, name, path, sha256, size_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id, name) DO UPDATE SET
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    created_at = excluded.created_at
                """,
                (
                    workflow_id,
                    name,
                    str(path),
                    sha256,
                    size_bytes,
                    utc_now(),
                ),
            )

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            workflow = connection.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if workflow is None:
                return None
            events = connection.execute(
                """
                SELECT * FROM workflow_events
                WHERE workflow_id = ? ORDER BY event_id
                """,
                (workflow_id,),
            ).fetchall()
            artifacts = connection.execute(
                """
                SELECT * FROM workflow_artifacts
                WHERE workflow_id = ? ORDER BY artifact_id
                """,
                (workflow_id,),
            ).fetchall()
        result = dict(workflow)
        result.pop("git_file_content", None)
        result["events"] = [dict(row) for row in events]
        for event in result["events"]:
            event["details"] = json.loads(event.pop("details_json"))
        result["artifacts"] = [dict(row) for row in artifacts]
        return result

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise WorkflowError("INVALID_LIMIT", "limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT workflow_id, state, iteration, max_iterations,
                       created_at, updated_at, base_commit
                FROM workflows ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class WorkflowArtifactWriter:
    def __init__(self, root: Path, workflow_id: str, store: WorkflowStore):
        if not WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise WorkflowError("INVALID_WORKFLOW_ID", "unsafe workflow id")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.directory = root / workflow_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.workflow_id = workflow_id
        self.store = store

    def write_bytes(self, name: str, data: bytes) -> Path:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise WorkflowError("INVALID_ARTIFACT_NAME", f"unsafe artifact name: {name!r}")
        path = self.directory / name
        temporary = self.directory / f".{name}.tmp"
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        digest = sha256_bytes(data)
        self.store.add_artifact(
            self.workflow_id, name, path, digest, len(data)
        )
        return path

    def write_json(self, name: str, value: object) -> Path:
        payload = json.dumps(
            value, sort_keys=True, indent=2, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        return self.write_bytes(name, payload)


class WorkflowController:
    def __init__(
        self,
        app: AppConfig,
        *,
        store: WorkflowStore | None = None,
        validator: Validator | None = None,
    ):
        self.app = app
        self.store = store or WorkflowStore(app.state_dir / "state.sqlite3")
        self.validator = validator or Validator()
        self.workspaces = WorkspaceManager(app.canonical_repo, app.worktree_root)

    def initialize(self) -> None:
        self.store.initialize()

    def _lock(self) -> GlobalRunLock:
        if kill_switch_active(self.app.effective_kill_switch):
            raise WorkflowError(
                "KILL_SWITCH_ACTIVE", "global kill switch blocks workflow mutation"
            )
        lock = GlobalRunLock(self.app.effective_lock_file)
        if not lock.acquire():
            raise WorkflowError("GLOBAL_LOCK_BUSY", "another operation owns the global lock")
        return lock

    def _artifacts(self, workflow_id: str) -> WorkflowArtifactWriter:
        return WorkflowArtifactWriter(
            self.app.state_dir / "workflow-artifacts", workflow_id, self.store
        )

    @staticmethod
    def _decode_json(payload: bytes, label: str) -> object:
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                "INVALID_JSON", f"{label} is not valid UTF-8 JSON"
            ) from exc

    def start(
        self,
        workflow_id: str,
        intent: bytes,
        *,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> WorkflowSnapshot:
        if not intent.strip():
            raise WorkflowError("EMPTY_INTENT", "intent artifact must not be empty")
        lock = self._lock()
        try:
            base_commit, _ = self.workspaces.preflight()
            snapshot = self.store.create(
                workflow_id,
                self.app.canonical_repo,
                base_commit,
                max_iterations=max_iterations,
            )
            self._artifacts(workflow_id).write_bytes("intent.txt", intent)
            return self.store.transition(
                workflow_id,
                WorkflowState.DISCOVERY_REQUIRED,
                "INTENT_RECORDED",
                details={"base_commit": base_commit},
            )
        finally:
            lock.release()

    def discovery_complete(
        self, workflow_id: str, evidence: bytes
    ) -> WorkflowSnapshot:
        if not evidence.strip():
            raise WorkflowError(
                "EMPTY_DISCOVERY_EVIDENCE",
                "discovery evidence must not be empty",
            )
        lock = self._lock()
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.DISCOVERY_REQUIRED:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "discovery evidence is accepted only in DISCOVERY_REQUIRED",
                )
            self._artifacts(workflow_id).write_bytes(
                "discovery-evidence.txt", evidence
            )
            return self.store.transition(
                workflow_id,
                WorkflowState.CONTRACT_REQUIRED,
                "DISCOVERY_COMPLETED",
            )
        finally:
            lock.release()

    def approve_contract(
        self, workflow_id: str, payload: bytes
    ) -> WorkflowSnapshot:
        raw = self._decode_json(payload, "feature contract")
        try:
            contract = parse_feature_contract(raw)
        except ProtocolError as exc:
            raise WorkflowError(exc.code, exc.message) from exc
        if contract.status != "APPROVED":
            raise WorkflowError(
                "CONTRACT_NOT_APPROVED",
                "feature contract must have status APPROVED",
            )

        lock = self._lock()
        workspace: Workspace | None = None
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.CONTRACT_REQUIRED:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "contract is accepted only in CONTRACT_REQUIRED",
                )
            expected_base = self.store.base_commit(workflow_id)
            current_base, _ = self.workspaces.preflight()
            if current_base != expected_base:
                raise WorkflowError(
                    "BASE_COMMIT_MOVED",
                    "canonical repository HEAD changed after workflow start",
                )
            self._artifacts(workflow_id).write_bytes("contract.json", payload)
            self.store.transition(
                workflow_id,
                WorkflowState.CONTRACT_READY,
                "CONTRACT_APPROVED",
                details={
                    "acceptance_criteria": len(contract.acceptance_criteria),
                },
            )
            workspace_id = uuid.uuid4().hex
            workspace = self.workspaces.create(workspace_id)
            if workspace.base_commit != expected_base:
                self.workspaces.cleanup(workspace)
                workspace = None
                raise WorkflowError(
                    "BASE_COMMIT_MOVED",
                    "workspace base differs from workflow base commit",
                )
            self.store.set_workspace(workflow_id, workspace, workspace_id)
            return self.store.transition(
                workflow_id,
                WorkflowState.ENGINEER_REQUIRED,
                "WORKSPACE_PREPARED",
                details={"workspace": str(workspace.path)},
            )
        except Exception:
            if workspace is not None:
                try:
                    self.workspaces.cleanup(workspace)
                except Exception:
                    pass
            raise
        finally:
            lock.release()

    def ingest_engineer_handoff(
        self, workflow_id: str, payload: bytes
    ) -> WorkflowSnapshot:
        raw = self._decode_json(payload, "engineer handoff")
        try:
            handoff = parse_engineer_handoff(raw)
        except ProtocolError as exc:
            raise WorkflowError(exc.code, exc.message) from exc

        lock = self._lock()
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.ENGINEER_REQUIRED:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "engineer handoff is accepted only in ENGINEER_REQUIRED",
                )
            expected_iteration = snapshot.iteration + 1
            if handoff.iteration != expected_iteration:
                raise WorkflowError(
                    "ITERATION_MISMATCH",
                    f"expected engineer iteration {expected_iteration}",
                )
            if handoff.base_commit != self.store.base_commit(workflow_id):
                raise WorkflowError(
                    "BASE_COMMIT_MISMATCH",
                    "engineer handoff base commit does not match workflow",
                )
            self._artifacts(workflow_id).write_bytes(
                f"engineer-handoff-{handoff.iteration:03d}.json",
                payload,
            )
            if handoff.status == "BLOCKED":
                return self.store.transition(
                    workflow_id,
                    WorkflowState.BLOCKED,
                    "WORKFLOW_BLOCKED",
                    details={"source": "engineer_handoff"},
                )
            if handoff.status == "ESCALATION_REQUIRED":
                return self.store.transition(
                    workflow_id,
                    WorkflowState.ESCALATED,
                    "HUMAN_ESCALATED",
                    details={"source": "engineer_handoff"},
                )
            return self.store.transition(
                workflow_id,
                WorkflowState.VERIFYING,
                "ENGINEER_HANDOFF_ACCEPTED",
                details={"iteration": handoff.iteration},
            )
        finally:
            lock.release()

    def _route_changes(
        self, workflow_id: str, *, source: str
    ) -> WorkflowSnapshot:
        snapshot = self.store.snapshot(workflow_id)
        if snapshot.state is not WorkflowState.CHANGES_REQUIRED:
            raise WorkflowError(
                "WRONG_WORKFLOW_STATE", "workflow is not in CHANGES_REQUIRED"
            )
        if snapshot.iteration >= snapshot.max_iterations:
            return self.store.transition(
                workflow_id,
                WorkflowState.ESCALATED,
                "ITERATION_LIMIT_REACHED",
                details={"source": source},
            )
        return self.store.transition(
            workflow_id,
            WorkflowState.ENGINEER_REQUIRED,
            "CORRECTION_PASS_REQUESTED",
            details={"source": source, "next_iteration": snapshot.iteration + 1},
        )

    def verify(
        self, workflow_id: str, policy: ValidationPolicy
    ) -> tuple[WorkflowSnapshot, ValidationResult]:
        lock = self._lock()
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.VERIFYING:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "verification is allowed only in VERIFYING",
                )
            workspace = self.store.workspace(workflow_id)
            result = self.validator.validate(workspace, policy)
            artifacts = self._artifacts(workflow_id)
            artifacts.write_json(
                f"validation-{snapshot.iteration:03d}.json", result.to_dict()
            )
            artifacts.write_bytes(
                f"diff-{snapshot.iteration:03d}.patch", result.diff
            )
            if result.ok:
                updated = self.store.transition(
                    workflow_id,
                    WorkflowState.REVIEW_REQUIRED,
                    "VERIFICATION_PASSED",
                    details={
                        "iteration": snapshot.iteration,
                        "changed_files": list(result.changed_files),
                        "diff_bytes": result.diff_bytes,
                    },
                )
                return updated, result
            self.store.transition(
                workflow_id,
                WorkflowState.CHANGES_REQUIRED,
                "VERIFICATION_FAILED",
                details={
                    "iteration": snapshot.iteration,
                    "failed_checks": [
                        check.code for check in result.checks if not check.passed
                    ],
                },
            )
            return self._route_changes(
                workflow_id, source="deterministic_verification"
            ), result
        finally:
            lock.release()

    def ingest_architect_review(
        self, workflow_id: str, payload: bytes
    ) -> WorkflowSnapshot:
        raw = self._decode_json(payload, "architect review")
        try:
            review = parse_architect_review(raw)
        except ProtocolError as exc:
            raise WorkflowError(exc.code, exc.message) from exc

        lock = self._lock()
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.REVIEW_REQUIRED:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "architect review is accepted only in REVIEW_REQUIRED",
                )
            self._artifacts(workflow_id).write_bytes(
                f"architect-review-{snapshot.iteration:03d}.json", payload
            )
            if review.verdict == "APPROVE":
                return self.store.transition(
                    workflow_id,
                    WorkflowState.HUMAN_MERGE_REQUIRED,
                    "REVIEW_APPROVED",
                    details={"iteration": snapshot.iteration},
                )
            if review.verdict == "ESCALATE":
                return self.store.transition(
                    workflow_id,
                    WorkflowState.ESCALATED,
                    "REVIEW_ESCALATED",
                    details={"iteration": snapshot.iteration},
                )
            self.store.transition(
                workflow_id,
                WorkflowState.CHANGES_REQUIRED,
                "REVIEW_REQUESTED_CHANGES",
                details={
                    "iteration": snapshot.iteration,
                    "blocking_findings": len(review.blocking_findings),
                },
            )
            return self._route_changes(workflow_id, source="architect_review")
        finally:
            lock.release()

    def complete(self, workflow_id: str, merged_commit: str) -> WorkflowSnapshot:
        if not re.fullmatch(r"[0-9a-f]{40}", merged_commit):
            raise WorkflowError(
                "INVALID_MERGED_COMMIT", "merged commit must be a full 40-char SHA"
            )
        lock = self._lock()
        try:
            snapshot = self.store.snapshot(workflow_id)
            if snapshot.state is not WorkflowState.HUMAN_MERGE_REQUIRED:
                raise WorkflowError(
                    "WRONG_WORKFLOW_STATE",
                    "completion requires HUMAN_MERGE_REQUIRED",
                )
            current_head, _ = self.workspaces.preflight()
            if current_head != merged_commit:
                raise WorkflowError(
                    "MERGE_NOT_OBSERVED",
                    "canonical repository HEAD does not equal merged commit",
                )
            workspace = self.store.workspace(workflow_id)
            self.workspaces.cleanup(workspace)
            self.store.set_merged_commit(workflow_id, merged_commit)
            return self.store.transition(
                workflow_id,
                WorkflowState.COMPLETE,
                "HUMAN_MERGED",
                details={"merged_commit": merged_commit},
            )
        finally:
            lock.release()

    def status(self, workflow_id: str) -> dict[str, Any]:
        record = self.store.get(workflow_id)
        if record is None:
            raise WorkflowError(
                "WORKFLOW_NOT_FOUND", f"workflow not found: {workflow_id}"
            )
        record["next_action"] = self.next_action(workflow_id)
        return record

    def next_action(self, workflow_id: str) -> str:
        state = self.store.snapshot(workflow_id).state
        actions = {
            WorkflowState.INTENT: "record_intent",
            WorkflowState.DISCOVERY_REQUIRED: "complete_repository_discovery",
            WorkflowState.CONTRACT_REQUIRED: "approve_feature_contract",
            WorkflowState.CONTRACT_READY: "prepare_workspace",
            WorkflowState.ENGINEER_REQUIRED: "invoke_repository_engineer",
            WorkflowState.VERIFYING: "run_deterministic_verification",
            WorkflowState.REVIEW_REQUIRED: "invoke_independent_reviewer",
            WorkflowState.CHANGES_REQUIRED: "route_correction",
            WorkflowState.HUMAN_MERGE_REQUIRED: "human_merge",
            WorkflowState.COMPLETE: "none",
            WorkflowState.ESCALATED: "human_decision_required",
            WorkflowState.BLOCKED: "resolve_blocker",
        }
        return actions[state]
