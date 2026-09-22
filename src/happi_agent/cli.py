from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from happi_agent.config import ConfigError, load_app_config, load_validation_policy
from happi_agent.state import StateStore
from happi_agent.workflow import WorkflowController, WorkflowError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="happi-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    workflow = subparsers.add_parser(
        "workflow", help="operate the agentic-dev-playbook/v0.2 state machine"
    )
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)

    start = workflow_sub.add_parser("start", help="record intent and start a workflow")
    start.add_argument("workflow_id")
    start.add_argument("--intent", required=True, type=Path)
    start.add_argument("--max-iterations", type=int, default=3)

    discovery = workflow_sub.add_parser(
        "discovery-complete", help="record repository discovery evidence"
    )
    discovery.add_argument("workflow_id")
    discovery.add_argument("--evidence", required=True, type=Path)

    contract = workflow_sub.add_parser(
        "contract", help="ingest an approved structured feature contract"
    )
    contract.add_argument("workflow_id")
    contract.add_argument("path", type=Path)

    engineer = workflow_sub.add_parser(
        "engineer-handoff", help="ingest a structured engineer handoff"
    )
    engineer.add_argument("workflow_id")
    engineer.add_argument("path", type=Path)

    verify = workflow_sub.add_parser(
        "verify", help="run deterministic verification on the prepared worktree"
    )
    verify.add_argument("workflow_id")
    verify.add_argument(
        "--policy",
        required=True,
        help="deterministic validation policy id",
    )

    review = workflow_sub.add_parser(
        "review", help="ingest a structured independent architect review"
    )
    review.add_argument("workflow_id")
    review.add_argument("path", type=Path)

    status = workflow_sub.add_parser("status", help="show one workflow")
    status.add_argument("workflow_id")

    next_action = workflow_sub.add_parser(
        "next", help="show the next permitted human/agent action"
    )
    next_action.add_argument("workflow_id")

    workflow_sub.add_parser("list", help="list workflows")

    complete = workflow_sub.add_parser(
        "complete", help="record an observed human merge and complete the workflow"
    )
    complete.add_argument("workflow_id")
    complete.add_argument("--merged-commit", required=True)

    subparsers.add_parser(
        "legacy-runs", help="read-only listing of historical v0.1 run records"
    )
    legacy_show = subparsers.add_parser(
        "legacy-show", help="read-only inspection of one historical v0.1 run"
    )
    legacy_show.add_argument("run_id")
    return parser


def _config_path() -> Path:
    return Path(
        os.environ.get("HAPPI_AGENT_CONFIG", "/etc/happi-agent/config.toml")
    ).expanduser()


def _print_error(code: str, message: str) -> None:
    print(
        json.dumps({"error_code": code, "message": message}, sort_keys=True),
        file=sys.stderr,
    )


def _read(path: Path, label: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise WorkflowError("ARTIFACT_READ_ERROR", f"cannot read {label}: {exc}") from exc
    if not data:
        raise WorkflowError("EMPTY_ARTIFACT", f"{label} is empty")
    return data


def _print_snapshot(controller: WorkflowController, workflow_id: str) -> None:
    snapshot = controller.store.snapshot(workflow_id)
    print(
        json.dumps(
            {
                "workflow_id": workflow_id,
                "state": snapshot.state.value,
                "iteration": snapshot.iteration,
                "max_iterations": snapshot.max_iterations,
                "next_action": controller.next_action(workflow_id),
            },
            sort_keys=True,
        )
    )


def _workflow_command(
    args: argparse.Namespace, controller: WorkflowController
) -> int:
    command = args.workflow_command
    if command == "start":
        controller.start(
            args.workflow_id,
            _read(args.intent, "intent"),
            max_iterations=args.max_iterations,
        )
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "discovery-complete":
        controller.discovery_complete(
            args.workflow_id, _read(args.evidence, "discovery evidence")
        )
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "contract":
        controller.approve_contract(
            args.workflow_id, _read(args.path, "feature contract")
        )
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "engineer-handoff":
        controller.ingest_engineer_handoff(
            args.workflow_id, _read(args.path, "engineer handoff")
        )
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "verify":
        policy = load_validation_policy(args.policy, controller.app)
        snapshot, result = controller.verify(args.workflow_id, policy)
        print(
            json.dumps(
                {
                    "workflow_id": args.workflow_id,
                    "state": snapshot.state.value,
                    "iteration": snapshot.iteration,
                    "next_action": controller.next_action(args.workflow_id),
                    "validation_ok": result.ok,
                    "changed_files": list(result.changed_files),
                    "diff_bytes": result.diff_bytes,
                },
                sort_keys=True,
            )
        )
        return 0 if result.ok else 4

    if command == "review":
        controller.ingest_architect_review(
            args.workflow_id, _read(args.path, "architect review")
        )
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "status":
        print(
            json.dumps(
                controller.status(args.workflow_id),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if command == "next":
        _print_snapshot(controller, args.workflow_id)
        return 0

    if command == "list":
        print(json.dumps(controller.store.list(), indent=2, sort_keys=True))
        return 0

    if command == "complete":
        controller.complete(args.workflow_id, args.merged_commit)
        _print_snapshot(controller, args.workflow_id)
        return 0

    raise WorkflowError("UNKNOWN_COMMAND", f"unknown workflow command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        app = load_app_config(_config_path())
        if args.command == "workflow":
            controller = WorkflowController(app)
            controller.initialize()
            return _workflow_command(args, controller)

        store = StateStore(app.state_dir / "state.sqlite3")
        store.initialize()
        if args.command == "legacy-runs":
            print(json.dumps(store.list_runs(), indent=2, sort_keys=True))
            return 0
        if args.command == "legacy-show":
            run = store.get_run(args.run_id)
            if run is None:
                _print_error("RUN_NOT_FOUND", f"run not found: {args.run_id}")
                return 2
            print(json.dumps(run, indent=2, sort_keys=True))
            return 0
    except (ConfigError, WorkflowError) as exc:
        _print_error(exc.code, exc.message)
        return 2
    except Exception as exc:
        _print_error(getattr(exc, "code", "UNEXPECTED_ERROR"), str(exc))
        return 1
    return 1
