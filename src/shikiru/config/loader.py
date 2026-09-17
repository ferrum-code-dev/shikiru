"""Merge packaged defaults, the project file and `--set` overrides into `Settings` (05 §1)."""

from __future__ import annotations

import tomllib
from importlib import resources
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from shikiru.config.settings import Settings
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic_core import ErrorDetails

PROJECT_CONFIG_RELATIVE_PATH = ".shikiru/config.toml"
_SET_OVERRIDE_SOURCE = "--set"


def load_settings(project_dir: Path, overrides: Sequence[str] = ()) -> Settings:
    """Merge packaged defaults, `config.toml` and `--set` overrides into `Settings`."""
    merged, sources = _load_defaults()
    project_file = project_dir / PROJECT_CONFIG_RELATIVE_PATH
    if project_file.is_file():
        project_data = _parse_toml(project_file, PROJECT_CONFIG_RELATIVE_PATH)
        merged = _deep_merge(merged, project_data)
        _record_sources(sources, project_data, PROJECT_CONFIG_RELATIVE_PATH)
    for override in overrides:
        key_path, value = _parse_set_override(override)
        fragment = _nest(key_path, value)
        merged = _deep_merge(merged, fragment)
        sources[key_path] = _SET_OVERRIDE_SOURCE
    try:
        return Settings.model_validate(merged)
    except ValidationError as error:
        raise ConfigError(_format_validation_error(error, sources)) from error


def _load_defaults() -> tuple[dict[str, Any], dict[str, str]]:
    defaults_text = (
        resources.files("shikiru.config").joinpath("defaults.toml").read_text(encoding="utf-8")
    )
    return tomllib.loads(defaults_text), {}


def _parse_toml(path: Path, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        message = f"{source}: {error}"
        raise ConfigError(message) from error


def _record_sources(
    sources: dict[str, str], data: dict[str, Any], source: str, prefix: str = ""
) -> None:
    for key, value in data.items():
        key_path = f"{prefix}.{key}" if prefix else key
        sources[key_path] = source
        if isinstance(value, dict):
            _record_sources(sources, cast("dict[str, Any]", value), source, key_path)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(
                cast("dict[str, Any]", existing), cast("dict[str, Any]", value)
            )
        else:
            merged[key] = value
    return merged


def _nest(key_path: str, value: Any) -> dict[str, Any]:
    parts = key_path.split(".")
    fragment: dict[str, Any] = value
    for part in reversed(parts):
        fragment = {part: fragment}
    return fragment


def _parse_set_override(text: str) -> tuple[str, Any]:
    key_path, separator, raw_value = text.partition("=")
    if not separator or not key_path:
        message = f"{_SET_OVERRIDE_SOURCE}: {text!r} is not key.path=value"
        raise ConfigError(message)
    return key_path, _parse_set_value(key_path, raw_value)


def _parse_set_value(key_path: str, raw_value: str) -> Any:
    try:
        return tomllib.loads(f"v = {raw_value}")["v"]
    except tomllib.TOMLDecodeError:
        pass
    escaped = raw_value.replace("\\", "\\\\").replace('"', '\\"')
    try:
        return tomllib.loads(f'v = "{escaped}"')["v"]
    except tomllib.TOMLDecodeError as error:
        message = (
            f"{_SET_OVERRIDE_SOURCE}: {key_path}: {raw_value!r} is not a valid value ({error})"
        )
        raise ConfigError(message) from error


def _format_validation_error(error: ValidationError, sources: dict[str, str]) -> str:
    lines = [_format_single_error(err, sources) for err in error.errors()]
    return "\n".join(lines)


def _format_single_error(err: ErrorDetails, sources: dict[str, str]) -> str:
    dotted = ".".join(str(part) for part in err["loc"])
    source = _source_for(dotted, sources)
    return f"{source}: {dotted}: {err['msg']}"


def _source_for(dotted: str, sources: dict[str, str]) -> str:
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in sources:
            return sources[candidate]
    return "defaults.toml"
