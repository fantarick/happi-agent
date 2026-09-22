from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


PROTOCOL_VERSION = "agentic-dev-playbook/v0.2"
DEFAULT_MAX_ITERATIONS = 3


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class WorkflowState(str, Enum):
    INTENT = "INTENT"
    DISCOVERY_REQUIRED = "DISCOVERY_REQUIRED"
    CONTRACT_REQUIRED = "CONTRACT_REQUIRED"
    CONTRACT_READY = "CONTRACT_READY"
    ENGINEER_REQUIRED = "ENGINEER_REQUIRED"
    VERIFYING = "VERIFYING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    HUMAN_MERGE_REQUIRED = "HUMAN_MERGE_REQUIRED"
    COMPLETE = "COMPLETE"
    ESCALATED = "ESCALATED"
    BLOCKED = "BLOCKED"


TERMINAL_WORKFLOW_STATES = frozenset(
    {WorkflowState.COMPLETE, WorkflowState.ESCALATED, WorkflowState.BLOCKED}
)


ALLOWED_WORKFLOW_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.INTENT: frozenset(
        {WorkflowState.DISCOVERY_REQUIRED, WorkflowState.ESCALATED}
    ),
    WorkflowState.DISCOVERY_REQUIRED: frozenset(
        {
            WorkflowState.CONTRACT_REQUIRED,
            WorkflowState.ESCALATED,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.CONTRACT_REQUIRED: frozenset(
        {
            WorkflowState.CONTRACT_READY,
            WorkflowState.ESCALATED,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.CONTRACT_READY: frozenset(
        {WorkflowState.ENGINEER_REQUIRED, WorkflowState.ESCALATED}
    ),
    WorkflowState.ENGINEER_REQUIRED: frozenset(
        {
            WorkflowState.VERIFYING,
            WorkflowState.ESCALATED,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.VERIFYING: frozenset(
        {
            WorkflowState.REVIEW_REQUIRED,
            WorkflowState.CHANGES_REQUIRED,
            WorkflowState.ESCALATED,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.REVIEW_REQUIRED: frozenset(
        {
            WorkflowState.HUMAN_MERGE_REQUIRED,
            WorkflowState.CHANGES_REQUIRED,
            WorkflowState.ESCALATED,
        }
    ),
    WorkflowState.CHANGES_REQUIRED: frozenset(
        {
            WorkflowState.ENGINEER_REQUIRED,
            WorkflowState.ESCALATED,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.HUMAN_MERGE_REQUIRED: frozenset(
        {WorkflowState.COMPLETE, WorkflowState.ESCALATED}
    ),
    WorkflowState.COMPLETE: frozenset(),
    WorkflowState.ESCALATED: frozenset(),
    WorkflowState.BLOCKED: frozenset(),
}


@dataclass(frozen=True)
class WorkflowSnapshot:
    workflow_id: str
    state: WorkflowState
    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    def __post_init__(self) -> None:
        if not self.workflow_id:
            raise ProtocolError("INVALID_WORKFLOW_ID", "workflow_id must not be empty")
        if self.iteration < 0:
            raise ProtocolError("INVALID_ITERATION", "iteration must be non-negative")
        if self.max_iterations < 1:
            raise ProtocolError(
                "INVALID_MAX_ITERATIONS", "max_iterations must be at least one"
            )
        if self.iteration > self.max_iterations:
            raise ProtocolError(
                "ITERATION_LIMIT_EXCEEDED",
                "iteration cannot exceed max_iterations",
            )


def transition(
    snapshot: WorkflowSnapshot, target: WorkflowState
) -> WorkflowSnapshot:
    allowed = ALLOWED_WORKFLOW_TRANSITIONS[snapshot.state]
    if target not in allowed:
        raise ProtocolError(
            "ILLEGAL_PROTOCOL_TRANSITION",
            f"{snapshot.state.value} -> {target.value} is not allowed",
        )

    iteration = snapshot.iteration
    if snapshot.state is WorkflowState.ENGINEER_REQUIRED and target is WorkflowState.VERIFYING:
        iteration += 1
        if iteration > snapshot.max_iterations:
            raise ProtocolError(
                "ITERATION_LIMIT_REACHED",
                "engineering iteration limit reached",
            )

    if snapshot.state is WorkflowState.CHANGES_REQUIRED and target is WorkflowState.ENGINEER_REQUIRED:
        if snapshot.iteration >= snapshot.max_iterations:
            raise ProtocolError(
                "ITERATION_LIMIT_REACHED",
                "another engineering pass would exceed max_iterations",
            )

    return WorkflowSnapshot(
        workflow_id=snapshot.workflow_id,
        state=target,
        iteration=iteration,
        max_iterations=snapshot.max_iterations,
    )


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("INVALID_HANDOFF", f"{label} must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ProtocolError(
            "INVALID_HANDOFF", f"{label} missing keys: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ProtocolError(
            "INVALID_HANDOFF", f"{label} unknown keys: {', '.join(sorted(unknown))}"
        )
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProtocolError("INVALID_HANDOFF", f"{label} must be a string array")
    return tuple(value)


@dataclass(frozen=True)
class FeatureContract:
    status: str
    objective: str
    acceptance_criteria: tuple[tuple[str, str], ...]
    invariants: tuple[str, ...]
    human_decisions_reserved: tuple[str, ...]
    escalation_conditions: tuple[str, ...]


def parse_feature_contract(value: object) -> FeatureContract:
    data = _strict_object(
        value,
        required=frozenset(
            {
                "protocol_version",
                "type",
                "status",
                "objective",
                "non_goals",
                "acceptance_criteria",
                "invariants",
                "verification_plan",
                "human_decisions_reserved",
                "escalation_conditions",
            }
        ),
        optional=frozenset({"why"}),
        label="feature contract",
    )
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_PROTOCOL", "unsupported protocol_version")
    if data["type"] != "feature_contract":
        raise ProtocolError("INVALID_CONTRACT", "wrong contract type")
    if data["status"] not in {
        "DRAFT",
        "APPROVED",
        "IMPLEMENTING",
        "REVIEW",
        "COMPLETE",
        "SUPERSEDED",
    }:
        raise ProtocolError("INVALID_CONTRACT", "unsupported contract status")
    if not isinstance(data["objective"], str) or not data["objective"].strip():
        raise ProtocolError("INVALID_CONTRACT", "objective must be non-empty")

    _string_list(data["non_goals"], "non_goals")
    invariants = _string_list(data["invariants"], "invariants")
    human_decisions = _string_list(
        data["human_decisions_reserved"], "human_decisions_reserved"
    )
    escalation = _string_list(
        data["escalation_conditions"], "escalation_conditions"
    )

    raw_criteria = data["acceptance_criteria"]
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ProtocolError(
            "INVALID_CONTRACT", "acceptance_criteria must be a non-empty array"
        )
    criteria: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for raw in raw_criteria:
        item = _strict_object(
            raw,
            required=frozenset({"id", "text"}),
            label="acceptance criterion",
        )
        criterion_id = item["id"]
        text = item["text"]
        if (
            not isinstance(criterion_id, str)
            or not criterion_id
            or criterion_id in seen_ids
        ):
            raise ProtocolError(
                "INVALID_CONTRACT", "criterion ids must be non-empty and unique"
            )
        if not isinstance(text, str) or not text.strip():
            raise ProtocolError("INVALID_CONTRACT", "criterion text must be non-empty")
        seen_ids.add(criterion_id)
        criteria.append((criterion_id, text))

    plan = data["verification_plan"]
    if not isinstance(plan, list):
        raise ProtocolError("INVALID_CONTRACT", "verification_plan must be an array")
    mapped: set[str] = set()
    for raw in plan:
        item = _strict_object(
            raw,
            required=frozenset({"criterion", "verification"}),
            label="verification plan item",
        )
        criterion = item["criterion"]
        verification = item["verification"]
        if criterion not in seen_ids or criterion in mapped:
            raise ProtocolError(
                "INVALID_CONTRACT",
                "verification_plan must map each known criterion at most once",
            )
        if not isinstance(verification, str) or not verification.strip():
            raise ProtocolError(
                "INVALID_CONTRACT", "verification description must be non-empty"
            )
        mapped.add(criterion)
    if mapped != seen_ids:
        raise ProtocolError(
            "INVALID_CONTRACT",
            "verification_plan must cover every acceptance criterion",
        )

    return FeatureContract(
        status=data["status"],
        objective=data["objective"].strip(),
        acceptance_criteria=tuple(criteria),
        invariants=invariants,
        human_decisions_reserved=human_decisions,
        escalation_conditions=escalation,
    )


@dataclass(frozen=True)
class EngineerHandoff:
    status: str
    iteration: int
    branch: str
    base_commit: str
    head_commit: str | None
    changes: tuple[str, ...]
    changed_files: tuple[str, ...]
    deviations: tuple[str, ...]
    known_uncertainty: tuple[str, ...]


def parse_engineer_handoff(value: object) -> EngineerHandoff:
    data = _strict_object(
        value,
        required=frozenset(
            {
                "protocol_version",
                "type",
                "status",
                "iteration",
                "repository_state",
                "changes",
                "verification",
                "deviations",
                "known_uncertainty",
            }
        ),
        optional=frozenset({"changed_files", "git_reference"}),
        label="engineer handoff",
    )
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_PROTOCOL", "unsupported protocol_version")
    if data["type"] != "engineer_handoff":
        raise ProtocolError("INVALID_HANDOFF", "wrong handoff type")
    if data["status"] not in {
        "READY_FOR_REVIEW",
        "BLOCKED",
        "ESCALATION_REQUIRED",
    }:
        raise ProtocolError("INVALID_HANDOFF", "unsupported engineer status")
    iteration = data["iteration"]
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
        raise ProtocolError("INVALID_HANDOFF", "iteration must be a positive integer")

    repository = _strict_object(
        data["repository_state"],
        required=frozenset({"branch", "base_commit"}),
        optional=frozenset({"head_commit"}),
        label="repository_state",
    )
    branch = repository["branch"]
    base_commit = repository["base_commit"]
    head_commit = repository.get("head_commit")
    if not isinstance(branch, str) or not branch:
        raise ProtocolError("INVALID_HANDOFF", "branch must be non-empty")
    if not isinstance(base_commit, str) or len(base_commit) < 7:
        raise ProtocolError("INVALID_HANDOFF", "base_commit is invalid")
    if head_commit is not None and (
        not isinstance(head_commit, str) or len(head_commit) < 7
    ):
        raise ProtocolError("INVALID_HANDOFF", "head_commit is invalid")

    verification = data["verification"]
    if not isinstance(verification, list):
        raise ProtocolError("INVALID_HANDOFF", "verification must be an array")
    for check in verification:
        item = _strict_object(
            check,
            required=frozenset({"check", "result"}),
            optional=frozenset({"evidence"}),
            label="verification item",
        )
        if not isinstance(item["check"], str) or not item["check"]:
            raise ProtocolError("INVALID_HANDOFF", "verification check is invalid")
        if item["result"] not in {"PASS", "FAIL", "NOT_RUN"}:
            raise ProtocolError("INVALID_HANDOFF", "verification result is invalid")
        if "evidence" in item and not isinstance(item["evidence"], str):
            raise ProtocolError("INVALID_HANDOFF", "verification evidence is invalid")

    return EngineerHandoff(
        status=data["status"],
        iteration=iteration,
        branch=branch,
        base_commit=base_commit,
        head_commit=head_commit,
        changes=_string_list(data["changes"], "changes"),
        changed_files=_string_list(data.get("changed_files", []), "changed_files"),
        deviations=_string_list(data["deviations"], "deviations"),
        known_uncertainty=_string_list(
            data["known_uncertainty"], "known_uncertainty"
        ),
    )


@dataclass(frozen=True)
class ArchitectReview:
    verdict: str
    acceptance_results: tuple[tuple[str, str], ...]
    blocking_findings: tuple[str, ...]
    non_blocking_findings: tuple[str, ...]
    residual_uncertainty: tuple[str, ...]


def parse_architect_review(value: object) -> ArchitectReview:
    data = _strict_object(
        value,
        required=frozenset(
            {
                "protocol_version",
                "type",
                "verdict",
                "acceptance_criteria",
                "blocking_findings",
                "non_blocking_findings",
                "residual_uncertainty",
            }
        ),
        label="architect review",
    )
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_PROTOCOL", "unsupported protocol_version")
    if data["type"] != "architect_review":
        raise ProtocolError("INVALID_HANDOFF", "wrong review type")
    if data["verdict"] not in {"APPROVE", "REQUEST_CHANGES", "ESCALATE"}:
        raise ProtocolError("INVALID_HANDOFF", "unsupported review verdict")

    criteria = data["acceptance_criteria"]
    if not isinstance(criteria, list):
        raise ProtocolError("INVALID_HANDOFF", "acceptance_criteria must be an array")
    acceptance_results: list[tuple[str, str]] = []
    seen_criteria: set[str] = set()
    for criterion in criteria:
        item = _strict_object(
            criterion,
            required=frozenset({"id", "result"}),
            optional=frozenset({"evidence"}),
            label="acceptance criterion",
        )
        if (
            not isinstance(item["id"], str)
            or not item["id"]
            or item["id"] in seen_criteria
        ):
            raise ProtocolError(
                "INVALID_HANDOFF", "criterion ids must be non-empty and unique"
            )
        seen_criteria.add(item["id"])
        if item["result"] not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ProtocolError("INVALID_HANDOFF", "criterion result is invalid")
        if "evidence" in item and not isinstance(item["evidence"], str):
            raise ProtocolError("INVALID_HANDOFF", "criterion evidence is invalid")
        acceptance_results.append((item["id"], item["result"]))

    blocking = _string_list(data["blocking_findings"], "blocking_findings")
    non_blocking = _string_list(
        data["non_blocking_findings"], "non_blocking_findings"
    )
    residual = _string_list(
        data["residual_uncertainty"], "residual_uncertainty"
    )
    if data["verdict"] == "APPROVE":
        if blocking:
            raise ProtocolError(
                "INVALID_REVIEW", "APPROVE cannot contain blocking findings"
            )
        if any(result != "PASS" for _, result in acceptance_results):
            raise ProtocolError(
                "INVALID_REVIEW", "APPROVE requires every criterion to PASS"
            )
    if data["verdict"] == "REQUEST_CHANGES" and not blocking:
        raise ProtocolError(
            "INVALID_REVIEW", "REQUEST_CHANGES requires a blocking finding"
        )

    return ArchitectReview(
        verdict=data["verdict"],
        acceptance_results=tuple(acceptance_results),
        blocking_findings=blocking,
        non_blocking_findings=non_blocking,
        residual_uncertainty=residual,
    )
