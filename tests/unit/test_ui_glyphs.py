from __future__ import annotations

import pytest

from shikiru.ui.glyphs import ALL_GLYPHS, is_single_cell


@pytest.mark.parametrize("glyph", ALL_GLYPHS)
def test_every_registered_glyph_is_a_single_terminal_cell(glyph: str) -> None:
    assert is_single_cell(glyph)


@pytest.mark.parametrize(
    ("glyph", "expected"),
    [
        ("a", True),
        ("?", True),
        ("文", False),
        ("🔥", False),
        ("◆", False),
        ("◆︎", True),
    ],
)
def test_is_single_cell_rejects_wide_ambiguous_and_emoji_presentation(
    glyph: str, expected: bool
) -> None:
    assert is_single_cell(glyph) is expected
