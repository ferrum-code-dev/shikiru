from __future__ import annotations

import pytest

from shikiru.domain.levels import UsageLevel, usage_level

_CONTEXT_WARN_PCT = 40
_CONTEXT_ALERT_PCT = 60


@pytest.mark.parametrize(
    ("used_pct", "level"),
    [
        (0, UsageLevel.OK),
        (39, UsageLevel.OK),
        (39.6, UsageLevel.WARN),
        (40, UsageLevel.WARN),
        (59, UsageLevel.WARN),
        (59.6, UsageLevel.ALERT),
        (60, UsageLevel.ALERT),
        (100, UsageLevel.ALERT),
    ],
)
def test_usage_level_classifies_the_whole_percentage_shown(
    used_pct: float, level: UsageLevel
) -> None:
    assert usage_level(used_pct, warn_pct=_CONTEXT_WARN_PCT, alert_pct=_CONTEXT_ALERT_PCT) is level
