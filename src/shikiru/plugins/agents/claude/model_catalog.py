"""Claude Code initialize response model catalog parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from shikiru.agents.contracts import ModelCatalog, ModelChoice

_CONTROL_RESPONSE_TYPE = "control_response"
_SUCCESS_SUBTYPE = "success"
_INVALID_RESPONSE = "Claude Code initialize response has an invalid model catalog"
_INVALID_JSON = "Claude Code initialize output is not valid stream JSON"


def parse_model_catalog(output: str, request_id: str) -> ModelCatalog:
    """Parse only model choices from the matching Claude Code initialize response."""
    response = _matching_initialize_response(output, request_id)
    raw_models = response.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise TypeError(_INVALID_RESPONSE)
    return ModelCatalog(
        models=_catalog_choices(cast("list[object]", raw_models)),
        supports_runtime_default=True,
    )


def _matching_initialize_response(output: str, request_id: str) -> Mapping[str, Any]:
    response: Mapping[str, Any] | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            raw_message = json.loads(line)
        except json.JSONDecodeError as error:
            raise TypeError(_INVALID_JSON) from error
        if not isinstance(raw_message, Mapping):
            raise TypeError(_INVALID_RESPONSE)
        message = cast("Mapping[str, Any]", raw_message)
        control_response = message.get("response")
        if message.get("type") != _CONTROL_RESPONSE_TYPE or not isinstance(
            control_response, Mapping
        ):
            continue
        typed_response = cast("Mapping[str, Any]", control_response)
        if typed_response.get("request_id") != request_id:
            continue
        if typed_response.get("subtype") != _SUCCESS_SUBTYPE:
            raise TypeError(_INVALID_RESPONSE)
        initialize_response = typed_response.get("response")
        if not isinstance(initialize_response, Mapping):
            raise TypeError(_INVALID_RESPONSE)
        response = cast("Mapping[str, Any]", initialize_response)
        break
    if response is None:
        raise TypeError(_INVALID_RESPONSE)
    return response


def _catalog_choices(raw_models: list[object]) -> tuple[ModelChoice, ...]:
    choices: dict[str, ModelChoice] = {}
    for raw_model in raw_models:
        for choice in _model_choices(raw_model):
            existing = choices.get(choice.model)
            if existing is not None and existing.efforts != choice.efforts:
                raise TypeError(_INVALID_RESPONSE)
            choices[choice.model] = choice
    return tuple(choices.values())


def _model_choices(raw_model: object) -> tuple[ModelChoice, ...]:
    if not isinstance(raw_model, Mapping):
        raise TypeError(_INVALID_RESPONSE)
    model = cast("Mapping[str, Any]", raw_model)
    value = model.get("value")
    if not isinstance(value, str) or not value:
        raise TypeError(_INVALID_RESPONSE)
    efforts = _efforts(model)
    identifiers = [value]
    resolved = model.get("resolvedModel")
    if resolved is not None:
        if not isinstance(resolved, str) or not resolved:
            raise TypeError(_INVALID_RESPONSE)
        identifiers.append(resolved)
    unique_identifiers = tuple(dict.fromkeys(identifiers))
    return tuple(
        ModelChoice(model=identifier, efforts=efforts) for identifier in unique_identifiers
    )


def _efforts(model: Mapping[str, Any]) -> tuple[str, ...] | None:
    supported = model.get("supportsEffort")
    if supported is not None and not isinstance(supported, bool):
        raise TypeError(_INVALID_RESPONSE)
    if supported is False:
        return ()
    raw_efforts = model.get("supportedEffortLevels")
    if raw_efforts is None:
        return None
    if not isinstance(raw_efforts, list):
        raise TypeError(_INVALID_RESPONSE)
    efforts: list[str] = []
    for effort in cast("list[object]", raw_efforts):
        if not isinstance(effort, str) or not effort or effort in efforts:
            raise TypeError(_INVALID_RESPONSE)
        efforts.append(effort)
    return tuple(efforts)
