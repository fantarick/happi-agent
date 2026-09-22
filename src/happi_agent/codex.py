from __future__ import annotations

import json
import os
import queue
import signal
import stat
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Protocol

from happi_agent.models import CodexExecutionResult
from happi_agent.security import codex_process_environment, sha256_file


class CodexError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


SUPPORTED_CODEX_VERSION = "codex-cli 0.154.0"
PERMISSION_PROFILE = "happi-workspace-only"
PRODUCTION_CODEX_HOME = Path("/var/lib/happi-agent/codex")
PRODUCTION_GIT_DIR = Path("/srv/machine-audits/.git")
PRODUCTION_CONFIG_SHA256 = (
    "b8d0529fd2968aca8e65a19bbf213b843b1de44b92d95fcf02f95222fd3adc0f"
)

STANDALONE_BUNDLE_FILES: dict[str, tuple[str, int]] = {
    "bin/codex": (
        "9b7c1c7abdc26fc3c4f47c77656a8e9121def5483dbae830ef1ee561758448a9",
        0o755,
    ),
    "bin/codex-code-mode-host": (
        "f31e1c5ffbbca7884aff2f0f8795d3da197f4aafb114033a399dfc17a5119031",
        0o755,
    ),
    "codex-package.json": (
        "abfae2ff248420c32f531f3ba0cb83ea27bb9ba9355872658b591e1939a60288",
        0o644,
    ),
    "codex-path/rg": (
        "e36d0eb52e70696bdf1781392722e05a21bb91d3b7b762ef5ec20e5df2ec687b",
        0o755,
    ),
    "codex-resources/bwrap": (
        "58bd88f39d02a0b5ac553c2f334edfff1ec74afb9b8f4233dfc5b69225038f92",
        0o755,
    ),
    "codex-resources/zsh/bin/zsh": (
        "7feeacd883e1dc749847936948c378653c80a69ec4a9542f0f126b411882c179",
        0o755,
    ),
}


class CodexExecutor(Protocol):
    def version(self) -> str: ...

    def execute(
        self, prompt: str, workspace: Path, timeout_seconds: int
    ) -> CodexExecutionResult: ...


class _AppServerProtocolError(RuntimeError):
    pass


class _TurnTimeout(TimeoutError):
    def __init__(self, turn_id: str):
        super().__init__("App Server turn deadline exceeded")
        self.turn_id = turn_id


