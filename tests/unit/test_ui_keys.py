from __future__ import annotations

import pytest

from shikiru.ui.keys import key_hint
from shikiru.ui.section import BOLD_STEEL
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

_ALL_KEYS = (
    "↑↓ select · Enter open · Esc back · Tab details · Ctrl+P actions · ← newer · → older · "
    "PgUp/PgDn page · End follow · Up/Down move · Left/Right choose · Escape: close · o: evidence"
)
_KEYS = (
    "↑↓", "Enter", "Esc", "Tab", "Ctrl+P", "←", "→", "PgUp/PgDn", "End", "Up/Down",
    "Left/Right", "Escape", "o",
)  # fmt: skip


def _styled(text: str, style: str) -> list[str]:
    hint = key_hint(text, SECONDARY_TEXT_COLOR)
    return [text[span.start : span.end] for span in hint.spans if span.style == style]


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        (_ALL_KEYS, _KEYS),
        ("Task browser: Endpoint tabs Entered Escalation, Mouse: click", ()),
        ("Select a session and press Enter. Esc cancels.", ("Enter", "Esc")),
    ],
)
def test_only_known_key_names_are_painted_and_the_text_is_unchanged(
    text: str, keys: tuple[str, ...]
) -> None:
    assert key_hint(text).plain == text
    assert _styled(text, BOLD_STEEL) == list(keys)
    base = key_hint(text, SECONDARY_TEXT_COLOR)
    assert base.spans[0].style == SECONDARY_TEXT_COLOR
    assert (base.spans[0].start, base.spans[0].end) == (0, len(text))
