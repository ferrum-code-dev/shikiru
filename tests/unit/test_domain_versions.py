from __future__ import annotations

import pytest

from shikiru.domain.versions import parse_version


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
