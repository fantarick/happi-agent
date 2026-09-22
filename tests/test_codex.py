from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from happi_agent.codex import (
    AppServerCodexExecutor,
    CodexError,
    PERMISSION_PROFILE,
)


FAKE_SERVER = r'''#!/usr/bin/python3
import json
import os
import subprocess
import sys
import time

SCENARIO = __SCENARIO__
if "--version" in sys.argv:
    print("codex-cli 0.155.0" if SCENARIO == "wrong_version" else "codex-cli 0.154.0")
    raise SystemExit(0)

record = open("requests.jsonl", "a", encoding="utf-8")
def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    record.write(json.dumps(request, sort_keys=True) + "\n")
    record.flush()
    if "id" not in request:
        continue
    request_id = request["id"]
    method = request.get("method")
    if method == "initialize":
        if SCENARIO == "experimental_unsupported":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "experimental API unsupported"}})
        elif request.get("params", {}).get("capabilities", {}).get("experimentalApi") is not True:
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -1, "message": "experimentalApi required"}})
        else:
            emit({"jsonrpc": "2.0", "id": request_id, "result": {"serverInfo": {"name": "fake"}}})
    elif method == "permissionProfile/list":
        if SCENARIO == "profile_method_unsupported":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
            continue
        if SCENARIO == "malformed_json":
            print("{", flush=True)
            continue
        data = [] if SCENARIO == "profile_missing" else [{"id": "happi-workspace-only", "allowed": SCENARIO != "profile_not_allowed"}]
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"data": data, "nextCursor": None}})
    elif method == "thread/start":
        active = {
            "id": "wrong-profile" if SCENARIO == "active_profile_wrong" else "happi-workspace-only",
            "extends": ":danger-full-access" if SCENARIO == "active_profile_parent" else None,
        }
        sources = ["/outside/AGENTS.md"] if SCENARIO == "instruction_sources" else []
        roots = ["/unexpected"] if SCENARIO == "additional_root" else []
        result = {
            "activePermissionProfile": active,
            "approvalPolicy": "never",
            "cwd": request["params"].get("cwd"),
            "runtimeWorkspaceRoots": roots,
            "sandbox": {
                "type": "workspaceWrite" if SCENARIO == "legacy_sandbox" else "readOnly",
                "networkAccess": SCENARIO == "network_enabled",
            },
            "multiAgentMode": "proactive" if SCENARIO == "proactive_multi_agent" else "explicitRequestOnly",
            "thread": {"id": "thread-1"}
        }
        if SCENARIO != "instruction_sources_missing":
            result["instructionSources"] = sources
        if SCENARIO == "sandbox_policy":
            result["sandboxPolicy"] = {"type": "workspaceWrite"}
        emit({"jsonrpc": "2.0", "id": request_id, "result": result})
    elif method == "turn/start":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"turn": {"id": "turn-1"}}})
        if SCENARIO == "timeout":
            child = subprocess.Popen(["sleep", "60"])
            open("child.pid", "w", encoding="utf-8").write(str(child.pid))
            time.sleep(60)
            continue
        if SCENARIO == "error_event":
            emit({"jsonrpc": "2.0", "method": "error", "params": {"message": "synthetic"}})
            continue
        if SCENARIO == "server_request":
            emit({"jsonrpc": "2.0", "id": 99, "method": "item/commandExecution/requestApproval", "params": {}})
            continue
        emit({"jsonrpc": "2.0", "method": "item/completed", "params": {"item": {
            "id": "command-1", "type": "commandExecution", "command": "printf ok",
            "status": "completed", "exitCode": 0, "aggregatedOutput": "COMMAND_OK\n"
        }}})
        if SCENARIO != "missing_message":
            emit({"jsonrpc": "2.0", "method": "item/completed", "params": {"item": {
                "id": "message-1", "type": "agentMessage", "text": "finished"
            }}})
        status = "failed" if SCENARIO == "failed_turn" else "completed"
        emit({"jsonrpc": "2.0", "method": "turn/completed", "params": {
            "turn": {"id": "turn-1", "status": status}
        }})
    elif method == "turn/interrupt":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
record.close()
'''


