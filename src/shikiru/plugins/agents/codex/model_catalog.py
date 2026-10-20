"""Codex CLI model catalog parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from shikiru.agents.contracts import ModelChoice

_MODELS_FIELD = "models"
_MODEL_SLUG_FIELD = "slug"
_MODEL_CONTEXT_WINDOW_FIELD = "context_window"
_MODEL_VISIBILITY_FIELD = "visibility"
_MODEL_SUPPORTED_IN_API_FIELD = "supported_in_api"
_MODEL_REASONING_LEVELS_FIELD = "supported_reasoning_levels"
_MODEL_EFFORT_FIELD = "effort"
_LIST_VISIBILITY = "list"
_HIDDEN_VISIBILITY = "hide"
_INVALID_CATALOG_MESSAGE = "Codex CLI model catalog has an invalid structure"
_INVALID_JSON_MESSAGE = "Codex CLI model catalog is not valid JSON"


def parse_bundled_context_windows(output: str) -> dict[str, int]:
    """Parse positive model context windows from Codex CLI's bundled model catalog."""
    windows: dict[str, int] = {}
    for raw_model in _bundled_models(output):
        if not isinstance(raw_model, Mapping):
            continue
        model = cast("Mapping[str, Any]", raw_model)
        slug = model.get(_MODEL_SLUG_FIELD)
        window = model.get(_MODEL_CONTEXT_WINDOW_FIELD)
        if (
            isinstance(slug, str)
            and isinstance(window, int)
            and not isinstance(window, bool)
            and window > 0
        ):
            windows[slug] = window
    return windows


def parse_bundled_model_choices(output: str) -> tuple[ModelChoice, ...]:
    """Parse API-supported models and only their reported effort choices."""
    return parse_catalog(output)[0]


def parse_catalog(output: str) -> tuple[tuple[ModelChoice, ...], dict[str, int]]:
    """Parse model choices and context windows from one Codex catalog payload."""
    choices: list[ModelChoice] = []
    seen: set[str] = set()
    context_windows: dict[str, int] = {}
    for raw_model in _bundled_models(output):
        if not isinstance(raw_model, Mapping):
            raise TypeError(_INVALID_CATALOG_MESSAGE)
        model = cast("Mapping[str, Any]", raw_model)
        slug = model.get(_MODEL_SLUG_FIELD)
        window = model.get(_MODEL_CONTEXT_WINDOW_FIELD)
        if (
            isinstance(slug, str)
            and isinstance(window, int)
            and not isinstance(window, bool)
            and window > 0
        ):
            context_windows[slug] = window
        choice = _model_choice(model)
        if choice is None:
            continue
        if choice.model in seen:
            raise TypeError(_INVALID_CATALOG_MESSAGE)
        seen.add(choice.model)
        choices.append(choice)
    return tuple(choices), context_windows


def _bundled_models(output: str) -> list[object]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise TypeError(_INVALID_JSON_MESSAGE) from error
    if not isinstance(payload, Mapping):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    typed_payload = cast("Mapping[str, Any]", payload)
    raw_models = typed_payload.get(_MODELS_FIELD)
    if not isinstance(raw_models, list):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    return cast("list[object]", raw_models)


def _model_choice(raw_model: object) -> ModelChoice | None:
    if not isinstance(raw_model, Mapping):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    model = cast("Mapping[str, Any]", raw_model)
    visibility = model.get(_MODEL_VISIBILITY_FIELD)
    if not isinstance(visibility, str):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    if visibility not in {_LIST_VISIBILITY, _HIDDEN_VISIBILITY}:
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    supported_in_api = model.get(_MODEL_SUPPORTED_IN_API_FIELD)
    if not isinstance(supported_in_api, bool):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    if not supported_in_api:
        return None
    slug = model.get(_MODEL_SLUG_FIELD)
    if not isinstance(slug, str) or not slug:
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    return ModelChoice(
        model=slug,
        efforts=_effort_choices(model),
        offered=visibility == _LIST_VISIBILITY,
    )


def _effort_choices(model: Mapping[str, Any]) -> tuple[str, ...] | None:
    raw_levels = model.get(_MODEL_REASONING_LEVELS_FIELD)
    if raw_levels is None:
        return None
    if not isinstance(raw_levels, list):
        raise TypeError(_INVALID_CATALOG_MESSAGE)
    effort_values: list[str] = []
    for raw_level in cast("list[object]", raw_levels):
        if not isinstance(raw_level, Mapping):
            raise TypeError(_INVALID_CATALOG_MESSAGE)
        effort = cast("Mapping[str, Any]", raw_level).get(_MODEL_EFFORT_FIELD)
        if not isinstance(effort, str) or not effort or effort in effort_values:
            raise TypeError(_INVALID_CATALOG_MESSAGE)
        effort_values.append(effort)
    return tuple(effort_values)
