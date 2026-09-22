from __future__ import annotations

import importlib.util
import unittest

from happi_agent.cli import _parser
from happi_agent.models import AppConfig


class CliBoundaryTests(unittest.TestCase):
    def test_workflow_surface_is_available(self) -> None:
        args = _parser().parse_args(["workflow", "next", "example"])
        self.assertEqual(args.command, "workflow")
        self.assertEqual(args.workflow_command, "next")
        self.assertEqual(args.workflow_id, "example")

    def test_unattended_run_command_is_absent(self) -> None:
        with self.assertRaises(SystemExit):
            _parser().parse_args(["run", "machine-audit-happi"])

    def test_direct_codex_executor_module_is_absent(self) -> None:
        self.assertIsNone(importlib.util.find_spec("happi_agent.codex"))
        self.assertIsNone(importlib.util.find_spec("happi_agent.runner"))

    def test_app_config_has_no_model_credential_or_binary_surface(self) -> None:
        fields = set(AppConfig.__dataclass_fields__)
        self.assertNotIn("codex_binary", fields)
        self.assertNotIn("codex_home", fields)
        self.assertEqual(
            fields,
            {
                "state_dir",
                "worktree_root",
                "canonical_repo",
                "policies_dir",
                "lock_file",
                "kill_switch",
            },
        )


if __name__ == "__main__":
    unittest.main()
