"""`PriceTable`: per-model token prices and context windows, from a plugin's `prices.toml`."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_TOKENS_PER_MILLION = 1_000_000
_PERCENT = 100


@dataclass(frozen=True, slots=True)
class ModelPrices:
    """One model's USD price per 1M tokens, by kind, plus its context window in tokens."""

    input_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float
    output_per_million: float
    context_window: int


def _model_prices_from_table(model: str, table: Mapping[str, Any]) -> ModelPrices:
    try:
        return ModelPrices(
            input_per_million=table["input"],
            cache_read_per_million=table["cache_read"],
            cache_write_per_million=table["cache_write"],
            output_per_million=table["output"],
            context_window=table["context_window"],
        )
    except KeyError as error:
        message = f"{model!r} price entry is missing {error}"
        raise ConfigError(message) from error


class PriceTable:
    """Computes `cost_usd` and `context_used_pct` from a model's prices and token counts."""

    def __init__(self, prices: Mapping[str, ModelPrices]) -> None:
        """Hold `prices`, keyed by model name."""
        self._prices = dict(prices)

    @classmethod
    def from_toml(cls, path: Path) -> PriceTable:
        """Load a `prices.toml` file: one `[<model>]` table per model."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        prices = {model: _model_prices_from_table(model, table) for model, table in data.items()}
        return cls(prices)

    def cost_usd(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        """Return the USD cost of this usage under `model`'s prices."""
        prices = self._prices_for(model)
        return (
            input_tokens * prices.input_per_million
            + output_tokens * prices.output_per_million
            + cache_read_tokens * prices.cache_read_per_million
            + cache_write_tokens * prices.cache_write_per_million
        ) / _TOKENS_PER_MILLION

    def context_used_pct(self, model: str, *, input_tokens: int, cache_tokens: int) -> float:
        """Return how much of `model`'s context window `input_tokens + cache_tokens` uses."""
        prices = self._prices_for(model)
        return (input_tokens + cache_tokens) / prices.context_window * _PERCENT

    def _prices_for(self, model: str) -> ModelPrices:
        prices = self._prices.get(model)
        if prices is None:
            message = f"no price entry for model {model!r}"
            raise ConfigError(message)
        return prices
