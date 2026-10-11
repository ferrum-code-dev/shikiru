from dataclasses import replace

import pytest

from shikiru.domain.runs import (
    MISSING_TITLE_VALUE,
    NO_USAGE,
    ContextLevel,
    context_level,
    context_title,
    cost_title,
)

_WARN_PCT = 40
_ALERT_PCT = 60


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


@pytest.mark.parametrize(
    ("used_pct", "level"),
    [
        (0, ContextLevel.OK),
        (39, ContextLevel.OK),
        (39.6, ContextLevel.WARN),
        (40, ContextLevel.WARN),
        (59, ContextLevel.WARN),
        (59.6, ContextLevel.ALERT),
        (60, ContextLevel.ALERT),
        (100, ContextLevel.ALERT),
    ],
)
def test_context_level_classifies_the_percentage_shown_in_the_title(
    used_pct: float, level: ContextLevel
) -> None:
    assert context_level(used_pct, warn_pct=_WARN_PCT, alert_pct=_ALERT_PCT) is level
