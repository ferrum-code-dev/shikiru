from __future__ import annotations

from datetime import timedelta

import pytest

from shikiru.ui.age import format_duration, format_short_age


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "now"),
        (0, "now"),
        (59, "now"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h"),
        (86399, "23h"),
        (86400, "1d"),
        (3 * 86400 + 5, "3d"),
    ],
)
def test_short_age_collapses_to_the_largest_whole_unit(seconds: int, expected: str) -> None:
    assert format_short_age(timedelta(seconds=seconds)) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (59.9, "59s"), (60, "1m"), (3599, "59m"), (3600, "1h 0m"), (11520, "3h 12m")],
)
def test_duration_keeps_the_two_largest_units_up_to_hours(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected
