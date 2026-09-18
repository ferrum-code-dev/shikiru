from __future__ import annotations

from pathlib import Path

import pytest

from shikiru.agents.sdk.prices import PriceTable
from shikiru.errors import ConfigError

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "agents" / "testkit" / "prices.toml"


@pytest.fixture
def table() -> PriceTable:
    return PriceTable.from_toml(_FIXTURE_PATH)


_MIXED_TOKENS = (500_000, 200_000, 100_000, 50_000)
_MIXED_COST = (
    _MIXED_TOKENS[0] * 15.0 / 1e6
    + _MIXED_TOKENS[1] * 75.0 / 1e6
    + _MIXED_TOKENS[2] * 1.5 / 1e6
    + _MIXED_TOKENS[3] * 18.75 / 1e6
)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ((0, 0, 0, 0), 0.0),
        ((1_000_000, 0, 0, 0), 15.0),
        ((0, 1_000_000, 0, 0), 75.0),
        ((0, 0, 1_000_000, 0), 1.5),
        ((0, 0, 0, 1_000_000), 18.75),
        (_MIXED_TOKENS, _MIXED_COST),
    ],
)
def test_cost_usd_sums_each_token_kind_at_its_own_price(
    table: PriceTable, tokens: tuple[int, int, int, int], expected: float
) -> None:
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = tokens

    cost = table.cost_usd(
        "opus",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )

    assert cost == pytest.approx(expected)


@pytest.mark.parametrize(
    ("input_tokens", "cache_tokens", "expected_pct"),
    [
        (0, 0, 0.0),
        (100_000, 0, 50.0),
        (0, 200_000, 100.0),
        (50_000, 50_000, 50.0),
    ],
)
def test_context_used_pct_is_tokens_over_context_window(
    table: PriceTable, input_tokens: int, cache_tokens: int, expected_pct: float
) -> None:
    pct = table.context_used_pct("opus", input_tokens=input_tokens, cache_tokens=cache_tokens)

    assert pct == pytest.approx(expected_pct)


def test_unknown_model_raises_naming_it(table: PriceTable) -> None:
    with pytest.raises(ConfigError, match="unknown-model"):
        table.cost_usd(
            "unknown-model",
            input_tokens=1,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
