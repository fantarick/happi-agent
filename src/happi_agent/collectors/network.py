from __future__ import annotations

import json
from typing import Any

from happi_agent.collectors.base import CollectorResult, run_argv


class NetworkSummaryCollector:
    collector_id = "network.summary"

    def collect(self) -> CollectorResult:
        addresses = run_argv(("ip", "-j", "address", "show"))
        routes = run_argv(("ip", "-j", "route", "show"))
        listening = run_argv(("ss", "--numeric", "--listening", "--tcp", "--udp"))
        commands = {
            "addresses": addresses,
            "routes": routes,
            "listening_sockets": listening,
        }
        data: dict[str, Any] = {
            "commands": {key: value.to_dict() for key, value in commands.items()},
            "listening_socket_lines": listening.stdout.splitlines(),
        }
        for name, result in (("addresses", addresses), ("routes", routes)):
            if result.exit_code == 0:
                try:
                    data[name] = json.loads(result.stdout)
                except json.JSONDecodeError:
                    data[f"{name}_parse_error"] = True
        errors = tuple(
            f"{name}:{result.error_code}"
            for name, result in commands.items()
            if result.error_code
        )
        return CollectorResult(self.collector_id, not errors, data, errors)
