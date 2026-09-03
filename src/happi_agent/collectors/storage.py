from __future__ import annotations

import json
from typing import Any

from happi_agent.collectors.base import CollectorResult, run_argv


class StorageCollector:
    collector_id = "host.storage"

    def collect(self) -> CollectorResult:
        lsblk = run_argv(
            (
                "lsblk",
                "--json",
                "--bytes",
                "--output",
                "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,RO,RM",
            )
        )
        df = run_argv(
            (
                "df",
                "--block-size=1",
                "--output=source,fstype,size,used,avail,pcent,target",
            )
        )
        data: dict[str, Any] = {
            "commands": {"lsblk": lsblk.to_dict(), "df": df.to_dict()},
            "filesystems": df.stdout.splitlines() if df.exit_code == 0 else [],
        }
        if lsblk.exit_code == 0:
            try:
                data["block_devices"] = json.loads(lsblk.stdout)
            except json.JSONDecodeError:
                data["block_devices_parse_error"] = True
        errors = tuple(
            name
            for name, result in (("lsblk", lsblk), ("df", df))
            if result.error_code
        )
        return CollectorResult(self.collector_id, not errors, data, errors)

