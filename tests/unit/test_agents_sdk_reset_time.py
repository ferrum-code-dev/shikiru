from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from shikiru.agents.sdk.reset_time import ResetTimeParser
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Callable

_NOW = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
_WARSAW = ZoneInfo("Europe/Warsaw")


def _epoch_expected(_now: datetime) -> datetime:
    return datetime.fromtimestamp(1767261600, tz=UTC)


def _iso_with_offset_expected(_now: datetime) -> datetime:
    return datetime(2026, 1, 2, 1, 10, tzinfo=UTC)


def _iso_naive_expected(_now: datetime) -> datetime:
    return datetime(2026, 1, 2, 3, 10, tzinfo=UTC)


def _in_3_hours_expected(now: datetime) -> datetime:
    return now + timedelta(hours=3)


def _in_45_minutes_expected(now: datetime) -> datetime:
    return now + timedelta(minutes=45)


def _in_2_days_expected(now: datetime) -> datetime:
    return now + timedelta(days=2)


def _clock_time_not_yet_passed_expected(now: datetime) -> datetime:
    return now.astimezone(_WARSAW).replace(hour=20, minute=0, second=0, microsecond=0)


def _clock_time_already_passed_expected(now: datetime) -> datetime:
    today = now.astimezone(_WARSAW).replace(hour=3, minute=10, second=0, microsecond=0)
    return today + timedelta(days=1)


def _clock_time_without_minutes_expected(now: datetime) -> datetime:
    return now.astimezone(_WARSAW).replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )


def _in_2_hours_30_minutes_expected(now: datetime) -> datetime:
    return now + timedelta(hours=2, minutes=30)


def _in_1_day_3_hours_expected(now: datetime) -> datetime:
    return now + timedelta(days=1, hours=3)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1767261600", _epoch_expected),
        ("2026-01-02T03:10:00+02:00", _iso_with_offset_expected),
        ("2026-01-02T03:10:00", _iso_naive_expected),
        ("in 3 hours", _in_3_hours_expected),
        ("in 45 minutes", _in_45_minutes_expected),
        ("in 2 days", _in_2_days_expected),
        ("in 2 hours 30 minutes", _in_2_hours_30_minutes_expected),
        ("in 1 day 3 hours", _in_1_day_3_hours_expected),
        (
            "You've hit your session limit · resets 8:00pm (Europe/Warsaw)",
            _clock_time_not_yet_passed_expected,
        ),
        ("resets 3:10am (Europe/Warsaw)", _clock_time_already_passed_expected),
        ("resets 3am (Europe/Warsaw)", _clock_time_without_minutes_expected),
    ],
)
def test_parse_recognises_every_supported_format(
    text: str, expected: Callable[[datetime], datetime]
) -> None:
    parser = ResetTimeParser(FakeClock(_NOW))

    result = parser.parse(text)

    assert result == expected(_NOW)


@pytest.mark.parametrize(
    "text",
    ["", "quota exceeded", "resets soon", "tomorrow", "resets 3:10am (Mars/Colony)"],
)
def test_parse_returns_none_for_unrecognised_text(text: str) -> None:
    parser = ResetTimeParser(FakeClock(_NOW))

    assert parser.parse(text) is None
