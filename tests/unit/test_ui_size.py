import pytest

from shikiru.ui.size import format_size

_KIB = 1024


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (0, "0 B"),
        (_KIB - 1, "1023 B"),
        (_KIB, "1.0 KiB"),
        (5 * _KIB**2 + _KIB**2 // 2, "5.5 MiB"),
        (3 * _KIB**4, "3.0 TiB"),
        (2048 * _KIB**4, "2048.0 TiB"),
    ],
)
def test_a_size_is_shown_in_the_largest_unit_that_keeps_it_at_or_above_one(
    size_bytes: int, expected: str
) -> None:
    assert format_size(size_bytes) == expected
