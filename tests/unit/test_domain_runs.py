from dataclasses import replace

import pytest

from shikiru.domain.runs import MISSING_TITLE_VALUE, NO_USAGE, context_title, cost_title


@pytest.mark.parametrize(
    ("context_used_pct", "expected"), [(None, MISSING_TITLE_VALUE), (41.6, "42%"), (0.0, "0%")]
)
def test_context_title_shows_the_rounded_share_or_a_dash(
    context_used_pct: float | None, expected: str
) -> None:
    assert context_title(replace(NO_USAGE, context_used_pct=context_used_pct)) == expected


@pytest.mark.parametrize(
    ("cost_usd", "expected"), [(None, MISSING_TITLE_VALUE), (1.838, "$1.84"), (0.0, "$0.00")]
)
def test_cost_title_shows_dollars_or_a_dash(cost_usd: float | None, expected: str) -> None:
    assert cost_title(replace(NO_USAGE, cost_usd=cost_usd)) == expected
