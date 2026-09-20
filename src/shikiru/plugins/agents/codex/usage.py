"""Codex rollout token usage parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import Usage

if TYPE_CHECKING:
    from shikiru.agents.sdk.prices import PriceTable

_EVENT_MESSAGE_TYPE = "event_msg"
_TOKEN_COUNT_TYPE = "token" + "_count"
_INFO_FIELD = "info"
_TOTAL_USAGE_FIELD = "total_token_usage"
_LAST_USAGE_FIELD = "last_token_usage"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cached_input_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_write_input_tokens"
_COST_BASIS_SUBSCRIPTION = "api_equivalent"
_COST_BASIS_API_KEY = "billed"


def rollout_usage(
    entries: Iterable[Mapping[str, Any]],
    *,
    model: str,
    prices: PriceTable,
    api_key_auth: bool,
) -> Usage:
    """Compute cumulative usage from the latest Codex rollout token count."""
    latest = _latest_token_count(entries)
    if latest is None:
        return _empty_usage(api_key_auth)
    total = latest[0]
    last = latest[1]
    input_tokens = _integer(total, _INPUT_TOKENS_FIELD)
    output_tokens = _integer(total, _OUTPUT_TOKENS_FIELD)
    cache_read_tokens = _integer(total, _CACHE_READ_TOKENS_FIELD)
    cache_write_tokens = _integer(total, _CACHE_WRITE_TOKENS_FIELD)
    has_usage = input_tokens or output_tokens or cache_read_tokens or cache_write_tokens
    cost_usd = (
        prices.cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        if has_usage
        else None
    )
    context_used_pct = (
        prices.context_used_pct(
            model,
            input_tokens=_integer(last, _INPUT_TOKENS_FIELD),
            cache_tokens=0,
        )
        if _INPUT_TOKENS_FIELD in last
        else None
    )
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
        cost_basis=_COST_BASIS_API_KEY if api_key_auth else _COST_BASIS_SUBSCRIPTION,
        context_used_pct=context_used_pct,
    )


def latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Return the latest total and per-turn token usage records."""
    return _latest_token_count(entries)


def _latest_token_count(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    latest: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None
    for entry in entries:
        payload = _payload(entry)
        if payload.get("type") != _TOKEN_COUNT_TYPE:
            continue
        info = payload.get(_INFO_FIELD)
        if not isinstance(info, Mapping):
            info = payload
        typed_info = cast("Mapping[str, Any]", info)
        total = typed_info.get(_TOTAL_USAGE_FIELD)
        last = typed_info.get(_LAST_USAGE_FIELD)
        if isinstance(total, Mapping) and isinstance(last, Mapping):
            latest = (cast("Mapping[str, Any]", total), cast("Mapping[str, Any]", last))
    return latest


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") == _EVENT_MESSAGE_TYPE:
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            return cast("Mapping[str, Any]", payload)
    nested_payload = entry.get("payload")
    if isinstance(nested_payload, Mapping):
        typed_payload = cast("Mapping[str, Any]", nested_payload)
        if typed_payload.get("type") == _TOKEN_COUNT_TYPE:
            return typed_payload
    return entry


def _empty_usage(api_key_auth: bool) -> Usage:
    return Usage(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=None,
        cost_basis=_COST_BASIS_API_KEY if api_key_auth else _COST_BASIS_SUBSCRIPTION,
        context_used_pct=None,
    )


def _integer(entry: Mapping[str, Any], key: str) -> int:
    value = entry.get(key, 0)
    if not isinstance(value, int | float):
        return 0
    return int(value)
