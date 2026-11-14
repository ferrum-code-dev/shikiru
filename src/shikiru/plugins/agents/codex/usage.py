"""Codex rollout token usage parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from shikiru.agents.contracts import Usage, subtract_optional
from shikiru.plugins.agents.codex.options import USAGE_EVENT_TYPE

_EVENT_MESSAGE_TYPE = "event_msg"
_INFO_FIELD = "info"
_TOTAL_USAGE_FIELD = "total_token_usage"
_LAST_USAGE_FIELD = "last_token_usage"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cached_input_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_write_input_tokens"
_CONTEXT_WINDOW_FIELD = "model_context_window"
_PERCENT = 100
_UNKNOWN_COST = "unknown"
_NO_WRITES = 0
_UNREPORTED_USAGE = Usage(None, None, None, None, None, _UNKNOWN_COST, None)


def rollout_usage(
    entries: Iterable[Mapping[str, Any]],
) -> Usage:
    """Report the latest rollout token count; context from its window, no priced cost."""
    latest = _latest_token_count(entries)
    if latest is None:
        return _UNREPORTED_USAGE
    total, last, context_window = latest
    return codex_usage(
        input_tokens=_optional_integer(total, _INPUT_TOKENS_FIELD),
        output_tokens=_optional_integer(total, _OUTPUT_TOKENS_FIELD),
        cached_input_tokens=_optional_integer(total, _CACHE_READ_TOKENS_FIELD),
        cache_write_tokens=_optional_integer(total, _CACHE_WRITE_TOKENS_FIELD),
        context_used_pct=_context_used_pct(last, context_window),
    )


def codex_usage(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    cache_write_tokens: int | None,
    context_used_pct: float | None,
) -> Usage:
    """Normalize Codex token fields: its input includes the cached reads, which are removed here.

    Uncached input is known only when cache writes are reported as zero. A non-zero write means
    the input overlap is not established by the recording, so uncached input stays unknown.
    """
    uncached_input_tokens = (
        subtract_optional(input_tokens, cached_input_tokens)
        if cache_write_tokens == _NO_WRITES
        else None
    )
    return Usage(
        uncached_input_tokens=uncached_input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=None,
        cost_basis=_UNKNOWN_COST,
        context_used_pct=context_used_pct,
    )


def latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None:
    """Return the latest total and per-turn token usage records."""
    return _latest_token_count(entries)


def token_count_observation_count(entries: Iterable[Mapping[str, Any]]) -> int:
    """Return the number of usable token-count observations in a rollout."""
    return sum(_token_count_observation(entry) is not None for entry in entries)


def _latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None:
    latest: tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None = None
    for entry in entries:
        observation = _token_count_observation(entry)
        if observation is not None:
            latest = observation
    return latest


def _token_count_observation(
    entry: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None:
    payload = _payload(entry)
    if payload.get("type") != USAGE_EVENT_TYPE:
        return None
    info = payload.get(_INFO_FIELD)
    if not isinstance(info, Mapping):
        return None
    typed_info = cast("Mapping[str, Any]", info)
    total = typed_info.get(_TOTAL_USAGE_FIELD)
    last = typed_info.get(_LAST_USAGE_FIELD)
    if not isinstance(total, Mapping) or not isinstance(last, Mapping):
        return None
    context_window = typed_info.get(_CONTEXT_WINDOW_FIELD)
    return (
        cast("Mapping[str, Any]", total),
        cast("Mapping[str, Any]", last),
        context_window if isinstance(context_window, int) else None,
    )


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") != _EVENT_MESSAGE_TYPE:
        return {}
    payload = entry.get("payload")
    return cast("Mapping[str, Any]", payload) if isinstance(payload, Mapping) else {}


def _optional_integer(entry: Mapping[str, Any], key: str) -> int | None:
    value = entry.get(key)
    return int(value) if isinstance(value, int | float) else None


def _integer(entry: Mapping[str, Any], key: str) -> int:
    value = entry.get(key, 0)
    if not isinstance(value, int | float):
        return 0
    return int(value)


def _context_used_pct(last: Mapping[str, Any], context_window: int | None) -> float | None:
    if context_window is None or context_window <= 0 or _INPUT_TOKENS_FIELD not in last:
        return None
    tokens = _integer(last, _INPUT_TOKENS_FIELD)
    return tokens / context_window * _PERCENT
