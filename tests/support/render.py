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
    return " ".join("\n".join(rendered_lines(content, width=width)).split())


def rendered_lines(content: object, *, width: int) -> list[str]:
    """Render content at exactly `width` columns and return its lines, trailing blanks dropped."""
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    console.print(content)
    return [line.rstrip() for line in console.export_text().splitlines()]
