from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from happi_agent.models import AppConfig, JobConfig, ValidationPolicy
from happi_agent.security import is_relative_to, sha256_bytes


JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ConfigError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _strict_keys(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(
            "UNKNOWN_CONFIG_KEY", f"unknown key(s) in {location}: {', '.join(unknown)}"
        )


def _resolve_path(value: object, base: Path, key: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError("INVALID_PATH", f"{key} must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_app_config(path: Path) -> AppConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("CONFIG_NOT_FOUND", f"config file not found: {path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("INVALID_TOML", f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("INVALID_CONFIG", "application config must be a TOML table")
    _strict_keys(
        raw,
        {
            "state_dir",
            "worktree_root",
            "canonical_repo",
            "jobs_dir",
            "prompts_dir",
            "codex_binary",
            "lock_file",
            "kill_switch",
        },
        "application config",
    )
    base = path.resolve().parent
    required = {
        "state_dir",
        "worktree_root",
        "canonical_repo",
        "jobs_dir",
        "prompts_dir",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ConfigError(
            "MISSING_CONFIG_KEY", f"missing application key(s): {', '.join(missing)}"
        )
    binary = raw.get("codex_binary", "codex")
    if not isinstance(binary, str) or not binary or "/" in binary:
        raise ConfigError(
            "INVALID_CODEX_BINARY", "codex_binary must be a bare executable name"
        )
    lock_file = (
        _resolve_path(raw["lock_file"], base, "lock_file")
        if "lock_file" in raw
        else None
    )
    kill_switch = (
        _resolve_path(raw["kill_switch"], base, "kill_switch")
        if "kill_switch" in raw
        else None
    )
    return AppConfig(
        state_dir=_resolve_path(raw["state_dir"], base, "state_dir"),
        worktree_root=_resolve_path(
            raw["worktree_root"], base, "worktree_root"
        ),
        canonical_repo=_resolve_path(
            raw["canonical_repo"], base, "canonical_repo"
        ),
        jobs_dir=_resolve_path(raw["jobs_dir"], base, "jobs_dir"),
        prompts_dir=_resolve_path(raw["prompts_dir"], base, "prompts_dir"),
        codex_binary=binary,
        lock_file=lock_file,
        kill_switch=kill_switch,
    )


def _parse_scalar(value: str, line_number: int) -> object:
    if not value:
        raise ConfigError("INVALID_YAML", f"empty scalar at line {line_number}")
    if value.startswith(('"', "'")):
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise ConfigError(
                "INVALID_YAML", f"unterminated quoted scalar at line {line_number}"
            )
        if quote == '"':
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ConfigError(
                    "INVALID_YAML", f"invalid quoted scalar at line {line_number}"
                ) from exc
            if not isinstance(parsed, str):
                raise ConfigError(
                    "INVALID_YAML", f"expected string at line {line_number}"
                )
            return parsed
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        return int(value)
    if any(token in value for token in ("&", "*", "!", "{", "}", "[", "]")):
        raise ConfigError(
            "UNSUPPORTED_YAML", f"unsupported YAML feature at line {line_number}"
        )
    return value


def parse_strict_yaml(text: str) -> dict[str, Any]:
    """Parse the small declarative YAML subset accepted for job files.

    It supports nested mappings, scalar lists, strings, integers, booleans and
    null. Anchors, tags, flow collections, multiline values, tabs and duplicate
    keys are rejected. This is deliberately narrower than general YAML.
    """

    lines: list[tuple[int, int, str]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        if "\t" in raw_line:
            raise ConfigError("INVALID_YAML", f"tabs are forbidden at line {number}")
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2:
            raise ConfigError(
                "INVALID_YAML", f"indentation must use two spaces at line {number}"
            )
        if stripped in {"---", "..."} or stripped.startswith((">", "|")):
            raise ConfigError(
                "UNSUPPORTED_YAML", f"unsupported YAML feature at line {number}"
            )
        lines.append((number, indent, stripped))
    if not lines:
        raise ConfigError("INVALID_YAML", "job file is empty")

    def parse_block(index: int, indent: int) -> tuple[object, int]:
        if index >= len(lines) or lines[index][1] != indent:
            line_number = lines[index][0] if index < len(lines) else lines[-1][0]
            raise ConfigError(
                "INVALID_YAML", f"unexpected indentation near line {line_number}"
            )
        is_list = lines[index][2].startswith("- ")
        if is_list:
            result_list: list[object] = []
            while index < len(lines) and lines[index][1] == indent:
                number, _, content = lines[index]
                if not content.startswith("- "):
                    raise ConfigError(
                        "INVALID_YAML", f"mixed list and mapping at line {number}"
                    )
                item = content[2:].strip()
                if not item or ":" in item:
                    raise ConfigError(
                        "UNSUPPORTED_YAML",
                        f"only scalar list items are supported at line {number}",
                    )
                result_list.append(_parse_scalar(item, number))
                index += 1
            return result_list, index

        result_map: dict[str, object] = {}
        while index < len(lines) and lines[index][1] == indent:
            number, _, content = lines[index]
            if content.startswith("- ") or ":" not in content:
                raise ConfigError("INVALID_YAML", f"expected mapping at line {number}")
            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
                raise ConfigError("INVALID_YAML", f"invalid key at line {number}")
            if key in result_map:
                raise ConfigError(
                    "DUPLICATE_CONFIG_KEY", f"duplicate key {key!r} at line {number}"
                )
            index += 1
            if value:
                result_map[key] = _parse_scalar(value, number)
            else:
                if index >= len(lines) or lines[index][1] != indent + 2:
                    raise ConfigError(
                        "INVALID_YAML", f"missing nested value after line {number}"
                    )
                nested, index = parse_block(index, indent + 2)
                result_map[key] = nested
        return result_map, index

    parsed, final_index = parse_block(0, lines[0][1])
    if lines[0][1] != 0 or final_index != len(lines) or not isinstance(parsed, dict):
        raise ConfigError("INVALID_YAML", "job root must be a mapping")
    return parsed


def _require_int(
    data: dict[str, Any], key: str, *, minimum: int, maximum: int
) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("INVALID_JOB", f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(
            "INVALID_JOB", f"{key} must be between {minimum} and {maximum}"
        )
    return value


def _string_list(data: dict[str, Any], key: str, *, required: bool = False) -> tuple[str, ...]:
    value = data.get(key)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or not value:
        raise ConfigError("INVALID_JOB", f"{key} must be a non-empty list")
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigError("INVALID_JOB", f"{key} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ConfigError("INVALID_JOB", f"{key} must not contain duplicates")
    return tuple(value)


def load_job_config(
    job_id: str,
    app: AppConfig,
    registered_collectors: frozenset[str] | None = None,
) -> JobConfig:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ConfigError("INVALID_JOB_ID", f"invalid job id: {job_id!r}")
    source = (app.jobs_dir / f"{job_id}.yaml").resolve()
    if not is_relative_to(source, app.jobs_dir.resolve()):
        raise ConfigError("INVALID_JOB_PATH", "job path escapes jobs_dir")
    try:
        data = parse_strict_yaml(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("JOB_NOT_FOUND", f"job not found: {job_id}") from exc
    except OSError as exc:
        raise ConfigError("JOB_READ_ERROR", f"cannot read job {job_id}: {exc}") from exc
    _strict_keys(
        data,
        {"version", "id", "prompt", "collectors", "timeout_seconds", "validation"},
        "job",
    )
    version = _require_int(data, "version", minimum=1, maximum=1)
    declared_id = data.get("id")
    if declared_id != job_id:
        raise ConfigError(
            "JOB_ID_MISMATCH",
            f"job id {declared_id!r} does not match requested id {job_id!r}",
        )
    prompt_value = data.get("prompt")
    if not isinstance(prompt_value, str) or not prompt_value.endswith(".md"):
        raise ConfigError("INVALID_PROMPT", "prompt must name a Markdown file")
    if Path(prompt_value).is_absolute() or len(Path(prompt_value).parts) != 1:
        raise ConfigError("INVALID_PROMPT", "prompt must be a filename inside prompts_dir")
    prompt_path = (app.prompts_dir / prompt_value).resolve()
    if not is_relative_to(prompt_path, app.prompts_dir.resolve()):
        raise ConfigError("INVALID_PROMPT", "prompt path escapes prompts_dir")
    if not prompt_path.is_file():
        raise ConfigError("PROMPT_NOT_FOUND", f"prompt not found: {prompt_path}")

    collectors = _string_list(data, "collectors", required=True)
    if registered_collectors is not None:
        unknown = sorted(set(collectors) - registered_collectors)
        if unknown:
            raise ConfigError(
                "UNKNOWN_COLLECTOR", f"unregistered collector(s): {', '.join(unknown)}"
            )
    timeout_seconds = _require_int(
        data, "timeout_seconds", minimum=1, maximum=24 * 60 * 60
    )
    validation = data.get("validation")
    if not isinstance(validation, dict):
        raise ConfigError("INVALID_JOB", "validation must be a mapping")
    _strict_keys(
        validation,
        {
            "max_files",
            "max_diff_bytes",
            "forbidden_paths",
            "allowed_paths",
            "allowed_binary_extensions",
        },
        "validation",
    )
    policy = ValidationPolicy(
        max_files=_require_int(validation, "max_files", minimum=1, maximum=10_000),
        max_diff_bytes=_require_int(
            validation, "max_diff_bytes", minimum=1, maximum=100 * 1024 * 1024
        ),
        forbidden_paths=_string_list(validation, "forbidden_paths"),
        allowed_paths=_string_list(validation, "allowed_paths"),
        allowed_binary_extensions=_string_list(
            validation, "allowed_binary_extensions"
        ),
    )
    return JobConfig(
        version=version,
        job_id=job_id,
        prompt_path=prompt_path,
        collectors=collectors,
        timeout_seconds=timeout_seconds,
        validation=policy,
        source_path=source,
    )


def resolved_config_hash(app: AppConfig, job: JobConfig) -> str:
    data = {
        "app": {
            "state_dir": str(app.state_dir),
            "worktree_root": str(app.worktree_root),
            "canonical_repo": str(app.canonical_repo),
            "jobs_dir": str(app.jobs_dir),
            "prompts_dir": str(app.prompts_dir),
            "codex_binary": app.codex_binary,
            "lock_file": str(app.effective_lock_file),
            "kill_switch": str(app.effective_kill_switch),
        },
        "job": {
            "version": job.version,
            "id": job.job_id,
            "prompt_path": str(job.prompt_path),
            "collectors": list(job.collectors),
            "timeout_seconds": job.timeout_seconds,
            "validation": asdict(job.validation),
        },
    }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(canonical)
