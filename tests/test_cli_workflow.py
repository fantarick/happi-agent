from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import ProjectFixture


class CliWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()
        self.config = self.fixture.root / "config.toml"
        self.config.write_text(
            "\n".join(
                [
                    f'state_dir = "{self.fixture.app.state_dir}"',
                    f'worktree_root = "{self.fixture.app.worktree_root}"',
                    f'canonical_repo = "{self.fixture.app.canonical_repo}"',
                    f'policies_dir = "{self.fixture.app.policies_dir}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.env = os.environ.copy()
        self.env["HAPPI_AGENT_CONFIG"] = str(self.config)

    def tearDown(self) -> None:
        self.fixture.close()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            (sys.executable, "-m", "happi_agent", *args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
            check=False,
        )
        if completed.returncode != expected:
            raise AssertionError(
                f"CLI returned {completed.returncode}, expected {expected}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return completed

    def write_json(self, name: str, value: object) -> Path:
        path = self.fixture.root / name
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return path

    def test_cli_flow_stops_at_human_merge_gate(self) -> None:
        intent = self.fixture.root / "intent.txt"
        intent.write_text("bounded integration intent\n", encoding="utf-8")
        discovery = self.fixture.root / "discovery.txt"
        discovery.write_text("repository inspected\n", encoding="utf-8")

        contract = self.write_json(
            "contract.json",
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "feature_contract",
                "status": "APPROVED",
                "objective": "Update the audit document.",
                "why": "CLI integration test.",
                "non_goals": [],
                "acceptance_criteria": [
                    {"id": "AC1", "text": "Audit document is updated."}
                ],
                "invariants": ["Human merge remains required."],
                "verification_plan": [
                    {"criterion": "AC1", "verification": "deterministic validator"}
                ],
                "human_decisions_reserved": ["merge"],
                "escalation_conditions": ["iteration limit reached"],
            },
        )

        self.run_cli("workflow", "start", "cli-e2e", "--intent", str(intent))
        self.run_cli(
            "workflow",
            "discovery-complete",
            "cli-e2e",
            "--evidence",
            str(discovery),
        )
        self.run_cli("workflow", "contract", "cli-e2e", str(contract))

        status = json.loads(
            self.run_cli("workflow", "status", "cli-e2e").stdout
        )
        self.assertEqual(status["state"], "ENGINEER_REQUIRED")
        workspace = Path(status["workspace_path"])
        audit = workspace / "docs" / "audit.md"
        audit.write_text(
            audit.read_text(encoding="utf-8") + "integration update\n",
            encoding="utf-8",
        )

        handoff = self.write_json(
            "engineer-handoff.json",
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "engineer_handoff",
                "status": "READY_FOR_REVIEW",
                "iteration": 1,
                "repository_state": {
                    "branch": "detached-worktree",
                    "base_commit": status["base_commit"],
                    "head_commit": status["base_commit"],
                },
                "changes": ["updated docs/audit.md"],
                "changed_files": ["docs/audit.md"],
                "verification": [
                    {
                        "check": "engineer-local-check",
                        "result": "PASS",
                        "evidence": "integration fixture",
                    }
                ],
                "deviations": [],
                "known_uncertainty": [],
            },
        )
        self.run_cli(
            "workflow", "engineer-handoff", "cli-e2e", str(handoff)
        )
        verified = json.loads(
            self.run_cli(
                "workflow",
                "verify",
                "cli-e2e",
                "--policy",
                "test-policy",
            ).stdout
        )
        self.assertTrue(verified["validation_ok"])
        self.assertEqual(verified["state"], "REVIEW_REQUIRED")

        review = self.write_json(
            "review.json",
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "architect_review",
                "verdict": "APPROVE",
                "acceptance_criteria": [
                    {"id": "AC1", "result": "PASS", "evidence": "validator"}
                ],
                "blocking_findings": [],
                "non_blocking_findings": [],
                "residual_uncertainty": [],
            },
        )
        reviewed = json.loads(
            self.run_cli("workflow", "review", "cli-e2e", str(review)).stdout
        )
        self.assertEqual(reviewed["state"], "HUMAN_MERGE_REQUIRED")
        self.assertEqual(reviewed["next_action"], "human_merge")

        final_status = json.loads(
            self.run_cli("workflow", "status", "cli-e2e").stdout
        )
        event_types = [event["event_type"] for event in final_status["events"]]
        self.assertIn("ENGINEER_HANDOFF_ACCEPTED", event_types)
        self.assertIn("VERIFICATION_PASSED", event_types)
        self.assertIn("REVIEW_APPROVED", event_types)


if __name__ == "__main__":
    unittest.main()
