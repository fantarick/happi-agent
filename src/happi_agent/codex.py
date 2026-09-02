from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Protocol

from happi_agent.models import CodexExecutionResult
from happi_agent.security import codex_process_environment


class CodexError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CodexExecutor(Protocol):
    def version(self) -> str: ...

    def execute(
        self, prompt: str, workspace: Path, timeout_seconds: int
    ) -> CodexExecutionResult: ...


def _final_message_and_protocol_error(stdout_jsonl: str) -> tuple[str, str | None]:
    final_message = ""
    turn_completed = False
    reported_failure = False
    for line_number, line in enumerate(stdout_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return final_message, f"invalid JSONL at line {line_number}"
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return final_message, f"invalid Codex event at line {line_number}"
        if event["type"] == "turn.completed":
            turn_completed = True
        elif event["type"] in {"turn.failed", "error"}:
            reported_failure = True
        if event["type"] == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                final_message = item["text"]
    if reported_failure:
        return final_message, "Codex JSONL reported a failure event"
    if not turn_completed:
        return final_message, "Codex JSONL did not contain turn.completed"
    if not final_message:
        return final_message, "Codex JSONL did not contain a final agent message"
    return final_message, None


class SubprocessCodexExecutor:
    """Codex CLI worker with explicit, fail-closed per-run policy."""

    def __init__(self, binary: str = "codex", termination_grace_seconds: int = 5):
        if not binary or "/" in binary:
            raise ValueError("Codex binary must be a bare executable name")
        self.binary = binary
        self.termination_grace_seconds = termination_grace_seconds

    def version(self) -> str:
        try:
            completed = subprocess.run(
                (self.binary, "--version"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                shell=False,
                env=codex_process_environment(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise CodexError("CODEX_UNAVAILABLE", f"cannot execute Codex: {exc}") from exc
        if completed.returncode != 0:
            raise CodexError(
                "CODEX_VERSION_FAILED",
                completed.stderr.strip() or "codex --version failed",
            )
        version = completed.stdout.strip()
        if not version:
            raise CodexError("CODEX_VERSION_FAILED", "Codex returned an empty version")
        return version

    def command(self, workspace: Path) -> tuple[str, ...]:
        # --ignore-user-config removes global behavioral defaults; direct -c
        # overrides then define all security-relevant settings for this run.
        return (
            self.binary,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--disable",
            "multi_agent",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "hooks",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "-c",
            'approval_policy="never"',
            "-c",
            "sandbox_workspace_write.network_access=false",
            "-c",
            "sandbox_workspace_write.writable_roots=[]",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            'shell_environment_policy.set={PATH="/usr/local/bin:/usr/bin:/bin",LANG="C.UTF-8",LC_ALL="C.UTF-8"}',
            "-c",
            "allow_login_shell=false",
            "-c",
            'web_search="disabled"',
            "-c",
            "mcp_servers={}",
            "-c",
            "apps={}",
            "-C",
            str(workspace),
            "-",
        )

    def execute(
        self, prompt: str, workspace: Path, timeout_seconds: int
    ) -> CodexExecutionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not workspace.is_dir():
            raise CodexError("WORKSPACE_MISSING", f"workspace missing: {workspace}")
        try:
            process = subprocess.Popen(
                self.command(workspace),
                cwd=workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                start_new_session=True,
                env=codex_process_environment(),
            )
        except OSError as exc:
            raise CodexError("CODEX_START_FAILED", f"cannot start Codex: {exc}") from exc

        timed_out = False
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_group(process)
            try:
                stdout, stderr = process.communicate(
                    timeout=self.termination_grace_seconds
                )
            except subprocess.TimeoutExpired:
                self._kill_process_group(process)
                stdout, stderr = process.communicate()
        final_message, protocol_error = _final_message_and_protocol_error(stdout)
        return CodexExecutionResult(
            stdout_jsonl=stdout,
            stderr=stderr,
            final_message=final_message,
            exit_code=process.returncode,
            timed_out=timed_out,
            protocol_error=protocol_error,
        )

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
