from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from happi_agent.collectors.base import CollectorResult, run_argv


def _os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {"ID", "VERSION_ID", "PRETTY_NAME"}:
            result[key.lower()] = value.strip().strip('"')
    return result


class HostBasicCollector:
    collector_id = "host.basic"

    def collect(self) -> CollectorResult:
        commands = {
            "uname": run_argv(("uname", "-a")),
            "uptime": run_argv(("uptime", "-p")),
            "cpu": run_argv(("lscpu", "--json")),
        }
        data: dict[str, Any] = {
            "os_release": _os_release(),
            "commands": {key: value.to_dict() for key, value in commands.items()},
        }
        cpu = commands["cpu"]
        if cpu.exit_code == 0:
            try:
                data["cpu"] = json.loads(cpu.stdout)
            except json.JSONDecodeError:
                data["cpu_parse_error"] = True
        errors = tuple(
            f"{name}:{result.error_code}"
            for name, result in commands.items()
            if result.error_code
        )
        return CollectorResult(self.collector_id, not errors, data, errors)

