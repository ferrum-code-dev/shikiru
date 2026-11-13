from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import QuotaWindow
from shikiru.domain.levels import (
    UsageLevel,
    effective_used_pct,
    free_space_level,
    next_quota_reset,
    reset_is_known,
    usage_level,
)

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


_QUOTA_NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _window(resets_at: datetime | None) -> QuotaWindow:
    return QuotaWindow(
        name="5h", used_pct=100.0, resets_at=resets_at, observed_at=_QUOTA_NOW - timedelta(hours=1)
    )


@pytest.mark.parametrize(
    ("resets_at", "expected"),
    [
        pytest.param(_QUOTA_NOW - timedelta(seconds=1), None, id="expired"),
        pytest.param(_QUOTA_NOW, None, id="exactly-at-reset"),
        pytest.param(_QUOTA_NOW + timedelta(seconds=1), 100.0, id="future-reset"),
        pytest.param(None, 100.0, id="no-reset"),
    ],
)
def test_effective_used_pct_is_unknown_once_a_known_reset_has_passed(
    resets_at: datetime | None, expected: float | None
) -> None:
    assert effective_used_pct(_window(resets_at), _QUOTA_NOW) == expected


@pytest.mark.parametrize(
    ("resets_at", "known"),
    [
        pytest.param(_QUOTA_NOW - timedelta(seconds=1), False, id="expired"),
        pytest.param(_QUOTA_NOW, False, id="exactly-at-reset"),
        pytest.param(_QUOTA_NOW + timedelta(seconds=1), True, id="future-reset"),
        pytest.param(None, False, id="no-reset"),
    ],
)
def test_reset_is_known_only_while_the_reset_is_still_ahead(
    resets_at: datetime | None, known: bool
) -> None:
    assert reset_is_known(_window(resets_at), _QUOTA_NOW) is known


@pytest.mark.parametrize(
    ("windows", "expected"),
    [
        pytest.param([], None, id="no-windows"),
        pytest.param([_window(None)], None, id="no-reset"),
        pytest.param([_window(_QUOTA_NOW - timedelta(seconds=1))], None, id="already-passed"),
        pytest.param([_window(_QUOTA_NOW)], None, id="exactly-at-reset"),
        pytest.param(
            [_window(_QUOTA_NOW + timedelta(hours=2)), _window(_QUOTA_NOW + timedelta(hours=1))],
            _QUOTA_NOW + timedelta(hours=1),
            id="earliest-future-reset",
        ),
        pytest.param(
            [_window(_QUOTA_NOW - timedelta(hours=1)), _window(_QUOTA_NOW + timedelta(minutes=5))],
            _QUOTA_NOW + timedelta(minutes=5),
            id="skips-passed-resets",
        ),
    ],
)
def test_next_quota_reset_is_the_earliest_reset_still_ahead(
    windows: list[QuotaWindow], expected: datetime | None
) -> None:
    assert next_quota_reset(windows, _QUOTA_NOW) == expected
