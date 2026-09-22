from __future__ import annotations

import unittest

from happi_agent.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    WorkflowSnapshot,
    WorkflowState,
    parse_architect_review,
    parse_engineer_handoff,
    transition,
)


class ProtocolStateTests(unittest.TestCase):
    def test_happy_path_reaches_human_merge_gate(self) -> None:
        state = WorkflowSnapshot("wf", WorkflowState.INTENT)
        for target in (
            WorkflowState.DISCOVERY_REQUIRED,
            WorkflowState.CONTRACT_REQUIRED,
            WorkflowState.CONTRACT_READY,
            WorkflowState.ENGINEER_REQUIRED,
            WorkflowState.VERIFYING,
            WorkflowState.REVIEW_REQUIRED,
            WorkflowState.HUMAN_MERGE_REQUIRED,
        ):
            state = transition(state, target)
        self.assertEqual(state.state, WorkflowState.HUMAN_MERGE_REQUIRED)
        self.assertEqual(state.iteration, 1)

    def test_illegal_transition_is_rejected(self) -> None:
        state = WorkflowSnapshot("wf", WorkflowState.INTENT)
        with self.assertRaisesRegex(ProtocolError, "not allowed"):
            transition(state, WorkflowState.HUMAN_MERGE_REQUIRED)

    def test_iteration_limit_blocks_another_engineering_pass(self) -> None:
        state = WorkflowSnapshot(
            "wf", WorkflowState.CHANGES_REQUIRED, iteration=3, max_iterations=3
        )
        with self.assertRaises(ProtocolError) as caught:
            transition(state, WorkflowState.ENGINEER_REQUIRED)
        self.assertEqual(caught.exception.code, "ITERATION_LIMIT_REACHED")


class HandoffTests(unittest.TestCase):
    def engineer_payload(self) -> dict[str, object]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "type": "engineer_handoff",
            "status": "READY_FOR_REVIEW",
            "iteration": 1,
            "repository_state": {
                "branch": "feature/example",
                "base_commit": "abcdef1",
                "head_commit": "abcdef2",
            },
            "changes": ["implemented bounded change"],
            "changed_files": ["src/example.py"],
            "verification": [
                {"check": "tests", "result": "PASS", "evidence": "59 passed"}
            ],
            "deviations": [],
            "known_uncertainty": [],
        }

    def test_engineer_handoff_parses(self) -> None:
        handoff = parse_engineer_handoff(self.engineer_payload())
        self.assertEqual(handoff.status, "READY_FOR_REVIEW")
        self.assertEqual(handoff.iteration, 1)

    def test_engineer_handoff_rejects_unknown_fields(self) -> None:
        payload = self.engineer_payload()
        payload["persuasive_summary"] = "trust me"
        with self.assertRaises(ProtocolError):
            parse_engineer_handoff(payload)

    def test_engineer_handoff_rejects_fake_verification_result(self) -> None:
        payload = self.engineer_payload()
        payload["verification"] = [{"check": "tests", "result": "SHOULD_PASS"}]
        with self.assertRaises(ProtocolError):
            parse_engineer_handoff(payload)

    def test_architect_review_parses(self) -> None:
        review = parse_architect_review(
            {
                "protocol_version": PROTOCOL_VERSION,
                "type": "architect_review",
                "verdict": "APPROVE",
                "acceptance_criteria": [
                    {"id": "AC1", "result": "PASS", "evidence": "CI"}
                ],
                "blocking_findings": [],
                "non_blocking_findings": [],
                "residual_uncertainty": [],
            }
        )
        self.assertEqual(review.verdict, "APPROVE")

    def test_architect_review_rejects_unknown_verdict(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_architect_review(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "type": "architect_review",
                    "verdict": "MERGE",
                    "acceptance_criteria": [],
                    "blocking_findings": [],
                    "non_blocking_findings": [],
                    "residual_uncertainty": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
