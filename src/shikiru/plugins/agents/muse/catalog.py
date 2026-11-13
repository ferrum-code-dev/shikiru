"""Muse Code model catalog read from one `muse serve` JSON-RPC exchange."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import ModelCatalog, ModelChoice
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.options import (
    MODEL_CATALOG_TIMEOUT_SECONDS,
    MUSE_COMMAND,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    SERVE_ARGV,
)

if TYPE_CHECKING:
    from shikiru.process import LineChannel, SubprocessRunner

_DISTRIBUTION_NAME = "shikiru-cli"
_CLIENT_NAME = "shikiru"
_CLIENT_TITLE = "Shikiru"
_JSONRPC_VERSION = "2.0"
_INITIALIZE_METHOD = "initialize"
_INITIALIZED_METHOD = "initialized"
_MODEL_LIST_METHOD = "model/list"
_INITIALIZE_ID = 1
_MODEL_LIST_ID = 2
_JSONRPC_FIELD = "jsonrpc"
_ID_FIELD = "id"
_METHOD_FIELD = "method"
_PARAMS_FIELD = "params"
_RESULT_FIELD = "result"
_ERROR_FIELD = "error"
_MODELS_FIELD = "models"
_MODEL_ID_FIELD = "modelId"
_VARIANTS_FIELD = "variants"
_CONTEXT_LIMIT_FIELD = "contextLimit"
_COMPACT_SEPARATORS = (",", ":")
_NO_DIAGNOSTIC_DETAIL = "no diagnostic output"
_UNREADABLE_OUTPUT_MESSAGE = f"{MUSE_COMMAND} serve printed an unreadable model catalog"
_EMPTY_CATALOG_MESSAGE = (
    f"{MUSE_COMMAND} serve returned no models; the catalog is not authoritative"
)


@dataclass(frozen=True, slots=True)
class MuseCatalog:
    """Muse's model catalog with the context window, in tokens, of each model it lists."""

    catalog: ModelCatalog
    context_windows: Mapping[str, int]


async def read_model_catalog(
    runner: SubprocessRunner,
    environment: Mapping[str, str],
    *,
    timeout: float = MODEL_CATALOG_TIMEOUT_SECONDS,
) -> MuseCatalog:
    """Read Muse's model catalog with one `muse serve` process that ends when the exchange does.

    The model list is requested only after `initialize` has succeeded. Any failure, including a
    timeout, is returned as a catalog error rather than raised.
    """
    try:
        result = await runner.exchange(
            SERVE_ARGV,
            timeout=timeout,
            talk=_converse,
            env={**environment, NO_AUTO_UPDATE_ENV_VAR: NO_AUTO_UPDATE_VALUE},
        )
    except (OSError, ShikiruError, UnicodeDecodeError) as error:
        return _error_catalog(f"{MUSE_COMMAND} model discovery failed: {error}")
    if result.returncode != 0:
        detail = result.stderr.strip() or _NO_DIAGNOSTIC_DETAIL
        return _error_catalog(
            f"{MUSE_COMMAND} serve exited with code {result.returncode}: {detail}"
        )
    try:
        return parse_model_catalog(result.stdout)
    except ShikiruError as error:
        return _error_catalog(str(error))


def parse_model_catalog(output: str) -> MuseCatalog:
    """Return the models and context windows in `muse serve`'s `model/list` reply to `output`.

    Raises `ShikiruError` when the initialize or model list reply is missing or failed, or when the
    catalog is empty, because an empty reply is not an authoritative list of models.
    """
    responses = _responses_by_id(output)
    _successful_result(responses, _INITIALIZE_ID, _INITIALIZE_METHOD)
    result = _successful_result(responses, _MODEL_LIST_ID, _MODEL_LIST_METHOD)
    entries = _model_entries(result)
    if not entries:
        raise ShikiruError(_EMPTY_CATALOG_MESSAGE)
    return MuseCatalog(
        catalog=ModelCatalog(
            models=tuple(choice for choice, _ in entries),
            complete=True,
            supports_runtime_default=True,
        ),
        context_windows={choice.model: limit for choice, limit in entries},
    )


def _error_catalog(message: str) -> MuseCatalog:
    return MuseCatalog(catalog=ModelCatalog(error=message), context_windows={})


