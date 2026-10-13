from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.runs import MISSING_TITLE_VALUE, NO_USAGE, context_title, cost_title

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.agents.contracts import Usage


@pytest.mark.parametrize(
    ("title", "field", "value", "expected"),
    [
        (context_title, "context_used_pct", None, MISSING_TITLE_VALUE),
        (context_title, "context_used_pct", 41.6, "42%"),
        (context_title, "context_used_pct", 0.0, "0%"),
        (cost_title, "cost_usd", None, MISSING_TITLE_VALUE),
        (cost_title, "cost_usd", 1.838, "$1.84"),
        (cost_title, "cost_usd", 0.0, "$0.00"),
    ],
)
def test_titles_show_the_rounded_value_or_a_dash(
    title: Callable[[Usage], str], field: str, value: float | None, expected: str
) -> None:
    assert title(replace(NO_USAGE, **{field: value})) == expected
