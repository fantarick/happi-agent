from __future__ import annotations

import json
import unittest

from happi_agent.config import load_validation_policy
from happi_agent.protocol import WorkflowState
from happi_agent.workflow import WorkflowController, WorkflowError
from tests.helpers import ProjectFixture


class WorkflowControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ProjectFixture()
        self.controller = WorkflowController(self.fixture.app)
        self.controller.initialize()

    def tearDown(self) -> None:
        self.fixture.close()

    @staticmethod
    def contract() -> bytes:
        return json.dumps(
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "feature_contract",
                "status": "APPROVED",
                "objective": "Update the bounded audit document.",
                "why": "Exercise the reference workflow.",
                "non_goals": ["No unrelated refactor."],
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
            sort_keys=True,
        ).encode()

    def engineer_handoff(self, workflow_id: str, iteration: int) -> bytes:
        return json.dumps(
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "engineer_handoff",
                "status": "READY_FOR_REVIEW",
                "iteration": iteration,
                "repository_state": {
                    "branch": "detached-worktree",
                    "base_commit": self.controller.store.base_commit(workflow_id),
                    "head_commit": self.controller.store.base_commit(workflow_id),
                },
                "changes": ["updated docs/audit.md"],
                "changed_files": ["docs/audit.md"],
                "verification": [
                    {
                        "check": "engineer-local-check",
                        "result": "PASS",
                        "evidence": "bounded manual check",
                    }
                ],
                "deviations": [],
                "known_uncertainty": [],
            },
            sort_keys=True,
        ).encode()

    @staticmethod
    def approved_review() -> bytes:
        return json.dumps(
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
            sort_keys=True,
        ).encode()

    @staticmethod
    def request_changes_review() -> bytes:
        return json.dumps(
            {
                "protocol_version": "agentic-dev-playbook/v0.2",
                "type": "architect_review",
                "verdict": "REQUEST_CHANGES",
                "acceptance_criteria": [
                    {"id": "AC1", "result": "FAIL", "evidence": "review finding"}
                ],
                "blocking_findings": ["AC1 is not satisfied."],
                "non_blocking_findings": [],
                "residual_uncertainty": [],
            },
            sort_keys=True,
        ).encode()

    def prepare_engineering(self, workflow_id: str, *, max_iterations: int = 3) -> None:
        state = self.controller.start(
            workflow_id, b"bounded intent\n", max_iterations=max_iterations
        )
        self.assertEqual(state.state, WorkflowState.DISCOVERY_REQUIRED)
        state = self.controller.discovery_complete(
            workflow_id, b"repository discovery evidence\n"
        )
        self.assertEqual(state.state, WorkflowState.CONTRACT_REQUIRED)
        state = self.controller.approve_contract(workflow_id, self.contract())
        self.assertEqual(state.state, WorkflowState.ENGINEER_REQUIRED)

    def test_happy_path_stops_at_human_merge_gate(self) -> None:
        workflow_id = "happy-path"
        self.prepare_engineering(workflow_id)
        workspace = self.controller.store.workspace(workflow_id)
        path = workspace.path / "docs" / "audit.md"
        path.write_text(path.read_text(encoding="utf-8") + "updated\n", encoding="utf-8")

        state = self.controller.ingest_engineer_handoff(
            workflow_id, self.engineer_handoff(workflow_id, 1)
        )
        self.assertEqual(state.state, WorkflowState.VERIFYING)
        self.assertEqual(state.iteration, 1)

        policy = load_validation_policy("test-policy", self.fixture.app)
        state, validation = self.controller.verify(workflow_id, policy)
        self.assertTrue(validation.ok)
        self.assertEqual(state.state, WorkflowState.REVIEW_REQUIRED)

        state = self.controller.ingest_architect_review(
            workflow_id, self.approved_review()
        )
        self.assertEqual(state.state, WorkflowState.HUMAN_MERGE_REQUIRED)
        self.assertEqual(
            self.controller.next_action(workflow_id),
            "human_merge",
        )

        record = self.controller.status(workflow_id)
        artifact_names = {item["name"] for item in record["artifacts"]}
        self.assertIn("intent.txt", artifact_names)
        self.assertIn("contract.json", artifact_names)
        self.assertIn("engineer-handoff-001.json", artifact_names)
        self.assertIn("validation-001.json", artifact_names)
        self.assertIn("architect-review-001.json", artifact_names)

    def test_review_change_at_iteration_limit_escalates(self) -> None:
        workflow_id = "limited"
        self.prepare_engineering(workflow_id, max_iterations=1)
        workspace = self.controller.store.workspace(workflow_id)
        path = workspace.path / "docs" / "audit.md"
        path.write_text(path.read_text(encoding="utf-8") + "updated\n", encoding="utf-8")

        self.controller.ingest_engineer_handoff(
            workflow_id, self.engineer_handoff(workflow_id, 1)
        )
        policy = load_validation_policy("test-policy", self.fixture.app)
        state, validation = self.controller.verify(workflow_id, policy)
        self.assertTrue(validation.ok)
        self.assertEqual(state.state, WorkflowState.REVIEW_REQUIRED)

        state = self.controller.ingest_architect_review(
            workflow_id, self.request_changes_review()
        )
        self.assertEqual(state.state, WorkflowState.ESCALATED)
        self.assertEqual(
            self.controller.next_action(workflow_id),
            "human_decision_required",
        )

    def test_review_must_cover_exact_contract_criteria(self) -> None:
        workflow_id = "review-coverage"
        self.prepare_engineering(workflow_id)
        workspace = self.controller.store.workspace(workflow_id)
        path = workspace.path / "docs" / "audit.md"
        path.write_text(path.read_text(encoding="utf-8") + "updated\n", encoding="utf-8")
        self.controller.ingest_engineer_handoff(
            workflow_id, self.engineer_handoff(workflow_id, 1)
        )
        policy = load_validation_policy("test-policy", self.fixture.app)
        self.controller.verify(workflow_id, policy)

        review = json.loads(self.approved_review().decode())
        review["acceptance_criteria"] = []
        with self.assertRaises(WorkflowError) as caught:
            self.controller.ingest_architect_review(
                workflow_id, json.dumps(review).encode()
            )
        self.assertEqual(caught.exception.code, "REVIEW_CRITERIA_MISMATCH")
        self.assertEqual(
            self.controller.store.snapshot(workflow_id).state,
            WorkflowState.REVIEW_REQUIRED,
        )

    def test_tampered_contract_artifact_blocks_review(self) -> None:
        workflow_id = "tampered-contract"
        self.prepare_engineering(workflow_id)
        workspace = self.controller.store.workspace(workflow_id)
        path = workspace.path / "docs" / "audit.md"
        path.write_text(path.read_text(encoding="utf-8") + "updated\n", encoding="utf-8")
        self.controller.ingest_engineer_handoff(
            workflow_id, self.engineer_handoff(workflow_id, 1)
        )
        policy = load_validation_policy("test-policy", self.fixture.app)
        self.controller.verify(workflow_id, policy)

        record = self.controller.status(workflow_id)
        contract_artifact = next(
            item for item in record["artifacts"] if item["name"] == "contract.json"
        )
        from pathlib import Path
        Path(contract_artifact["path"]).write_text("{}\n", encoding="utf-8")

        with self.assertRaises(WorkflowError) as caught:
            self.controller.ingest_architect_review(
                workflow_id, self.approved_review()
            )
        self.assertEqual(caught.exception.code, "ARTIFACT_INTEGRITY_ERROR")
        self.assertEqual(
            self.controller.store.snapshot(workflow_id).state,
            WorkflowState.REVIEW_REQUIRED,
        )

    def test_workspace_failure_does_not_advance_contract_state(self) -> None:
        workflow_id = "workspace-failure"
        self.controller.start(workflow_id, b"intent\n")
        self.controller.discovery_complete(workflow_id, b"discovery\n")

        class FailingWorkspaceManager:
            def __init__(self, base_commit: str):
                self.base_commit = base_commit

            def preflight(self):
                return self.base_commit, self.fixture_git_dir

            def create(self, workspace_id: str):
                raise RuntimeError("synthetic workspace failure")

        failing = FailingWorkspaceManager(
            self.controller.store.base_commit(workflow_id)
        )
        failing.fixture_git_dir = self.fixture.repo / ".git"
        self.controller.workspaces = failing  # type: ignore[assignment]

        with self.assertRaisesRegex(RuntimeError, "synthetic workspace failure"):
            self.controller.approve_contract(workflow_id, self.contract())
        self.assertEqual(
            self.controller.store.snapshot(workflow_id).state,
            WorkflowState.CONTRACT_REQUIRED,
        )

    def test_iteration_mismatch_fails_closed(self) -> None:
        workflow_id = "iteration-check"
        self.prepare_engineering(workflow_id)
        with self.assertRaises(WorkflowError) as caught:
            self.controller.ingest_engineer_handoff(
                workflow_id, self.engineer_handoff(workflow_id, 2)
            )
        self.assertEqual(caught.exception.code, "ITERATION_MISMATCH")
        self.assertEqual(
            self.controller.store.snapshot(workflow_id).state,
            WorkflowState.ENGINEER_REQUIRED,
        )

    def test_kill_switch_blocks_workflow_mutation(self) -> None:
        self.fixture.app.state_dir.mkdir(parents=True, exist_ok=True)
        self.fixture.app.effective_kill_switch.write_text("stop\n", encoding="utf-8")
        with self.assertRaises(WorkflowError) as caught:
            self.controller.start("blocked", b"intent\n")
        self.assertEqual(caught.exception.code, "KILL_SWITCH_ACTIVE")


if __name__ == "__main__":
    unittest.main()
