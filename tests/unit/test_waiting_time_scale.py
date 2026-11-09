"""Unit tests for the calibrated test time scale."""

from __future__ import annotations

import pytest

from tests.support.waiting import MAXIMUM_TIME_SCALE, scale_from_calibration

_CALIBRATION_UNLOADED_SECONDS = 0.025

_CASES: list[tuple[float, float]] = [
    (_CALIBRATION_UNLOADED_SECONDS / 4, 1.0),
    (_CALIBRATION_UNLOADED_SECONDS, 1.0),
    (_CALIBRATION_UNLOADED_SECONDS * 2, 2.0),
    (_CALIBRATION_UNLOADED_SECONDS * 1000, MAXIMUM_TIME_SCALE),
]


@pytest.mark.parametrize(("batch_seconds", "expected"), _CASES)
def test_calibrated_scale_is_never_below_one_and_never_above_the_maximum(
    batch_seconds: float, expected: float
) -> None:
    assert scale_from_calibration(batch_seconds) == pytest.approx(expected)
