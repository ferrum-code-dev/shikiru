"""Claude Code transcript usage parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import Usage

if TYPE_CHECKING:
    from shikiru.agents.sdk.prices import PriceTable

_ASSISTANT_TYPE = "assistant"
_MESSAGE_FIELD = "message"
_MESSAGE_ID_FIELD = "id"
_USAGE_FIELD = "usage"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cache_read_input_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_creation_input_tokens"
_COST_BASIS_SUBSCRIPTION = "api_equivalent"
_COST_BASIS_API_KEY = "billed"


def transcript_usage(
    entries: Iterable[Mapping[str, Any]],
    *,
    model: str,
    prices: PriceTable,
    api_key_auth: bool,
) -> Usage:
    """Compute cumulative usage from Claude assistant transcript entries."""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    last_input_tokens: int | None = None
    last_cache_tokens: int | None = None
    seen_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != _ASSISTANT_TYPE:
            continue
        message = entry.get(_MESSAGE_FIELD)
        if not isinstance(message, Mapping):
            continue
        typed_message = cast("Mapping[str, Any]", message)
        message_id = typed_message.get(_MESSAGE_ID_FIELD)
        if isinstance(message_id, str):
            if message_id in seen_ids:
                continue
            seen_ids.add(message_id)
        raw_usage = typed_message.get(_USAGE_FIELD)
        if not isinstance(raw_usage, Mapping):
            continue
        typed_usage = cast("Mapping[str, Any]", raw_usage)
        turn_input = _integer(typed_usage, _INPUT_TOKENS_FIELD)
        turn_output = _integer(typed_usage, _OUTPUT_TOKENS_FIELD)
        turn_cache_read = _integer(typed_usage, _CACHE_READ_TOKENS_FIELD)
        turn_cache_write = _integer(typed_usage, _CACHE_WRITE_TOKENS_FIELD)
        input_tokens += turn_input
        output_tokens += turn_output
        cache_read_tokens += turn_cache_read
        cache_write_tokens += turn_cache_write
        last_input_tokens = turn_input
        last_cache_tokens = turn_cache_read + turn_cache_write
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
            input_tokens=last_input_tokens,
            cache_tokens=last_cache_tokens,
        )
        if last_input_tokens is not None and last_cache_tokens is not None
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


def _integer(entry: Mapping[str, Any], key: str) -> int:
    value = entry.get(key, 0)
    if not isinstance(value, int | float):
        return 0
    return int(value)
