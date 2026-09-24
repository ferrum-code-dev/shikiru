"""Codex rollout token usage parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from shikiru.agents.contracts import Usage
from shikiru.domain.runs import NO_USAGE

_EVENT_MESSAGE_TYPE = "event_msg"
_TOKEN_COUNT_TYPE = "token" + "_count"
_INFO_FIELD = "info"
_TOTAL_USAGE_FIELD = "total_token_usage"
_LAST_USAGE_FIELD = "last_token_usage"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cached_input_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_write_input_tokens"
_CONTEXT_WINDOW_FIELD = "model_context_window"
_PERCENT = 100


def rollout_usage(
    entries: Iterable[Mapping[str, Any]],
) -> Usage:
    """Report the latest rollout token count; context from its window, no priced cost."""
    latest = _latest_token_count(entries)
    if latest is None:
        return NO_USAGE
    total, last, context_window = latest
    input_tokens = _integer(total, _INPUT_TOKENS_FIELD)
    output_tokens = _integer(total, _OUTPUT_TOKENS_FIELD)
    cache_read_tokens = _integer(total, _CACHE_READ_TOKENS_FIELD)
    cache_write_tokens = _integer(total, _CACHE_WRITE_TOKENS_FIELD)
    context_used_pct = _context_used_pct(last, context_window)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=None,
        cost_basis="unknown",
        context_used_pct=context_used_pct,
    )


def latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None:
    """Return the latest total and per-turn token usage records."""
    return _latest_token_count(entries)


def _latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None:
    latest: tuple[Mapping[str, Any], Mapping[str, Any], int | None] | None = None
    for entry in entries:
        payload = _payload(entry)
        if payload.get("type") != _TOKEN_COUNT_TYPE:
            continue
        info = payload.get(_INFO_FIELD)
        if not isinstance(info, Mapping):
            continue
        typed_info = cast("Mapping[str, Any]", info)
        total = typed_info.get(_TOTAL_USAGE_FIELD)
        last = typed_info.get(_LAST_USAGE_FIELD)
        if isinstance(total, Mapping) and isinstance(last, Mapping):
            context_window = typed_info.get(_CONTEXT_WINDOW_FIELD)
            latest = (
                cast("Mapping[str, Any]", total),
                cast("Mapping[str, Any]", last),
                context_window if isinstance(context_window, int) else None,
            )
    return latest


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") != _EVENT_MESSAGE_TYPE:
        return {}
    payload = entry.get("payload")
    return cast("Mapping[str, Any]", payload) if isinstance(payload, Mapping) else {}


def _integer(entry: Mapping[str, Any], key: str) -> int:
    value = entry.get(key, 0)
    if not isinstance(value, int | float):
        return 0
    return int(value)


def _context_used_pct(last: Mapping[str, Any], context_window: int | None) -> float | None:
    if context_window is None or context_window <= 0 or _INPUT_TOKENS_FIELD not in last:
        return None
    tokens = _integer(last, _INPUT_TOKENS_FIELD) + _integer(last, _CACHE_READ_TOKENS_FIELD)
    return tokens / context_window * _PERCENT