class _JsonRpcSession:
    def __init__(self, process: subprocess.Popen[str]):
        self.process = process
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.notifications: list[dict[str, Any]] = []
        self._messages: queue.Queue[str | None] = queue.Queue()
        self._responses: dict[int, dict[str, Any]] = {}
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="happi-app-server-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="happi-app-server-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.stdout_lines.append(line)
                self._messages.put(line)
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line)

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def send_request_without_waiting(
        self, method: str, params: dict[str, Any]
    ) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        return request_id

    def request(
        self, method: str, params: dict[str, Any], deadline: float
    ) -> dict[str, Any]:
        request_id = self.send_request_without_waiting(method, params)
        return self.wait_response(request_id, deadline)

    def wait_response(self, request_id: int, deadline: float) -> dict[str, Any]:
        while request_id not in self._responses:
            self.read_one(deadline)
        response = self._responses.pop(request_id)
        if "error" in response:
            raise _AppServerProtocolError(
                f"App Server request {request_id} failed: "
                f"{json.dumps(response['error'], sort_keys=True)}"
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise _AppServerProtocolError(
                f"App Server response {request_id} has no object result"
            )
        return result

    def read_one(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("App Server response deadline exceeded")
        try:
            line = self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("App Server response deadline exceeded") from exc
        if line is None:
            raise _AppServerProtocolError("App Server closed stdout unexpectedly")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _AppServerProtocolError("App Server emitted malformed JSON") from exc
        if not isinstance(message, dict):
            raise _AppServerProtocolError("App Server emitted a non-object JSON message")
        if "method" in message:
            if not isinstance(message.get("method"), str):
                raise _AppServerProtocolError("App Server emitted an invalid method")
            if "id" in message:
                raise _AppServerProtocolError(
                    f"unexpected server request: {message['method']}"
                )
            self.notifications.append(message)
        elif "id" in message:
            response_id = message.get("id")
            if not isinstance(response_id, int):
                raise _AppServerProtocolError("App Server emitted an invalid response id")
            if response_id in self._responses:
                raise _AppServerProtocolError("App Server emitted a duplicate response")
            self._responses[response_id] = message
        else:
            raise _AppServerProtocolError("App Server emitted an unknown JSON-RPC message")
        return message

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        try:
            with self._write_lock:
                self.process.stdin.write(encoded + "\n")
                self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise _AppServerProtocolError("cannot write to App Server") from exc

    def stdout_text(self) -> str:
        return "".join(self.stdout_lines)

    def stderr_text(self) -> str:
        return "".join(self.stderr_lines)

    def join_readers(self) -> None:
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()


class AppServerCodexExecutor:
    """One fail-closed Codex App Server process per job."""

    def __init__(
        self,
        binary: str | Path,
        codex_config: Path,
        *,
        permission_profile: str = PERMISSION_PROFILE,
        credential_store: Path = PRODUCTION_CODEX_HOME,
        canonical_git_dir: Path = PRODUCTION_GIT_DIR,
        expected_bundle_files: dict[str, tuple[str, int]] | None = None,
        expected_config_sha256: str = PRODUCTION_CONFIG_SHA256,
        expected_owner_uid: int = 0,
        termination_grace_seconds: int = 5,
    ):
        binary_path = Path(binary)
        if not binary_path.is_absolute():
            raise ValueError("Codex binary must be an absolute path")
        if binary_path != Path(os.path.normpath(str(binary_path))):
            raise ValueError("Codex binary path must be normalized")
        if not codex_config.is_absolute():
            raise ValueError("Codex config path must be absolute")
        if not credential_store.is_absolute() or not canonical_git_dir.is_absolute():
            raise ValueError("credential and canonical Git paths must be absolute")
        self.binary = binary_path
        self.bundle_root = binary_path.parent.parent
        self.codex_config = codex_config
        self.permission_profile = permission_profile
        self.credential_store = credential_store
        self.canonical_git_dir = canonical_git_dir
        self.expected_bundle_files = dict(
            STANDALONE_BUNDLE_FILES
            if expected_bundle_files is None
            else expected_bundle_files
        )
        self.expected_config_sha256 = expected_config_sha256
        self.expected_owner_uid = expected_owner_uid
        self.termination_grace_seconds = termination_grace_seconds

    def _verify_regular_file(
        self,
        path: Path,
        expected_hash: str,
        expected_mode: int,
        *,
        error_code: str = "CODEX_BUNDLE_INVALID",
    ) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise CodexError(
                error_code, f"cannot inspect required file {path}: {exc}"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise CodexError(error_code, f"not a regular file: {path}")
        if metadata.st_uid != self.expected_owner_uid:
            raise CodexError(error_code, f"unexpected owner for {path}")
        if stat.S_IMODE(metadata.st_mode) != expected_mode:
            raise CodexError(error_code, f"unexpected mode for {path}")
        if sha256_file(path) != expected_hash:
            raise CodexError(error_code, f"SHA-256 mismatch for {path}")

    def _verify_bundle(self) -> None:
        expected_binary = self.bundle_root / "bin" / "codex"
        if self.binary != expected_binary:
            raise CodexError(
                "CODEX_BUNDLE_INVALID",
                f"Codex binary must be the bundle entrypoint {expected_binary}",
            )
        trusted_parent = self.bundle_root.parent
        directories = (
            self.bundle_root,
            self.bundle_root / "bin",
            self.bundle_root / "codex-path",
            self.bundle_root / "codex-resources",
            self.bundle_root / "codex-resources" / "zsh",
            self.bundle_root / "codex-resources" / "zsh" / "bin",
        )
        try:
            parent_metadata = trusted_parent.lstat()
        except OSError as exc:
            raise CodexError(
                "CODEX_BUNDLE_INVALID", f"cannot inspect bundle parent: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != self.expected_owner_uid
            or parent_metadata.st_mode & 0o022
        ):
            raise CodexError(
                "CODEX_BUNDLE_INVALID", f"unsafe bundle parent: {trusted_parent}"
            )
        for directory in directories:
            try:
                metadata = directory.lstat()
            except OSError as exc:
                raise CodexError(
                    "CODEX_BUNDLE_INVALID", f"cannot inspect bundle directory: {exc}"
                ) from exc
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != self.expected_owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o755
            ):
                raise CodexError(
                    "CODEX_BUNDLE_INVALID", f"unsafe bundle directory: {directory}"
                )
        for relative, (expected_hash, expected_mode) in self.expected_bundle_files.items():
            self._verify_regular_file(
                self.bundle_root / relative, expected_hash, expected_mode
            )
        allowed_paths = {
            "bin",
            "codex-path",
            "codex-resources",
            "codex-resources/zsh",
            "codex-resources/zsh/bin",
            "codex",
            *self.expected_bundle_files,
        }
        actual_paths = {
            str(path.relative_to(self.bundle_root))
            for path in self.bundle_root.rglob("*")
        }
        if actual_paths != allowed_paths:
            raise CodexError(
                "CODEX_BUNDLE_INVALID", "bundle inventory differs from the pinned release"
            )
        entrypoint = self.bundle_root / "codex"
        try:
            metadata = entrypoint.lstat()
            target = os.readlink(entrypoint)
        except OSError as exc:
            raise CodexError(
                "CODEX_BUNDLE_INVALID", f"cannot inspect bundle entrypoint: {exc}"
            ) from exc
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != self.expected_owner_uid
            or target != "bin/codex"
        ):
            raise CodexError("CODEX_BUNDLE_INVALID", "invalid bundle codex symlink")

    def _verify_config(self) -> None:
        self._verify_regular_file(
            self.codex_config,
            self.expected_config_sha256,
            0o644,
            error_code="CODEX_CONFIG_INVALID",
        )
        environment = codex_process_environment()
        codex_home = environment.get("CODEX_HOME")
        if not codex_home:
            raise CodexError("CODEX_CONFIG_INVALID", "CODEX_HOME is not set")
        expected_location = Path(codex_home) / "config.toml"
        if self.codex_config != expected_location:
            raise CodexError(
                "CODEX_CONFIG_INVALID",
                f"verified config must be loaded from {expected_location}",
            )
        try:
            parsed = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise CodexError("CODEX_CONFIG_INVALID", f"invalid Codex config: {exc}") from exc
        def contains_legacy_key(value: object) -> bool:
            if not isinstance(value, dict):
                return False
            if any(
                key
                in {
                    "sandbox",
                    "sandbox_mode",
                    "sandbox_workspace_write",
                    "sandboxPolicy",
                }
                for key in value
            ):
                return True
            return any(contains_legacy_key(item) for item in value.values())

        if contains_legacy_key(parsed):
            raise CodexError("CODEX_CONFIG_INVALID", "legacy sandbox config is forbidden")
        if parsed.get("default_permissions") != self.permission_profile:
            raise CodexError("CODEX_CONFIG_INVALID", "wrong default permission profile")
        permissions = parsed.get("permissions")
        profile = permissions.get(self.permission_profile) if isinstance(permissions, dict) else None
        filesystem = profile.get("filesystem") if isinstance(profile, dict) else None
        network = profile.get("network") if isinstance(profile, dict) else None
        expected_rules = {
            ":root": "deny",
            ":minimal": "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(self.binary.parent): "read",
            str(self.canonical_git_dir): "read",
            str(self.credential_store): "deny",
        }
        expected_filesystem: dict[str, object] = dict(expected_rules)
        expected_filesystem[":workspace_roots"] = {".": "write"}
        if filesystem != expected_filesystem:
            raise CodexError("CODEX_CONFIG_INVALID", "permission filesystem rules differ")
        if not isinstance(network, dict) or network.get("enabled") is not False:
            raise CodexError("CODEX_CONFIG_INVALID", "tool network must be disabled")
        required_scalars = {
            "forced_login_method": "chatgpt",
            "approval_policy": "never",
            "allow_login_shell": False,
            "web_search": "disabled",
            "project_doc_max_bytes": 0,
            "project_doc_fallback_filenames": [],
            "mcp_servers": {},
            "apps": {},
        }
        if any(parsed.get(key) != value for key, value in required_scalars.items()):
            raise CodexError("CODEX_CONFIG_INVALID", "Codex hardening settings differ")
        shell_environment = parsed.get("shell_environment_policy")
        if not isinstance(shell_environment, dict) or shell_environment.get("inherit") != "none":
            raise CodexError("CODEX_CONFIG_INVALID", "shell environment is not isolated")
        expected_environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        if shell_environment.get("set") != expected_environment:
            raise CodexError("CODEX_CONFIG_INVALID", "shell environment differs")
        features = parsed.get("features")
        disabled_features = {
            "multi_agent",
            "apps",
            "plugins",
            "hooks",
            "browser_use",
            "computer_use",
            "image_generation",
            "in_app_browser",
        }
        if not isinstance(features, dict) or any(
            features.get(feature) is not False for feature in disabled_features
        ):
            raise CodexError("CODEX_CONFIG_INVALID", "a forbidden integration is enabled")
        analytics = parsed.get("analytics")
        if not isinstance(analytics, dict) or analytics.get("enabled") is not False:
            raise CodexError("CODEX_CONFIG_INVALID", "analytics must be disabled")

    def version(self) -> str:
        self._verify_bundle()
        self._verify_config()
        try:
            completed = subprocess.run(
                (str(self.binary), "--version"),
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
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexError("CODEX_UNAVAILABLE", f"cannot execute Codex: {exc}") from exc
        if completed.returncode != 0:
            raise CodexError(
                "CODEX_VERSION_FAILED",
                completed.stderr.strip() or "codex --version failed",
            )
        version = completed.stdout.strip()
        if version != SUPPORTED_CODEX_VERSION:
            raise CodexError(
                "UNSUPPORTED_CODEX_VERSION",
                f"expected {SUPPORTED_CODEX_VERSION}; got {version or '<empty>'}",
            )
        return version

    @staticmethod
    def _toml_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _permission_profile_override(self) -> str:
        rules = {
            ":root": "deny",
            ":minimal": "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(self.binary.parent): "read",
            str(self.canonical_git_dir): "read",
            str(self.credential_store): "deny",
        }
        encoded_rules = ",".join(
            f"{self._toml_string(path)}={self._toml_string(access)}"
            for path, access in rules.items()
        )
        return (
            "{filesystem={"
            + encoded_rules
            + ',":workspace_roots"={"."="write"}},network={enabled=false}}'
        )

    def command(self) -> tuple[str, ...]:
        overrides = (
            f"default_permissions={self._toml_string(self.permission_profile)}",
            "approval_policy=\"never\"",
            "forced_login_method=\"chatgpt\"",
            "allow_login_shell=false",
            "web_search=\"disabled\"",
            "project_doc_max_bytes=0",
            "project_doc_fallback_filenames=[]",
            "mcp_servers={}",
            "apps={}",
            'shell_environment_policy.inherit="none"',
            'shell_environment_policy.set={PATH="/usr/bin:/bin",LANG="C.UTF-8",LC_ALL="C.UTF-8",GIT_OPTIONAL_LOCKS="0"}',
            f"permissions.{self.permission_profile}={self._permission_profile_override()}",
            "features.multi_agent=false",
            "features.apps=false",
            "features.plugins=false",
            "features.hooks=false",
            "features.browser_use=false",
            "features.computer_use=false",
            "features.image_generation=false",
            "features.in_app_browser=false",
        )
        command: list[str] = [
            str(self.binary),
            "app-server",
            "--stdio",
            "--strict-config",
        ]
        for override in overrides:
            command.extend(("-c", override))
        result = tuple(command)
        forbidden = ("--sandbox", "sandbox_mode", "sandbox_workspace_write", "sandboxPolicy")
        if any(any(token in argument for token in forbidden) for argument in result):
            raise CodexError("LEGACY_SANDBOX_FORBIDDEN", "legacy sandbox option in argv")
        return result

    @staticmethod
    def _error_notification(notifications: list[dict[str, Any]]) -> str | None:
        for notification in notifications:
            method = notification.get("method")
            if method == "error" or (isinstance(method, str) and method.endswith("/error")):
                return f"App Server emitted {method}: {json.dumps(notification.get('params'), sort_keys=True)}"
        return None

    def _list_permission_profiles(
        self, session: _JsonRpcSession, workspace: Path, deadline: float
    ) -> None:
        cursor: str | None = None
        found: dict[str, Any] | None = None
        for _ in range(10):
            params: dict[str, Any] = {"cwd": str(workspace)}
            if cursor is not None:
                params["cursor"] = cursor
            result = session.request("permissionProfile/list", params, deadline)
            data = result.get("data")
            if not isinstance(data, list):
                raise _AppServerProtocolError("permissionProfile/list returned invalid data")
            for candidate in data:
                if isinstance(candidate, dict) and candidate.get("id") == self.permission_profile:
                    if found is not None:
                        raise _AppServerProtocolError("duplicate permission profile id")
                    found = candidate
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise _AppServerProtocolError("permissionProfile/list returned invalid cursor")
            cursor = next_cursor
        else:
            raise _AppServerProtocolError("permissionProfile/list pagination did not terminate")
        if found is None:
            raise _AppServerProtocolError(
                f"permission profile {self.permission_profile!r} is missing"
            )
        if found.get("allowed") is not True:
            raise _AppServerProtocolError(
                f"permission profile {self.permission_profile!r} is not allowed"
            )

    def _start_thread(
        self, session: _JsonRpcSession, workspace: Path, deadline: float
    ) -> tuple[str, str]:
        result = session.request(
            "thread/start",
            {
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "permissions": self.permission_profile,
                "ephemeral": True,
                "multiAgentMode": "explicitRequestOnly",
            },
            deadline,
        )
        active = result.get("activePermissionProfile")
        active_id = active.get("id") if isinstance(active, dict) else None
        if active_id != self.permission_profile:
            raise _AppServerProtocolError(
                f"active permission profile mismatch: {active_id!r}"
            )
        if active.get("extends") is not None:
            raise _AppServerProtocolError("active permission profile has a parent")
        sources = result.get("instructionSources")
        if sources != []:
            raise _AppServerProtocolError(
                f"external instruction sources are forbidden: {sources!r}"
            )
        if result.get("cwd") != str(workspace):
            raise _AppServerProtocolError("thread cwd differs from the worktree")
        response_roots = result.get("runtimeWorkspaceRoots")
        if response_roots not in ([], [str(workspace)]):
            raise _AppServerProtocolError("unexpected additional runtime workspace roots")
        if result.get("approvalPolicy") != "never":
            raise _AppServerProtocolError("approval policy is not never")
        if result.get("multiAgentMode") != "explicitRequestOnly":
            raise _AppServerProtocolError("multi-agent mode is not explicitRequestOnly")
        if "sandboxPolicy" in result:
            raise _AppServerProtocolError("legacy sandboxPolicy was selected")
        compatibility_sandbox = result.get("sandbox")
        if not isinstance(compatibility_sandbox, dict):
            raise _AppServerProtocolError("thread/start returned no sandbox summary")
        if (
            compatibility_sandbox.get("type") != "readOnly"
            or compatibility_sandbox.get("networkAccess") is not False
        ):
            raise _AppServerProtocolError(
                "legacy or network-enabled sandbox was selected"
            )
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise _AppServerProtocolError("thread/start returned no thread id")
        return thread_id, active_id

    def _run_turn(
        self,
        session: _JsonRpcSession,
        thread_id: str,
        prompt: str,
        deadline: float,
    ) -> tuple[str, str, tuple[dict[str, Any], ...], str]:
        notification_offset = len(session.notifications)
        result = session.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "permissions": self.permission_profile,
                "approvalPolicy": "never",
            },
            deadline,
        )
        turn = result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise _AppServerProtocolError("turn/start returned no turn id")

        final_message = ""
        command_executions: list[dict[str, Any]] = []
        turn_status: str | None = None
        notification_index = notification_offset
        try:
            while turn_status is None:
                while notification_index >= len(session.notifications):
                    session.read_one(deadline)
                notification = session.notifications[notification_index]
                notification_index += 1
                method = notification.get("method")
                params = notification.get("params")
                if not isinstance(params, dict):
                    params = {}
                if method == "item/completed":
                    item = params.get("item")
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        text = item.get("text")
                        if isinstance(text, str):
                            final_message = text
                    elif isinstance(item, dict) and item.get("type") == "commandExecution":
                        command_executions.append(dict(item))
                elif method == "turn/completed":
                    completed_turn = params.get("turn")
                    if not isinstance(completed_turn, dict):
                        raise _AppServerProtocolError("turn/completed has no turn object")
                    if completed_turn.get("id") != turn_id:
                        raise _AppServerProtocolError("turn/completed id mismatch")
                    status = completed_turn.get("status")
                    if not isinstance(status, str):
                        raise _AppServerProtocolError("turn/completed has no status")
                    turn_status = status
                elif method == "error" or (
                    isinstance(method, str) and method.endswith("/error")
                ):
                    raise _AppServerProtocolError(
                        f"App Server emitted {method}: {json.dumps(params, sort_keys=True)}"
                    )
        except TimeoutError as exc:
            raise _TurnTimeout(turn_id) from exc
        if turn_status != "completed":
            raise _AppServerProtocolError(f"turn completed with status {turn_status}")
        if not final_message:
            raise _AppServerProtocolError("turn completed without a final agent message")
        return final_message, turn_status, tuple(command_executions), turn_id

    def execute(
        self, prompt: str, workspace: Path, timeout_seconds: int
    ) -> CodexExecutionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise CodexError("WORKSPACE_MISSING", f"workspace missing: {workspace}")
        self.version()
        try:
            process = subprocess.Popen(
                self.command(),
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
                bufsize=1,
            )
        except OSError as exc:
            raise CodexError("CODEX_START_FAILED", f"cannot start Codex: {exc}") from exc

        session = _JsonRpcSession(process)
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        protocol_error: str | None = None
        final_message = ""
        active_profile: str | None = None
        turn_status: str | None = None
        command_executions: tuple[dict[str, Any], ...] = ()
        thread_id: str | None = None
        turn_id: str | None = None
        try:
            initialize = session.request(
                "initialize",
                {
                    "clientInfo": {"name": "happi-agent", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
                deadline,
            )
            if not isinstance(initialize, dict):
                raise _AppServerProtocolError("initialize returned invalid result")
            session.send_notification("initialized")
            self._list_permission_profiles(session, workspace, deadline)
            error = self._error_notification(session.notifications)
            if error:
                raise _AppServerProtocolError(error)
            thread_id, active_profile = self._start_thread(session, workspace, deadline)
            error = self._error_notification(session.notifications)
            if error:
                raise _AppServerProtocolError(error)
            final_message, turn_status, command_executions, turn_id = self._run_turn(
                session, thread_id, prompt, deadline
            )
        except TimeoutError as exc:
            timed_out = True
            protocol_error = "App Server execution timed out"
            if isinstance(exc, _TurnTimeout):
                turn_id = exc.turn_id
            if thread_id is not None and turn_id is not None:
                try:
                    session.send_request_without_waiting(
                        "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
                    )
                except _AppServerProtocolError:
                    pass
        except _AppServerProtocolError as exc:
            protocol_error = str(exc)
        finally:
            self._finish_process(process, session, timed_out=timed_out)

        return CodexExecutionResult(
            stdout_jsonl=session.stdout_text(),
            stderr=session.stderr_text(),
            final_message=final_message,
            exit_code=process.returncode,
            timed_out=timed_out,
            protocol_error=protocol_error,
            active_permission_profile=active_profile,
            turn_status=turn_status,
            command_executions=command_executions,
        )

    def _finish_process(
        self,
        process: subprocess.Popen[str],
        session: _JsonRpcSession,
        *,
        timed_out: bool,
    ) -> None:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        wait_before_term = self.termination_grace_seconds if timed_out else 1
        try:
            process.wait(timeout=wait_before_term)
        except subprocess.TimeoutExpired:
            self._terminate_process_group(process)
            try:
                process.wait(timeout=self.termination_grace_seconds)
            except subprocess.TimeoutExpired:
                self._kill_process_group(process)
                process.wait()
        session.join_readers()

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
