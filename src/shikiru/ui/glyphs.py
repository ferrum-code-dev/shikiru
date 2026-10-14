"""The one glyph set shared by the CLI and the dashboard (docs/design/06-operator.md §6.1).

Ambiguous-width characters (`◆ ▲ ≡ · ○ ◑ ●`) are drawn one cell wide, as Textual assumes;
terminals set to CJK ambiguous-wide are unsupported. A character with an emoji variant
(`⚖ ⚙ ⏸ ⚠`) carries U+FE0E, the text-presentation variation selector, because some terminals
render it as wide color emoji without it. `is_single_cell` is the check a unit test runs over
every glyph registered here.
"""

from __future__ import annotations

import unicodedata

from shikiru.domain.tasks import Role

_TEXT_PRESENTATION_SELECTOR = "︎"
_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Cf"})
_WIDE_EAST_ASIAN_WIDTHS = frozenset({"W", "F"})

_EMOJI_CAPABLE_CODE_POINTS = frozenset({"⚖", "⚙", "⏸", "⚠"})
"""The base characters we use that have an emoji-presentation variant (Unicode has no stdlib
property for this, so this is the small explicit set our own glyphs draw from)."""


def is_single_cell(glyph: str) -> bool:
    """Return whether `glyph` renders as exactly one terminal cell in every terminal."""
    cells = 0
    characters = list(glyph)
    for index, character in enumerate(characters):
        if character == _TEXT_PRESENTATION_SELECTOR:
            continue
        if unicodedata.category(character) in _ZERO_WIDTH_CATEGORIES:
            continue
        if unicodedata.east_asian_width(character) in _WIDE_EAST_ASIAN_WIDTHS:
            return False
        followed_by_selector = (
            index + 1 < len(characters) and characters[index + 1] == _TEXT_PRESENTATION_SELECTOR
        )
        if character in _EMOJI_CAPABLE_CODE_POINTS and not followed_by_selector:
            return False
        cells += 1
    return cells == 1


PANEL_KANJI = ("起", "承", "転", "結")
"""Decorative kanji for the strip's four panel positions (06 §6.2): ki-shō-ten-ketsu. Kanji
are decorative marks, not state glyphs, so they are wide and excluded from `ALL_GLYPHS`."""

STAGE_TRIAGE = "⌕"
STAGE_PLAN = "≡"
STAGE_IMPLEMENT = "⚒"
STAGE_VERIFY = "⚖︎"
STAGE_MERGE = "⑂"
STAGE_PR = "⇪"

ROLE_GLYPHS: dict[Role, str] = {
    Role.LEADER: "◆",
    Role.IMPLEMENTER: "▲",
    Role.HELPER: "✦",
}
GLYPH_OPERATOR = "⌨"
GLYPH_ENGINE = "⚙︎"

GLYPH_DONE = "✔"
GLYPH_REWORK = "↺"
GLYPH_CONSULT = "?"
GLYPH_PAUSED = "⏸︎"
GLYPH_QUOTA = "⧗"
GLYPH_STALLED = "⚠︎"
GLYPH_ESCALATED = "‼"
GLYPH_BLOCKED = "⊘"
GLYPH_PENDING = "·"
GLYPH_STOPPED = "✕"
GLYPH_CURRENT_STAGE = "\u203a"

GAUGE_GLYPHS = ("○", "◔", "◑", "◕", "●")

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
    GLYPH_STOPPED,
    GLYPH_CURRENT_STAGE,
    *GAUGE_GLYPHS,
    *SPINNER_FRAMES,
)
