from __future__ import annotations

import pytest

from shikiru.ui.compact import format_cpu_pct


@pytest.mark.parametrize(
    ("cpu_pct", "expected"),
    [
        (0.0, "0.0%"),
        (0.26, "0.3%"),
        (9.94, "9.9%"),
        (10.0, "10%"),
        (37.4, "37%"),
        (100.0, "100%"),
    ],
)
def test_cpu_is_shown_with_a_decimal_only_below_ten_percent(cpu_pct: float, expected: str) -> None:
    assert format_cpu_pct(cpu_pct) == expected
