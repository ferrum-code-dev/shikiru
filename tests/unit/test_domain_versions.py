from __future__ import annotations

import pytest

from shikiru.domain.versions import parse_version, version_shortfall


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tmux 3.7c", (3, 7)),
        ("tmux 3.3a", (3, 3)),
        ("tmux 3.10", (3, 10)),
        ("tmux next-3.5", (3, 5)),
        ("git version 2.55.0", (2, 55)),
        ("git version 2.39.5 (Apple Git-154)", (2, 39)),
        ("tmux master", None),
        ("", None),
    ],
)
def test_the_first_major_and_minor_are_read_from_whatever_the_tool_prints(
    text: str, expected: tuple[int, int] | None
) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("text", "minimum", "meets"),
    [
        ("tmux 3.3a", (3, 5), False),
        ("tmux 3.4", (3, 5), False),
        ("tmux 3.5", (3, 5), True),
        ("tmux 3.10", (3, 5), True),
        ("tmux 4.0", (3, 5), True),
        ("tmux master", (3, 5), False),
        ("git version 2.44.0", (2, 45), False),
        ("git version 2.45.0", (2, 45), True),
        ("git version 2.100.1", (2, 45), True),
    ],
)
def test_a_version_meets_a_floor_only_when_it_is_readable_and_numerically_not_older(
    text: str, minimum: tuple[int, int], meets: bool
) -> None:
    shortfall = version_shortfall(text, minimum)

    assert (shortfall is None) is meets
    if shortfall is not None:
        assert text in shortfall