class AppServerFixture:
    def __init__(self, scenario: str = "ok"):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.binary = self.bundle / "bin" / "codex"
        for directory in (
            self.bundle / "bin",
            self.bundle / "codex-path",
            self.bundle / "codex-resources" / "zsh" / "bin",
        ):
            directory.mkdir(parents=True, mode=0o755)
        source = FAKE_SERVER.replace("__SCENARIO__", repr(scenario))
        self.binary.write_text(source, encoding="utf-8")
        self.binary.chmod(0o755)
        (self.bundle / "codex").symlink_to("bin/codex")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        self.config = self.codex_home / "config.toml"
        self.credential_store = self.codex_home
        self.git_dir = self.root / "canonical" / ".git"
        self.git_dir.mkdir(parents=True)
        config_text = f'''forced_login_method = "chatgpt"
approval_policy = "never"
allow_login_shell = false
web_search = "disabled"
project_doc_max_bytes = 0
project_doc_fallback_filenames = []
default_permissions = "{PERMISSION_PROFILE}"
mcp_servers = {{}}
apps = {{}}

[analytics]
enabled = false

[shell_environment_policy]
inherit = "none"
set = {{ PATH = "/usr/bin:/bin", LANG = "C.UTF-8", LC_ALL = "C.UTF-8", GIT_OPTIONAL_LOCKS = "0" }}

[features]
multi_agent = false
apps = false
plugins = false
hooks = false
browser_use = false
computer_use = false
image_generation = false
in_app_browser = false

[permissions.{PERMISSION_PROFILE}.filesystem]
":root" = "deny"
":minimal" = "read"
":tmpdir" = "deny"
":slash_tmp" = "deny"
"{self.binary.parent}" = "read"
"{self.git_dir}" = "read"
"{self.credential_store}" = "deny"

[permissions.{PERMISSION_PROFILE}.filesystem.":workspace_roots"]
"." = "write"

[permissions.{PERMISSION_PROFILE}.network]
enabled = false
'''
        self.config.write_text(config_text, encoding="utf-8")
        self.config.chmod(0o644)
        binary_hash = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        config_hash = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.executor = AppServerCodexExecutor(
            self.binary,
            self.config,
            credential_store=self.credential_store,
            canonical_git_dir=self.git_dir,
            expected_bundle_files={"bin/codex": (binary_hash, 0o755)},
            expected_config_sha256=config_hash,
            expected_owner_uid=os.geteuid(),
            termination_grace_seconds=1,
        )

    def execute(self, timeout: int = 5):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}, clear=False):
            return self.executor.execute("test prompt", self.workspace, timeout)

    def requests(self) -> list[dict[str, object]]:
        path = self.workspace / "requests.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def close(self) -> None:
        self.temporary.cleanup()


