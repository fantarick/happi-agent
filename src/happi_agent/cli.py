from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from happi_agent.config import ConfigError, load_app_config
from happi_agent.models import RunState
from happi_agent.runner import Runner
from happi_agent.state import StateStore


EXIT_CODES = {
    RunState.SUCCESS: 0,
    RunState.FAILED: 1,
    RunState.BLOCKED: 3,
    RunState.QUARANTINED: 4,
    RunState.TIMEOUT: 124,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="happi-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one declared job")
    run.add_argument("job_id")
    subparsers.add_parser("runs", help="list recorded runs")
    show = subparsers.add_parser("show", help="show one run and its audit trail")
    show.add_argument("run_id")
    return parser


def _config_path() -> Path:
    return Path(
        os.environ.get("HAPPI_AGENT_CONFIG", "/etc/happi-agent/config.toml")
    ).expanduser()


def _print_error(code: str, message: str) -> None:
    print(json.dumps({"error_code": code, "message": message}), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        app = load_app_config(_config_path())
        store = StateStore(app.state_dir / "state.sqlite3")
        store.initialize()
        if args.command == "run":
            outcome = Runner(app, state=store).run(args.job_id)
            print(
                json.dumps(
                    {
                        "run_id": outcome.run_id,
                        "state": outcome.state.value,
                        "error_code": outcome.error_code,
                    },
                    sort_keys=True,
                )
            )
            return EXIT_CODES.get(outcome.state, 1)
        if args.command == "runs":
            runs = store.list_runs()
            print(json.dumps(runs, indent=2, sort_keys=True))
            return 0
        if args.command == "show":
            run = store.get_run(args.run_id)
            if run is None:
                _print_error("RUN_NOT_FOUND", f"run not found: {args.run_id}")
                return 2
            print(json.dumps(run, indent=2, sort_keys=True))
            return 0
    except ConfigError as exc:
        _print_error(exc.code, exc.message)
        return 2
    except Exception as exc:
        _print_error(getattr(exc, "code", "UNEXPECTED_ERROR"), str(exc))
        return 1
    return 1
