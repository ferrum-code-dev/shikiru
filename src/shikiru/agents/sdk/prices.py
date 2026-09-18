"""`PriceTable`: per-model token prices and context windows, from a plugin's `prices.toml`."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_TOKENS_PER_MILLION = 1_000_000
_PERCENT = 100


@dataclass(frozen=True, slots=True)
class ModelPrices:
    """One model's USD price per 1M tokens, by kind, plus its context window in tokens."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    input_per_million: Annotated[float, Field(alias="input", strict=True)]
    cache_read_per_million: Annotated[float, Field(alias="cache_read", strict=True)]
    cache_write_per_million: Annotated[float, Field(alias="cache_write", strict=True)]
    output_per_million: Annotated[float, Field(alias="output", strict=True)]
    context_window: Annotated[int, Field(gt=0, strict=True)]


_MODEL_PRICES_BY_NAME = TypeAdapter(dict[str, ModelPrices])


class PriceTable:
    """Computes `cost_usd` and `context_used_pct` from a model's prices and token counts."""

    def __init__(self, prices: Mapping[str, ModelPrices]) -> None:
        """Hold `prices`, keyed by model name."""
        self._prices = dict(prices)

    @classmethod
    def from_toml(cls, path: Path) -> PriceTable:
        """Load a `prices.toml` file: one `[<model>]` table per model."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        try:
            prices = _MODEL_PRICES_BY_NAME.validate_python(data)
        except ValidationError as error:
            message = f"{path} has invalid price entries: {error}"
            raise ConfigError(message) from error
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
