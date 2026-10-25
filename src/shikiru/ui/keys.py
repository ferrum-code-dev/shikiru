"""Key names painted in the shared cyan inside hint text."""

from __future__ import annotations

import re

from rich.text import Text

from shikiru.ui.section import BOLD_STEEL
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR, STYLE_BOLD

KEY_NAMES: tuple[str, ...] = (
    "Ctrl+P",
    "Ctrl+S",
    "Escape",
    "Esc",
    "Enter",
    "Tab",
    "PgUp/PgDn",
    "PgUp",
    "PgDn",
    "End",
    "Up/Down",
    "Left/Right",
)
KEY_SYMBOLS: tuple[str, ...] = ("↑↓", "↑", "↓", "←", "→")
KEY_LABELLED_CHARACTERS = "a-z?/"
BOLD_DISABLED_KEY = f"{STYLE_BOLD} {SECONDARY_TEXT_COLOR}"

_KEY_PATTERN = re.compile(
    "|".join(
        (
            rf"(?<!\w)(?:{'|'.join(re.escape(name) for name in KEY_NAMES)})(?!\w)",
            "|".join(re.escape(symbol) for symbol in KEY_SYMBOLS),
            rf"(?<!\w)[{KEY_LABELLED_CHARACTERS}](?=:)",
        )
    )
)


def key_hint(text: str, base_style: str = "", *, enabled: bool = True) -> Text:
    """Return `text` in `base_style` with every known key name in the cyan key style.

    A disabled hint (its key does nothing now) paints the keys bold grey instead.
    """
    key_style = BOLD_STEEL if enabled else BOLD_DISABLED_KEY
    hint = Text(text)
    if base_style:
        hint.stylize(base_style)
    for match in _KEY_PATTERN.finditer(text):
        hint.stylize(key_style, match.start(), match.end())
    return hint


def append_key_hint(target: Text, text: str, base_style: str = "") -> None:
    """Append `text` to `target` as `key_hint` does."""
    target.append_text(key_hint(text, base_style))
