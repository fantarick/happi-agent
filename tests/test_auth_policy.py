from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from happi_agent.codex import SubprocessCodexExecutor
from happi_agent.security import codex_process_environment


class AuthenticationPolicyTests(unittest.TestCase):
    def test_codex_environment_does_not_forward_api_credentials(self) -> None:
        source = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/var/lib/happi-agent",
            "CODEX_HOME": "/var/lib/happi-agent/codex",
            "OPENAI_API_KEY": "must-not-leak",
            "CODEX_API_KEY": "must-not-leak",
            "CODEX_ACCESS_TOKEN": "must-not-leak",
        }
        with patch.dict(os.environ, source, clear=True):
            environment = codex_process_environment()

        self.assertEqual(
            environment.get("CODEX_HOME"), "/var/lib/happi-agent/codex"
        )
        self.assertEqual(environment.get("HOME"), "/var/lib/happi-agent")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CODEX_API_KEY", environment)
        self.assertNotIn("CODEX_ACCESS_TOKEN", environment)

    def test_codex_command_forces_chatgpt_login(self) -> None:
        command = SubprocessCodexExecutor().command(Path("/tmp/worktree"))
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertNotIn("--with-api-key", command)
        self.assertNotIn("--with-access-token", command)


if __name__ == "__main__":
    unittest.main()