async def _converse(channel: LineChannel) -> None:
    await channel.send(_encode(_initialize_message()))
    reply = await _reply_to(channel, _INITIALIZE_ID)
    if reply is None or not _succeeded(reply):
        return
    await channel.send(_encode(_initialized_message()))
    await channel.send(_encode(_model_list_message()))
    await _reply_to(channel, _MODEL_LIST_ID)


async def _reply_to(channel: LineChannel, identifier: int) -> Mapping[str, Any] | None:
    """Return the reply to request `identifier`, or `None` once the output ends."""
    while (line := await channel.receive()) is not None:
        if not line.strip():
            continue
        message = _message(line)
        if message.get(_ID_FIELD) == identifier:
            return message
    return None


def _succeeded(reply: Mapping[str, Any]) -> bool:
    return _RESULT_FIELD in reply and _ERROR_FIELD not in reply


def _initialize_message() -> dict[str, Any]:
    client_info = {
        "name": _CLIENT_NAME,
        "title": _CLIENT_TITLE,
        "version": version(_DISTRIBUTION_NAME),
    }
    return {
        _JSONRPC_FIELD: _JSONRPC_VERSION,
        _ID_FIELD: _INITIALIZE_ID,
        _METHOD_FIELD: _INITIALIZE_METHOD,
        _PARAMS_FIELD: {
            "clientInfo": client_info,
            "capabilities": {"requestedCapabilities": []},
        },
    }


def _initialized_message() -> dict[str, Any]:
    return {_JSONRPC_FIELD: _JSONRPC_VERSION, _METHOD_FIELD: _INITIALIZED_METHOD}


def _model_list_message() -> dict[str, Any]:
    return {
        _JSONRPC_FIELD: _JSONRPC_VERSION,
        _ID_FIELD: _MODEL_LIST_ID,
        _METHOD_FIELD: _MODEL_LIST_METHOD,
        _PARAMS_FIELD: {},
    }


def _encode(message: Mapping[str, Any]) -> str:
    return json.dumps(message, separators=_COMPACT_SEPARATORS)


def _responses_by_id(output: str) -> dict[object, Mapping[str, Any]]:
    responses: dict[object, Mapping[str, Any]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        message = _message(line)
        identifier = message.get(_ID_FIELD)
        if isinstance(identifier, int | str):
            responses[identifier] = message
    return responses


def _message(line: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as error:
        raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE) from error
    return _mapping(decoded)


def _successful_result(
    responses: Mapping[object, Mapping[str, Any]], identifier: int, method: str
) -> Mapping[str, Any]:
    response = responses.get(identifier)
    if response is None:
        message = f"{MUSE_COMMAND} serve sent no reply to {method}"
        raise ShikiruError(message)
    if _ERROR_FIELD in response:
        error = json.dumps(response[_ERROR_FIELD], sort_keys=True)
        message = f"{MUSE_COMMAND} serve rejected {method}: {error}"
        raise ShikiruError(message)
    return _mapping(response.get(_RESULT_FIELD))


def _model_entries(result: Mapping[str, Any]) -> tuple[tuple[ModelChoice, int], ...]:
    raw_models = result.get(_MODELS_FIELD)
    if not isinstance(raw_models, list):
        raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE)
    entries: list[tuple[ModelChoice, int]] = []
    identifiers: set[str] = set()
    for raw_model in cast("list[object]", raw_models):
        choice, context_limit = _model_entry(raw_model)
        if choice.model in identifiers:
            raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE)
        identifiers.add(choice.model)
        entries.append((choice, context_limit))
    return tuple(entries)


def _model_entry(raw_model: object) -> tuple[ModelChoice, int]:
    model = _mapping(raw_model)
    identifier = model.get(_MODEL_ID_FIELD)
    variants = model.get(_VARIANTS_FIELD)
    context_limit = model.get(_CONTEXT_LIMIT_FIELD)
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(variants, list)
        or not _is_context_limit(context_limit)
    ):
        raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE)
    choice = ModelChoice(model=identifier, efforts=_efforts(cast("list[object]", variants)))
    return choice, cast("int", context_limit)


def _is_context_limit(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _efforts(variants: list[object]) -> tuple[str, ...]:
    efforts: list[str] = []
    for variant in variants:
        if not isinstance(variant, str) or not variant or variant in efforts:
            raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE)
        efforts.append(variant)
    return tuple(efforts)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShikiruError(_UNREADABLE_OUTPUT_MESSAGE)
    return cast("Mapping[str, Any]", value)
