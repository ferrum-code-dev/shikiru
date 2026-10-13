from __future__ import annotations

import pytest

from shikiru.domain.levels import UsageLevel, free_space_level, usage_level

_CONTEXT_THRESHOLDS = (40, 60)
_QUOTA_THRESHOLDS = (50, 90)
_DISK_WARN_PCT = 10
_DISK_ALERT_PCT = 5


@pytest.mark.parametrize(
    ("used_pct", "thresholds", "level"),
    [
        (39, _CONTEXT_THRESHOLDS, UsageLevel.OK),
        (39.6, _CONTEXT_THRESHOLDS, UsageLevel.WARN),
        (59.6, _CONTEXT_THRESHOLDS, UsageLevel.ALERT),
        (49.4, _QUOTA_THRESHOLDS, UsageLevel.OK),
        (49.5, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (89.4, _QUOTA_THRESHOLDS, UsageLevel.WARN),
        (89.5, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
        (120, _QUOTA_THRESHOLDS, UsageLevel.ALERT),
    ],
)
def test_usage_level_classifies_the_whole_percentage_shown(
    used_pct: float, thresholds: tuple[int, int], level: UsageLevel
) -> None:
    warn_pct, alert_pct = thresholds
    assert usage_level(used_pct, warn_pct=warn_pct, alert_pct=alert_pct) is level


_DISK_TOTAL_BYTES = 10_000


@pytest.mark.parametrize(
    ("free_bytes", "level"),
    [
        (1_000, UsageLevel.OK),
        (999, UsageLevel.WARN),
        (500, UsageLevel.WARN),
        (499, UsageLevel.ALERT),
        (0, UsageLevel.ALERT),
    ],
)
def test_free_space_is_warn_below_ten_percent_and_alert_below_five_percent(
    free_bytes: int, level: UsageLevel
) -> None:
    assert (
        free_space_level(
            free_bytes, _DISK_TOTAL_BYTES, warn_pct=_DISK_WARN_PCT, alert_pct=_DISK_ALERT_PCT
        )
        is level
    )
