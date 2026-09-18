"""The one glyph set shared by the CLI and the dashboard (docs/design/06-operator.md §6.1).

Every glyph below renders in exactly one terminal cell. A base character whose default East
Asian Width is `Ambiguous` drifts to two cells in a CJK-wide terminal, so it is followed by
U+FE0E (the text-presentation variation selector) to pin it narrow; `is_single_cell` is the
check a unit test runs over every glyph registered here.
"""

from __future__ import annotations

import unicodedata

from shikiru.domain.tasks import Role

_TEXT_PRESENTATION_SELECTOR = "︎"
_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Cf"})
_WIDE_EAST_ASIAN_WIDTHS = frozenset({"W", "F"})
_AMBIGUOUS_EAST_ASIAN_WIDTH = "A"


def _narrow(char: str) -> str:
    if unicodedata.east_asian_width(char) == _AMBIGUOUS_EAST_ASIAN_WIDTH:
        return char + _TEXT_PRESENTATION_SELECTOR
    return char


def is_single_cell(glyph: str) -> bool:
    """Return whether `glyph` renders as exactly one terminal cell in every terminal."""
    cells = 0
    characters = list(glyph)
    for index, character in enumerate(characters):
        if character == _TEXT_PRESENTATION_SELECTOR:
            continue
        if unicodedata.category(character) in _ZERO_WIDTH_CATEGORIES:
            continue
        width = unicodedata.east_asian_width(character)
        if width in _WIDE_EAST_ASIAN_WIDTHS:
            return False
        followed_by_selector = (
            index + 1 < len(characters) and characters[index + 1] == _TEXT_PRESENTATION_SELECTOR
        )
        if width == _AMBIGUOUS_EAST_ASIAN_WIDTH and not followed_by_selector:
            return False
        cells += 1
    return cells == 1


STAGE_TRIAGE = "⌕"
STAGE_PLAN = _narrow("≡")
STAGE_IMPLEMENT = "⚒"
STAGE_VERIFY = _narrow("⚖")
STAGE_MERGE = "⑂"
STAGE_PR = "⇪"

ROLE_GLYPHS: dict[Role, str] = {
    Role.LEADER: _narrow("◆"),
    Role.IMPLEMENTER: _narrow("▲"),
    Role.HELPER: "✦",
}
GLYPH_OPERATOR = "⌨"
GLYPH_ENGINE = _narrow("⚙")

GLYPH_DONE = "✔"
GLYPH_REWORK = "↺"
GLYPH_CONSULT = "?"
GLYPH_PAUSED = _narrow("⏸")
GLYPH_QUOTA = "⧗"
GLYPH_STALLED = _narrow("⚠")
GLYPH_ESCALATED = "‼"
GLYPH_BLOCKED = "⊘"
GLYPH_PENDING = _narrow("·")

GAUGE_GLYPHS = (_narrow("○"), "◔", _narrow("◑"), "◕", _narrow("●"))

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

ALL_GLYPHS = (
    STAGE_TRIAGE,
    STAGE_PLAN,
    STAGE_IMPLEMENT,
    STAGE_VERIFY,
    STAGE_MERGE,
    STAGE_PR,
    *ROLE_GLYPHS.values(),
    GLYPH_OPERATOR,
    GLYPH_ENGINE,
    GLYPH_DONE,
    GLYPH_REWORK,
    GLYPH_CONSULT,
    GLYPH_PAUSED,
    GLYPH_QUOTA,
    GLYPH_STALLED,
    GLYPH_ESCALATED,
    GLYPH_BLOCKED,
    GLYPH_PENDING,
    *GAUGE_GLYPHS,
    *SPINNER_FRAMES,
)
