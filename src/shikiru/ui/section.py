"""Rule-and-heading sections with labelled, separator-wrapped rows shared by the panels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from shikiru.ui.age import AGE_NOW
from shikiru.ui.tokens import NEUTRAL_BORDER_COLOR, SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult

GROUP_SEPARATOR = " · "
LABEL_GAP = 1
HEADING_MIN_GAP = 1
STACKED_BELOW_WIDTH = 32
OBSERVED_WORD = "observed"
AGO_WORD = "ago"

Row = tuple[Text, tuple[Text, ...]]


def grey(text: str) -> Text:
    """Return `text` in the secondary grey."""
    return Text(text, style=SECONDARY_TEXT_COLOR)


def observed_text(observed_ago: str) -> str:
    """Return `observed <age> ago`, or `observed now`."""
    suffix = "" if observed_ago == AGE_NOW else f" {AGO_WORD}"
    return f"{OBSERVED_WORD} {observed_ago}{suffix}"


def wrap_groups(groups: tuple[Text, ...], width: int | None) -> Text:
    """Join `groups` with grey separators, breaking lines only between groups."""
    result = Text()
    line_width = 0
    for group in groups:
        separator = GROUP_SEPARATOR if line_width else ""
        group_width = cell_len(group.plain)
        exceeds_width = line_width + cell_len(separator) + group_width > width if width else False
        if width is not None and line_width and exceeds_width:
            result.append("\n")
            line_width = 0
            separator = ""
        result.append(separator, style=SECONDARY_TEXT_COLOR)
        result.append(group)
        line_width += cell_len(separator) + group_width
    return result


def labelled_rows(rows: list[Row], width: int, label_width: int) -> RenderableType:
    """Lay rows out as a label column and wrapped values, stacked when narrow."""
    if width < STACKED_BELOW_WIDTH:
        return Group(*_stacked_lines(rows, width))
    value_width = width - label_width - LABEL_GAP
    grid = Table.grid()
    grid.add_column(width=label_width + LABEL_GAP, no_wrap=True)
    grid.add_column(width=value_width)
    for label, groups in rows:
        grid.add_row(label, wrap_groups(groups, value_width))
    return grid


def _stacked_lines(rows: list[Row], width: int) -> list[Text]:
    lines: list[Text] = []
    for label, groups in rows:
        if label.plain:
            lines.append(label)
        lines.append(wrap_groups(groups, width))
    return lines


@dataclass(frozen=True)
class Section:
    """Render `body` below a rule and a grey heading with an optional right-aligned `trailing`."""

    title: str
    body: RenderableType
    trailing: Text | None = None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the rule, the heading and the body."""
        del console
        yield Rule(style=NEUTRAL_BORDER_COLOR)
        yield self._heading(options.max_width)
        yield self.body

    def _heading(self, width: int) -> RenderableType:
        title = grey(self.title)
        if self.trailing is None:
            return title
        gap = width - cell_len(title.plain) - cell_len(self.trailing.plain)
        if gap < HEADING_MIN_GAP:
            return Group(title, self.trailing)
        title.append(" " * gap)
        title.append(self.trailing)
        return title
