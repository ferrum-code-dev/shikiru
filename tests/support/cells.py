"""Per-character bold and colour readouts of rendered Textual widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.color import Color
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import Select, Static
from textual.widgets._select import SelectCurrent

from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
)

if TYPE_CHECKING:
    from rich.color import Color as RichColor
    from rich.style import Style as RichStyle

Style = tuple[bool, str | None]
Piece = tuple[str, Style]
Cell = tuple[str, Style]

WHITE_BOLD: Style = (True, PRIMARY_TEXT_COLOR)
WHITE: Style = (False, PRIMARY_TEXT_COLOR)
CYAN_BOLD: Style = (True, STEEL_BLUE)
CYAN: Style = (False, STEEL_BLUE)
GREEN_BOLD: Style = (True, SAGE)
GREY: Style = (False, SECONDARY_TEXT_COLOR)
DISABLED_KEY: Style = (True, SECONDARY_TEXT_COLOR)
AMBER_TEXT: Style = (False, AMBER)
ALERT_TEXT: Style = (False, ALERT_COLOR)
BLANK = " "
SEPARATOR: Piece = (" \u00b7 ", GREY)


def group(*pieces: Piece) -> tuple[Piece, ...]:
    """Collect the pieces of one ` \u00b7 `-separated group."""
    return pieces


def line(*groups: tuple[Piece, ...]) -> list[Cell]:
    """Expand groups joined by the grey separator into one cell per character."""
    pieces: list[Piece] = []
    for index, item in enumerate(groups):
        if index:
            pieces.append(SEPARATOR)
        pieces.extend(item)
    return [(char, style) for text, style in pieces for char in text]


def _hex_colour(colour: RichColor | None) -> str | None:
    return Color.from_rich_color(colour).hex.lower() if colour is not None else None


def style_of(style: RichStyle | None) -> Style:
    """Reduce a Rich style to its bold flag and lowercase hex foreground."""
    if style is None:
        return (False, None)
    return (bool(style.bold), _hex_colour(style.color))


def painted_backgrounds(widget: Widget) -> set[str | None]:
    """Return every background colour painted across the widget, blanks included."""
    region = Region(0, 0, widget.region.width, widget.region.height)
    return {
        _hex_colour(segment.style.bgcolor if segment.style is not None else None)
        for strip in widget.render_lines(region)
        for segment in strip
    }


def rendered_select_label(select: Select[str]) -> str:
    """Read the Select's painted current option label."""
    label = select.query_one("#label", Static)
    text = "".join(char for row in rendered_lines(label) for char, _ in row)
    current = select.query_one(SelectCurrent)
    if not text or not label.visible or not current.visible:
        return ""
    visible = label.region
    ancestor = label.parent
    while isinstance(ancestor, Widget):
        visible = visible.intersection(ancestor.content_region)
        if visible.width <= 0 or visible.height <= 0:
            return ""
        ancestor = ancestor.parent
    screen = select.screen
    text_bottom = label.region.y + 1
    if (
        visible.x > label.region.x
        or visible.right < label.region.x + len(text)
        or visible.y > label.region.y
        or visible.bottom < text_bottom
    ):
        return ""
    for offset in range(len(text)):
        painted, _ = screen.get_widget_and_offset_at(label.region.x + offset, label.region.y)
        if painted is not label:
            return ""
    return text


def rendered_lines(widget: Widget) -> list[list[Cell]]:
    """Read the widget's painted lines as styled cells without trailing blanks."""
    region = Region(0, 0, widget.region.width, widget.region.height)
    rendered: list[list[Cell]] = []
    for strip in widget.render_lines(region):
        cells = [(char, style_of(segment.style)) for segment in strip for char in segment.text]
        while cells and cells[-1][0] == BLANK:
            cells.pop()
        rendered.append(cells)
    return rendered


def styles(widget: Widget, needle: str) -> list[Style]:
    """Return the bold flag and foreground of each cell of the first occurrence of `needle`."""
    for cells in rendered_lines(widget):
        text = "".join(char for char, _ in cells)
        start = text.find(needle)
        if start >= 0:
            return [style for _, style in cells[start : start + len(needle)]]
    raise AssertionError(needle)


def foregrounds(widget: Widget, needle: str) -> list[str | None]:
    """Return the foreground of each painted cell of the first occurrence of `needle`."""
    return [colour for _, colour in styles(widget, needle)]
