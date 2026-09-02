from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from happi_agent.codex import SubprocessCodexExecutor, _final_message_and_protocol_error


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
        self.assertNotIn("--full-auto", command)
        self.assertIn("multi_agent", joined)

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
