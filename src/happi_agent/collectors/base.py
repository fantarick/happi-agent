from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence


MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollectorResult:
    collector_id: str
    ok: bool
    data: dict[str, Any]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Collector(Protocol):
    collector_id: str

    def collect(self) -> CollectorResult: ...


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        collector_id = collector.collector_id
        if not collector_id or collector_id in self._collectors:
            raise ValueError(f"duplicate or empty collector id: {collector_id!r}")
        self._collectors[collector_id] = collector

    def ids(self) -> frozenset[str]:
        return frozenset(self._collectors)

    def get(self, collector_id: str) -> Collector:
        try:
            return self._collectors[collector_id]
        except KeyError as exc:
            raise KeyError(f"unregistered collector: {collector_id}") from exc

    def collect(self, collector_ids: Sequence[str]) -> tuple[CollectorResult, ...]:
        return tuple(self.get(collector_id).collect() for collector_id in collector_ids)


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_COMMAND_OUTPUT_BYTES:
        return text
    suffix = b"\n[output truncated by happi-agent]\n"
    return (encoded[: MAX_COMMAND_OUTPUT_BYTES - len(suffix)] + suffix).decode(
        "utf-8", errors="replace"
    )


def run_argv(argv: Sequence[str], timeout_seconds: int = 15) -> CommandResult:
    """Run an allowlisted collector command with structured argv and no shell."""

    if not argv or not all(isinstance(arg, str) and arg for arg in argv):
        raise ValueError("argv must contain non-empty strings")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
            env=environment,
        )
    except FileNotFoundError:
        return CommandResult(tuple(argv), None, "", "", error_code="COMMAND_NOT_FOUND")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            tuple(argv),
            None,
            _truncate(stdout),
            _truncate(stderr),
            timed_out=True,
            error_code="COMMAND_TIMEOUT",
        )
    return CommandResult(
        tuple(argv),
        completed.returncode,
        _truncate(completed.stdout),
        _truncate(completed.stderr),
        error_code=None if completed.returncode == 0 else "COMMAND_FAILED",
    )


def default_registry() -> CollectorRegistry:
    from happi_agent.collectors.host_basic import HostBasicCollector
    from happi_agent.collectors.network import NetworkSummaryCollector
    from happi_agent.collectors.services import ServicesCollector
    from happi_agent.collectors.storage import StorageCollector

    registry = CollectorRegistry()
    registry.register(HostBasicCollector())
    registry.register(StorageCollector())
    registry.register(ServicesCollector())
    registry.register(NetworkSummaryCollector())
    return registry

