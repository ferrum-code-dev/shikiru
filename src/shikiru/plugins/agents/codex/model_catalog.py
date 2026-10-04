"""Codex CLI model catalog parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

_MODELS_FIELD = "models"
_MODEL_SLUG_FIELD = "slug"
_MODEL_CONTEXT_WINDOW_FIELD = "context_window"
_INVALID_CATALOG_MESSAGE = "Codex CLI bundled model catalog has an invalid structure"
_INVALID_JSON_MESSAGE = "Codex CLI bundled model catalog is not valid JSON"


def parse_bundled_context_windows(output: str) -> dict[str, int]:
    """Parse positive model context windows from Codex CLI's bundled model catalog."""
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
    models: dict[str, int] = {}
    for raw_model in cast("list[object]", raw_models):
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
            models[slug] = window
    return models
