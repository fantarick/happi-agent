from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from happi_agent.models import ALLOWED_TRANSITIONS, TERMINAL_STATES, RunState


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class IllegalTransition(StateError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    base_commit TEXT,
    codex_version TEXT,
    prompt_sha256 TEXT,
    config_sha256 TEXT NOT NULL,
    exit_code INTEGER,
    error_code TEXT,
    error_detail TEXT,
    workspace_path TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    code TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, name)
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id, artifact_id);
"""


class StateStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.database_path.parent, 0o700)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
        os.chmod(self.database_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def create_run(self, run_id: str, job_id: str, config_sha256: str) -> None:
        created = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(run_id, job_id, state, started_at, config_sha256)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, job_id, RunState.QUEUED.value, created, config_sha256),
            )
            connection.execute(
                """
                INSERT INTO events(run_id, created_at, from_state, to_state, code)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (run_id, created, RunState.QUEUED.value, "RUN_CREATED"),
            )

    def transition(
        self,
        run_id: str,
        new_state: RunState,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise StateError("RUN_NOT_FOUND", f"run not found: {run_id}")
            current = RunState(row["state"])
            if new_state not in ALLOWED_TRANSITIONS[current]:
                raise IllegalTransition(
                    "ILLEGAL_TRANSITION",
                    f"illegal transition {current.value} -> {new_state.value}",
                )
            finished_at = timestamp if new_state in TERMINAL_STATES else None
            connection.execute(
                """
                UPDATE runs
                SET state = ?, finished_at = COALESCE(?, finished_at),
                    error_code = COALESCE(?, error_code),
                    error_detail = COALESCE(?, error_detail)
                WHERE run_id = ?
                """,
                (
                    new_state.value,
                    finished_at,
                    error_code,
                    error_detail,
                    run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO events(
                    run_id, created_at, from_state, to_state, code, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    timestamp,
                    current.value,
                    new_state.value,
                    code,
                    json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                ),
            )

    def update_run(self, run_id: str, **fields: object) -> None:
        allowed = {
            "base_commit",
            "codex_version",
            "prompt_sha256",
            "exit_code",
            "error_code",
            "error_detail",
            "workspace_path",
        }
        if not fields or not set(fields) <= allowed:
            raise StateError("INVALID_RUN_UPDATE", "invalid run metadata update")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [run_id]
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", values
            )
            if cursor.rowcount != 1:
                raise StateError("RUN_NOT_FOUND", f"run not found: {run_id}")

    def add_artifact(
        self,
        run_id: str,
        name: str,
        path: Path,
        sha256: str,
        size_bytes: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    run_id, name, path, sha256, size_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, name) DO UPDATE SET
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    created_at = excluded.created_at
                """,
                (run_id, name, str(path), sha256, size_bytes, utc_now()),
            )

    def current_state(self, run_id: str) -> RunState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise StateError("RUN_NOT_FOUND", f"run not found: {run_id}")
        return RunState(row["state"])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            events = connection.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY event_id", (run_id,)
            ).fetchall()
            artifacts = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY artifact_id",
                (run_id,),
            ).fetchall()
        result = dict(run)
        result["events"] = [dict(row) for row in events]
        for event in result["events"]:
            event["details"] = json.loads(event.pop("details_json"))
        result["artifacts"] = [dict(row) for row in artifacts]
        return result

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise StateError("INVALID_LIMIT", "limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, job_id, state, started_at, finished_at, error_code
                FROM runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