class AppServerExecutorTests(unittest.TestCase):
    def fixture(self, scenario: str = "ok") -> AppServerFixture:
        fixture = AppServerFixture(scenario)
        self.addCleanup(fixture.close)
        return fixture

    def test_handshake_requires_experimental_api(self) -> None:
        fixture = self.fixture()
        result = fixture.execute()
        self.assertIsNone(result.protocol_error)
        initialize = fixture.requests()[0]
        self.assertEqual(initialize["method"], "initialize")
        self.assertIs(initialize["params"]["capabilities"]["experimentalApi"], True)
        self.assertEqual(fixture.requests()[1]["method"], "initialized")

    def test_fails_when_experimental_api_is_not_supported(self) -> None:
        result = self.fixture("experimental_unsupported").execute()
        self.assertIn("experimental API unsupported", str(result.protocol_error))

    def test_permission_profile_list_uses_real_cwd(self) -> None:
        fixture = self.fixture()
        fixture.execute()
        request = next(item for item in fixture.requests() if item.get("method") == "permissionProfile/list")
        self.assertEqual(request["params"]["cwd"], str(fixture.workspace.resolve()))

    def test_missing_profile_fails_closed(self) -> None:
        result = self.fixture("profile_missing").execute()
        self.assertIn("is missing", str(result.protocol_error))

    def test_unsupported_permission_profile_method_fails_closed(self) -> None:
        result = self.fixture("profile_method_unsupported").execute()
        self.assertIn("method not found", str(result.protocol_error))

    def test_not_allowed_profile_fails_closed(self) -> None:
        result = self.fixture("profile_not_allowed").execute()
        self.assertIn("is not allowed", str(result.protocol_error))

    def test_legacy_write_sandbox_fails_closed(self) -> None:
        result = self.fixture("legacy_sandbox").execute()
        self.assertIn("legacy or network-enabled sandbox", str(result.protocol_error))

    def test_wrong_active_profile_fails_closed(self) -> None:
        result = self.fixture("active_profile_wrong").execute()
        self.assertIn("active permission profile mismatch", str(result.protocol_error))

    def test_active_profile_parent_fails_closed(self) -> None:
        result = self.fixture("active_profile_parent").execute()
        self.assertIn("active permission profile has a parent", str(result.protocol_error))

    def test_legacy_sandbox_policy_field_fails_closed(self) -> None:
        result = self.fixture("sandbox_policy").execute()
        self.assertIn("legacy sandboxPolicy", str(result.protocol_error))

    def test_network_enabled_sandbox_summary_fails_closed(self) -> None:
        result = self.fixture("network_enabled").execute()
        self.assertIn("network-enabled sandbox", str(result.protocol_error))

    def test_proactive_multi_agent_mode_fails_closed(self) -> None:
        result = self.fixture("proactive_multi_agent").execute()
        self.assertIn("multi-agent mode", str(result.protocol_error))

    def test_additional_runtime_root_fails_closed(self) -> None:
        result = self.fixture("additional_root").execute()
        self.assertIn("additional runtime workspace roots", str(result.protocol_error))

    def test_instruction_sources_block_before_turn_start(self) -> None:
        fixture = self.fixture("instruction_sources")
        result = fixture.execute()
        self.assertIn("instruction sources are forbidden", str(result.protocol_error))
        self.assertNotIn("turn/start", [item.get("method") for item in fixture.requests()])

    def test_missing_instruction_sources_blocks_before_turn_start(self) -> None:
        fixture = self.fixture("instruction_sources_missing")
        result = fixture.execute()
        self.assertIn("instruction sources are forbidden", str(result.protocol_error))
        self.assertNotIn("turn/start", [item.get("method") for item in fixture.requests()])

    def test_parses_command_execution_agent_message_and_completion(self) -> None:
        result = self.fixture().execute()
        self.assertEqual(result.final_message, "finished")
        self.assertEqual(result.turn_status, "completed")
        self.assertEqual(result.command_executions[0]["exitCode"], 0)
        self.assertEqual(result.command_executions[0]["aggregatedOutput"], "COMMAND_OK\n")
        self.assertEqual(result.active_permission_profile, PERMISSION_PROFILE)

    def test_missing_agent_message_is_protocol_error(self) -> None:
        result = self.fixture("missing_message").execute()
        self.assertIn("without a final agent message", str(result.protocol_error))

    def test_failed_turn_is_protocol_error(self) -> None:
        result = self.fixture("failed_turn").execute()
        self.assertIn("status failed", str(result.protocol_error))

    def test_error_event_is_protocol_error(self) -> None:
        result = self.fixture("error_event").execute()
        self.assertIn("App Server emitted error", str(result.protocol_error))

    def test_server_request_is_protocol_error(self) -> None:
        result = self.fixture("server_request").execute()
        self.assertIn("unexpected server request", str(result.protocol_error))

    def test_malformed_json_is_protocol_error(self) -> None:
        result = self.fixture("malformed_json").execute()
        self.assertIn("malformed JSON", str(result.protocol_error))

    def test_timeout_terminates_process_group(self) -> None:
        fixture = self.fixture("timeout")
        result = fixture.execute(timeout=1)
        self.assertTrue(result.timed_out)
        child_pid = int((fixture.workspace / "child.pid").read_text(encoding="utf-8"))
        time.sleep(0.1)
        status = Path(f"/proc/{child_pid}/stat")
        if status.exists():
            self.assertEqual(status.read_text().split()[2], "Z")

    def test_requests_never_select_legacy_sandbox(self) -> None:
        fixture = self.fixture()
        fixture.execute()
        command = fixture.executor.command()
        self.assertNotIn("--sandbox", command)
        self.assertFalse(any("sandbox_mode" in value for value in command))
        self.assertFalse(any("sandbox_workspace_write" in value for value in command))
        for request in fixture.requests():
            params = request.get("params", {})
            if isinstance(params, dict):
                self.assertNotIn("sandbox", params)
                self.assertNotIn("sandboxPolicy", params)

    def test_profile_disables_network_and_scopes_filesystem(self) -> None:
        fixture = self.fixture()
        profile_override = fixture.executor._permission_profile_override()
        self.assertIn('network={enabled=false}', profile_override)
        self.assertIn(f'"{fixture.credential_store}"="deny"', profile_override)
        self.assertIn(f'"{fixture.git_dir}"="read"', profile_override)
        self.assertIn('":workspace_roots"={"."="write"}', profile_override)
        parsed = tomllib.loads(fixture.config.read_text(encoding="utf-8"))
        profile = parsed["permissions"][PERMISSION_PROFILE]
        self.assertIs(profile["network"]["enabled"], False)
        self.assertEqual(profile["filesystem"][str(fixture.credential_store)], "deny")
        self.assertEqual(profile["filesystem"][str(fixture.git_dir)], "read")

    def test_workspace_root_is_dynamic_and_exact(self) -> None:
        fixture = self.fixture()
        fixture.execute()
        thread = next(item for item in fixture.requests() if item.get("method") == "thread/start")
        turn = next(item for item in fixture.requests() if item.get("method") == "turn/start")
        self.assertEqual(thread["params"]["cwd"], str(fixture.workspace.resolve()))
        self.assertEqual(thread["params"]["permissions"], PERMISSION_PROFILE)
        self.assertEqual(turn["params"]["permissions"], PERMISSION_PROFILE)
        self.assertNotIn("runtimeWorkspaceRoots", thread["params"])
        self.assertNotIn("runtimeWorkspaceRoots", turn["params"])
        self.assertIn('":workspace_roots"={"."="write"}', fixture.executor._permission_profile_override())

    def test_wrong_version_is_rejected(self) -> None:
        fixture = self.fixture("wrong_version")
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "UNSUPPORTED_CODEX_VERSION")

    def test_bundle_hash_mismatch_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture.binary.write_text("changed\n", encoding="utf-8")
        fixture.binary.chmod(0o755)
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_BUNDLE_INVALID")

    def test_missing_bundle_helper_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture.executor.expected_bundle_files["codex-resources/bwrap"] = ("0" * 64, 0o755)
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_BUNDLE_INVALID")

    def test_config_hash_mismatch_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture.config.write_text(
            fixture.config.read_text(encoding="utf-8") + "# changed\n",
            encoding="utf-8",
        )
        fixture.config.chmod(0o644)
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_CONFIG_INVALID")

    def test_bundle_directory_mode_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture.bundle.chmod(0o775)
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_BUNDLE_INVALID")

    def test_enabled_analytics_is_rejected(self) -> None:
        fixture = self.fixture()
        original = fixture.config.read_text(encoding="utf-8")
        fixture.config.write_text(
            original.replace("enabled = false\n\n[shell_environment_policy]", "enabled = true\n\n[shell_environment_policy]"),
            encoding="utf-8",
        )
        fixture.executor.expected_config_sha256 = hashlib.sha256(
            fixture.config.read_bytes()
        ).hexdigest()
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_CONFIG_INVALID")

    def test_legacy_sandbox_in_config_is_rejected(self) -> None:
        fixture = self.fixture()
        fixture.config.write_text(
            fixture.config.read_text(encoding="utf-8")
            + 'sandbox_mode = "workspace-write"\n',
            encoding="utf-8",
        )
        fixture.executor.expected_config_sha256 = hashlib.sha256(
            fixture.config.read_bytes()
        ).hexdigest()
        with patch.dict(os.environ, {"CODEX_HOME": str(fixture.codex_home)}, clear=False):
            with self.assertRaises(CodexError) as context:
                fixture.executor.version()
        self.assertEqual(context.exception.code, "CODEX_CONFIG_INVALID")

    def test_canary_result_parser_requires_real_command_evidence(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "credential_read_canary.py"
        spec = importlib.util.spec_from_file_location("credential_canary", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        execution = type("Execution", (), {"command_executions": ({
            "command": module.CANARY_SHELL_COMMAND,
            "status": "completed",
            "exitCode": 0,
            "aggregatedOutput": "CANARY_DENIED\n",
        },)})()
        self.assertEqual(module._command_execution_result(execution), "CANARY_DENIED")
        execution.command_executions[0]["aggregatedOutput"] = "CANARY_READABLE\n"
        self.assertEqual(module._command_execution_result(execution), "CANARY_READABLE")
        execution.command_executions[0]["exitCode"] = 1
        self.assertIsNone(module._command_execution_result(execution))
        execution.command_executions[0]["exitCode"] = 0
        execution.command_executions[0]["command"] += "\n/usr/bin/id"
        self.assertIsNone(module._command_execution_result(execution))
        execution.command_executions = ()
        self.assertIsNone(module._command_execution_result(execution))

    def test_canary_result_file_parser_is_exact(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "credential_read_canary.py"
        spec = importlib.util.spec_from_file_location("credential_canary_file", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / "result"
            result.write_bytes(b"CANARY_DENIED\n")
            self.assertEqual(module._result_file_value(result), "CANARY_DENIED")
            result.write_bytes(b"CANARY_DENIED\n\n")
            self.assertIsNone(module._result_file_value(result))
            result.unlink()
            target = root / "target"
            target.write_bytes(b"CANARY_DENIED\n")
            result.symlink_to(target)
            self.assertIsNone(module._result_file_value(result))


if __name__ == "__main__":
    unittest.main()
