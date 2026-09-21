from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from happi_agent.codex import (
    SubprocessCodexExecutor,
    _final_message_and_protocol_error,
    _tool_host_protocol_error,
)


class CodexExecutorTests(unittest.TestCase):
    def test_jsonl_protocol_requires_completion_and_final_message(self) -> None:
        message, error = _final_message_and_protocol_error(
            '{"type":"turn.started"}\n'
        )
        self.assertEqual(message, "")
        self.assertIn("turn.completed", str(error))

    def test_command_has_explicit_fail_closed_settings(self) -> None:
        command = SubprocessCodexExecutor().command(Path("/tmp/worktree"))
        joined = " ".join(command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--json", command)
        self.assertIn("workspace-write", command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertIn('shell_environment_policy.inherit="none"', command)
        self.assertIn(
            'shell_environment_policy.set={PATH="/usr/local/bin:/usr/bin:/bin",LANG="C.UTF-8",LC_ALL="C.UTF-8"}',
            command,
        )
        self.assertNotIn('shell_environment_policy.inherit="core"', command)
        self.assertFalse(any("include_only" in value for value in command))
        self.assertNotIn("--full-auto", command)
        self.assertIn("multi_agent", joined)

    def test_tool_host_failure_is_a_protocol_error_even_with_zero_exit(self) -> None:
        self.assertEqual(
            _tool_host_protocol_error(
                "Command could not run: tool host unavailable.", ""
            ),
            "Codex tool host unavailable",
        )
        self.assertEqual(
            _tool_host_protocol_error(
                "",
                "ERROR failed to spawn code-mode host "
                "/usr/local/bin/codex-code-mode-host: No such file or directory",
            ),
            "Codex tool host unavailable",
        )
        self.assertIsNone(_tool_host_protocol_error("normal response", ""))

    def test_execute_rejects_zero_exit_with_missing_tool_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "fake-codex"
            binary.write_text(
                """#!/usr/bin/python3
import json
import sys
print(json.dumps({'type': 'turn.started'}))
print(json.dumps({'type': 'item.completed', 'item': {
    'type': 'agent_message',
    'text': 'Command could not run: tool host unavailable.',
}}))
print(json.dumps({'type': 'turn.completed'}))
print('ERROR failed to spawn code-mode host: No such file', file=sys.stderr)
""",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = {"PATH": f"{root}:{os.environ.get('PATH', '')}"}
            with patch.dict(os.environ, environment, clear=False):
                result = SubprocessCodexExecutor("fake-codex").execute(
                    "prompt", workspace, timeout_seconds=5
                )
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.protocol_error, "Codex tool host unavailable")

    def test_real_timeout_terminates_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "fake-codex"
            binary.write_text(
                """#!/usr/bin/python3
import json
import os
import subprocess
import sys
import time
if '--version' in sys.argv:
    print('fake-codex 1')
    raise SystemExit(0)
child = subprocess.Popen(['sleep', '60'])
open('child.pid', 'w', encoding='utf-8').write(str(child.pid))
print(json.dumps({'type': 'turn.started'}), flush=True)
time.sleep(60)
""",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = {"PATH": f"{root}:{os.environ.get('PATH', '')}"}
            with patch.dict(os.environ, environment, clear=False):
                result = SubprocessCodexExecutor(
                    "fake-codex", termination_grace_seconds=1
                ).execute("prompt", workspace, timeout_seconds=1)
            self.assertTrue(result.timed_out)
            child_pid = int((workspace / "child.pid").read_text())
            time.sleep(0.1)
            proc_stat = Path(f"/proc/{child_pid}/stat")
            if proc_stat.exists():
                state = proc_stat.read_text().split()[2]
                self.assertEqual(state, "Z", "child process still running after timeout")


if __name__ == "__main__":
    unittest.main()
