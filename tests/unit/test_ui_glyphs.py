from __future__ import annotations

import pytest

from shikiru.ui.glyphs import ALL_GLYPHS, is_single_cell


def test_every_registered_glyph_is_a_single_terminal_cell() -> None:
    assert [glyph for glyph in ALL_GLYPHS if not is_single_cell(glyph)] == []


@pytest.mark.parametrize(
    ("glyph", "expected"),
    [
        ("a", True),
        ("?", True),
        ("◆", True),
        ("文", False),
        ("🔥", False),
        ("⚠", False),
        ("⚠︎", True),
    ],
)
def test_is_single_cell_rejects_wide_and_emoji_presentation_characters(
    glyph: str, expected: bool
) -> None:
    assert is_single_cell(glyph) is expected
