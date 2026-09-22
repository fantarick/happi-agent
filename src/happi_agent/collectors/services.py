from __future__ import annotations

from happi_agent.collectors.base import CollectorResult, run_argv


class ServicesCollector:
    collector_id = "host.services"

    def collect(self) -> CollectorResult:
        running = run_argv(
            (
                "systemctl",
                "list-units",
                "--type=service",
                "--state=running,failed",
                "--all",
                "--no-legend",
                "--no-pager",
                "--plain",
            )
        )
        failed = run_argv(
            (
                "systemctl",
                "list-units",
                "--type=service",
                "--state=failed",
                "--all",
                "--no-legend",
                "--no-pager",
                "--plain",
            )
        )
        errors = tuple(
            name
            for name, result in (("services", running), ("failed", failed))
            if result.error_code
        )
        return CollectorResult(
            self.collector_id,
            not errors,
            {
                "commands": {
                    "services": running.to_dict(),
                    "failed": failed.to_dict(),
                },
                "service_lines": running.stdout.splitlines(),
                "failed_service_lines": failed.stdout.splitlines(),
            },
            errors,
        )

