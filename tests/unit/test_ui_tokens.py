from __future__ import annotations

import pytest

from shikiru.ui.tokens import GAUGE_ALERT_COLOR, GAUGE_OK_COLOR, GAUGE_WARN_COLOR, gauge_color


@pytest.mark.parametrize(
    ("used_pct", "expected"),
    [
        (0, GAUGE_OK_COLOR),
        (69.9, GAUGE_OK_COLOR),
        (70, GAUGE_WARN_COLOR),
        (89.9, GAUGE_WARN_COLOR),
        (90, GAUGE_ALERT_COLOR),
        (100, GAUGE_ALERT_COLOR),
    ],
)
def test_gauge_color_thresholds(used_pct: float, expected: str) -> None:
    assert gauge_color(used_pct) == expected
