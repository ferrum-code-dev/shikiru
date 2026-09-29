"""Terminal-independent rendering of Rich and Textual content for assertions."""

from __future__ import annotations

import io

from rich.console import Console
from rich.text import Text

RENDER_WIDTH = 200


def plain_text(content: object, *, width: int = RENDER_WIDTH) -> str:
    """Render content to text with layout whitespace collapsed, ignoring terminal settings."""
    if isinstance(content, Text):
        return " ".join(content.plain.split())
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    console.print(content)
    return " ".join(console.export_text().split())
