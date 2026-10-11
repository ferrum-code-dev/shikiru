from __future__ import annotations

import pytest

from shikiru.domain.levels import UsageLevel, usage_level

_CONTEXT_THRESHOLDS = (40, 60)
_QUOTA_THRESHOLDS = (50, 90)


@pytest.mark.parametrize(
    ("used_pct", "thresholds", "level"),
    [
        (39, _CONTEXT_THRESHOLDS, UsageLevel.OK),
        (39.6, _CONTEXT_THRESHOLDS, UsageLevel.WARN),
        (40, _CONTEXT_THRESHOLDS, UsageLevel.WARN),
        (59, _CONTEXT_THRESHOLDS, UsageLevel.WARN),
        (59.6, _CONTEXT_THRESHOLDS, UsageLevel.ALERT),
        (60, _CONTEXT_THRESHOLDS, UsageLevel.ALERT),
        (49.4, _QUOTA_THRESHOLDS, UsageLevel.OK),
        (49.5, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (49.6, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (50, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (89.4, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (89.5, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
        (89.6, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
        (90, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
        (120, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
    ],
)
def test_usage_level_classifies_the_whole_percentage_shown(
    used_pct: float, thresholds: tuple[int, int], level: UsageLevel
) -> None:
    warn_pct, alert_pct = thresholds
    assert usage_level(used_pct, warn_pct=warn_pct, alert_pct=alert_pct) is level
