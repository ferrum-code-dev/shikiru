"""Rules for matching public model prices and pricing token usage."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from shikiru.agents.contracts import COST_BASIS_API_EQUIVALENT, TokenCounts, Usage

_MUSE_VENDOR = "meta"


def token_cost(pricing: Mapping[str, Any], tokens: TokenCounts) -> float | None:
    """Calculate the listed cost, or return unknown for incomplete or invalid prices."""
    keys = ("prompt", "completion", "input_cache_read", "input_cache_write")
    selected = dict(pricing)
    overrides = pricing.get("overrides", [])
    if not isinstance(overrides, Sequence) or isinstance(overrides, str | bytes):
        return None
    prompt_size = tokens.input + tokens.cache_read + tokens.cache_write
    eligible: list[tuple[int, Mapping[str, Any]]] = []
    for item in cast("Sequence[object]", overrides):
        if not isinstance(item, Mapping):
            return None
        override = cast("Mapping[str, Any]", item)
        threshold = override.get("min_prompt_tokens")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
            return None
        if prompt_size >= threshold:
            eligible.append((threshold, override))
    if eligible:
        selected.update(max(eligible, key=lambda item: item[0])[1])
    try:
        rates = [float(selected[key]) for key in keys]
    except KeyError, TypeError, ValueError, OverflowError:
        return None
    counts = (tokens.input, tokens.output, tokens.cache_read, tokens.cache_write)
    if any(not math.isfinite(rate) or rate < 0 for rate in rates) or any(
        count < 0 for count in counts
    ):
        return None
    return sum(count * rate for count, rate in zip(counts, rates, strict=True))


def model_price(models: Mapping[str, Any], vendor: str, name: str) -> Mapping[str, Any] | None:
    """Return the exact model's price, with the approved Muse version retry."""
    identifier = f"{vendor}/{name}"
    model = models.get(identifier)
    if model is None and vendor == _MUSE_VENDOR:
        if name.endswith("-1.0"):
            model = models.get(identifier[: -len("-1.0")])
        elif name.endswith(".0"):
            model = models.get(identifier[: -len(".0")])
    if not isinstance(model, Mapping):
        return None
    pricing = cast("Mapping[str, Any]", model).get("pricing")
    return cast("Mapping[str, Any]", pricing) if isinstance(pricing, Mapping) else None


def priced_usage(
    previous: Usage,
    current: Usage,
    prices: Mapping[str, Any],
    vendor: str,
    calls: Sequence[tuple[str, TokenCounts]],
) -> Usage:
    """Price new calls only when they account for all the newly reported tokens."""
    expected = (
        current.input_tokens - previous.input_tokens,
        current.output_tokens - previous.output_tokens,
        current.cache_read_tokens - previous.cache_read_tokens,
        current.cache_write_tokens - previous.cache_write_tokens,
    )
    observed = tuple(
        sum(getattr(tokens, field) for _, tokens in calls)
        for field in ("input", "output", "cache_read", "cache_write")
    )
    if not calls or observed != expected:
        return current
    if previous.cost_usd is None and any(
        (
            previous.input_tokens,
            previous.output_tokens,
            previous.cache_read_tokens,
            previous.cache_write_tokens,
        )
    ):
        return current
    if previous.cost_usd is not None and previous.cost_basis != COST_BASIS_API_EQUIVALENT:
        return current
    cost = previous.cost_usd or 0.0
    for name, tokens in calls:
        price = model_price(prices, vendor, name)
        amount = None if price is None else token_cost(price, tokens)
        if amount is None:
            return current
        cost += amount
    return replace(current, cost_usd=cost, cost_basis=COST_BASIS_API_EQUIVALENT)
