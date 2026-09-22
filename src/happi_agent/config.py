from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from happi_agent.models import AppConfig, ValidationPolicy
from happi_agent.security import is_relative_to


PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ConfigError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _strict_keys(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(
            "UNKNOWN_CONFIG_KEY",
            f"unknown key(s) in {location}: {', '.join(unknown)}",
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
            "policies_dir",
            "lock_file",
            "kill_switch",
        },
        "application config",
    )
    required = {"state_dir", "worktree_root", "canonical_repo", "policies_dir"}
    missing = sorted(required - set(raw))
    if missing:
        raise ConfigError(
            "MISSING_CONFIG_KEY",
            f"missing application key(s): {', '.join(missing)}",
        )
    base = path.resolve().parent
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
        worktree_root=_resolve_path(raw["worktree_root"], base, "worktree_root"),
        canonical_repo=_resolve_path(raw["canonical_repo"], base, "canonical_repo"),
        policies_dir=_resolve_path(raw["policies_dir"], base, "policies_dir"),
        lock_file=lock_file,
        kill_switch=kill_switch,
    )


def _string_array(
    data: dict[str, Any], key: str, *, default: tuple[str, ...] = ()
) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ConfigError(
            "INVALID_VALIDATION_POLICY", f"{key} must be a string array"
        )
    if len(value) != len(set(value)):
        raise ConfigError(
            "INVALID_VALIDATION_POLICY", f"{key} must not contain duplicates"
        )
    return tuple(value)


def _positive_int(data: dict[str, Any], key: str, maximum: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            "INVALID_VALIDATION_POLICY", f"{key} must be an integer"
        )
    if not 1 <= value <= maximum:
        raise ConfigError(
            "INVALID_VALIDATION_POLICY",
            f"{key} must be between 1 and {maximum}",
        )
    return value


def load_validation_policy(profile_id: str, app: AppConfig) -> ValidationPolicy:
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise ConfigError(
            "INVALID_POLICY_ID", f"invalid validation policy id: {profile_id!r}"
        )
    root = app.policies_dir.resolve()
    source = (root / f"{profile_id}.json").resolve()
    if not is_relative_to(source, root):
        raise ConfigError("INVALID_POLICY_PATH", "validation policy escapes policies_dir")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(
            "POLICY_NOT_FOUND", f"validation policy not found: {profile_id}"
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(
            "INVALID_VALIDATION_POLICY",
            f"cannot read validation policy {profile_id}: {exc}",
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            "INVALID_VALIDATION_POLICY", "validation policy root must be an object"
        )
    _strict_keys(
        raw,
        {
            "version",
            "id",
            "max_files",
            "max_diff_bytes",
            "forbidden_paths",
            "allowed_paths",
            "allowed_binary_extensions",
        },
        "validation policy",
    )
    if raw.get("version") != 1:
        raise ConfigError(
            "INVALID_VALIDATION_POLICY", "validation policy version must be 1"
        )
    if raw.get("id") != profile_id:
        raise ConfigError(
            "POLICY_ID_MISMATCH",
            f"declared policy id does not match {profile_id!r}",
        )
    return ValidationPolicy(
        max_files=_positive_int(raw, "max_files", 10_000),
        max_diff_bytes=_positive_int(raw, "max_diff_bytes", 100 * 1024 * 1024),
        forbidden_paths=_string_array(raw, "forbidden_paths"),
        allowed_paths=_string_array(raw, "allowed_paths"),
        allowed_binary_extensions=_string_array(
            raw, "allowed_binary_extensions"
        ),
    )
