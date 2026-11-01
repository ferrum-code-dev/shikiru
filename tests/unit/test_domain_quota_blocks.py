from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import QuotaWindow
from shikiru.domain.quota_blocks import block_released

_BLOCKED_AT = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
_NOW = _BLOCKED_AT + timedelta(minutes=30)
_FUTURE = _BLOCKED_AT + timedelta(days=3)
_PAST = _BLOCKED_AT - timedelta(hours=1)
_FRESH = _BLOCKED_AT + timedelta(minutes=10)
_STORED = [
    QuotaWindow("5h", 11.0, _BLOCKED_AT + timedelta(hours=4), _BLOCKED_AT),
    QuotaWindow("7d", 100.0, _FUTURE, _BLOCKED_AT),
]


def _window(name: str, used: float, observed_at: datetime = _FRESH) -> QuotaWindow:
    return QuotaWindow(name, used, None, observed_at)


@pytest.mark.parametrize(
    ("reading", "released"),
    [
        pytest.param([_window("5h", 2), _window("7d", 0)], True, id="every-window-available"),
        pytest.param([_window("5h", 2), _window("7d", 100)], False, id="weekly-still-exhausted"),
        pytest.param([_window("5h", 100), _window("7d", 0)], False, id="five-hour-exhausted"),
        pytest.param([_window("5h", 2)], False, id="exhausted-window-not-covered"),
        pytest.param([], False, id="no-windows"),
        pytest.param(
            [_window("5h", 2, _PAST), _window("7d", 0, _PAST)], False, id="older-than-the-block"
        ),
        pytest.param(
            [_window("5h", 2, _BLOCKED_AT), _window("7d", 0, _BLOCKED_AT)],
            False,
            id="observed-at-the-block-instant",
        ),
        pytest.param(
            [_window("5h", 2), _window("7d", 0, _PAST)], False, id="covering-window-is-stale"
        ),
    ],
)
def test_a_block_is_released_only_by_a_fresh_reading_that_clears_every_exhausted_window(
    reading: list[QuotaWindow], released: bool
) -> None:
    assert (
        block_released(blocked_at=_BLOCKED_AT, stored=_STORED, reading=reading, now=_NOW)
        is released
    )


def test_a_window_whose_reset_has_passed_need_not_be_covered() -> None:
    stored = [QuotaWindow("7d", 100.0, _BLOCKED_AT + timedelta(minutes=5), _BLOCKED_AT)]

    assert block_released(
        blocked_at=_BLOCKED_AT, stored=stored, reading=[_window("5h", 2)], now=_NOW
    )


def test_a_block_with_no_recorded_windows_is_released_by_any_available_reading() -> None:
    assert block_released(blocked_at=_BLOCKED_AT, stored=[], reading=[_window("5h", 2)], now=_NOW)
