"""Rule-and-heading sections with labelled, separator-wrapped rows shared by the panels."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from shikiru.ui.age import AGE_NOW
from shikiru.ui.tokens import (
    AMBER,
    NEUTRAL_BORDER_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult

GROUP_SEPARATOR = " · "
LABEL_GAP = 1
MIN_VALUE_WIDTH = 24
VALUE_OVERFLOW = "fold"
HEADING_MIN_GAP = 1
STACKED_BELOW_WIDTH = 32
OBSERVED_WORD = "observed"
AGO_WORD = "ago"
UNKNOWN_AGE = "\N{EN DASH}"
STALE_WORD = "stale"
FAILED_WORD = "failed"
UNAVAILABLE_WORD = "unavailable"
BOLD_STEEL = f"{STYLE_BOLD} {STEEL_BLUE}"
BOLD_PRIMARY = f"{STYLE_BOLD} {PRIMARY_TEXT_COLOR}"

Row = tuple[Text, tuple[Text, ...]]


def grey(text: str) -> Text:
    """Return `text` in the secondary grey."""
    return Text(text, style=SECONDARY_TEXT_COLOR)


def row_name(text: str) -> Text:
    """Return `text` as a bold white row name."""
    return Text(text, style=BOLD_PRIMARY)


def number(value: str) -> Text:
    """Return `value` as a bold cyan figure."""
    return Text(value, style=BOLD_STEEL)


def key_label(key: str, label: str) -> Text:
    """Return `key` in the bold cyan key style followed by its grey `label`."""
    return Text.assemble((key, BOLD_STEEL), grey(f" {label}"))


def observed_text(observed_ago: str) -> str:
    """Return `observed <age> ago`, or `observed now`."""
    suffix = "" if observed_ago == AGE_NOW else f" {AGO_WORD}"
    return f"{OBSERVED_WORD} {observed_ago}{suffix}"


def observation(observed_ago: str, *, stale: bool) -> Text | None:
    """Return the amber `stale · observed <age> ago` heading note, or nothing unless `stale`."""
    if not stale:
        return None
    return Text(f"{STALE_WORD}{GROUP_SEPARATOR}{observed_text(observed_ago)}", style=AMBER)


def state_line(word: str, colour: str, error: str | None) -> Text:
    """Return a sampling state `word` in `colour`, with any recorded `error` after it."""
    detail = f"{GROUP_SEPARATOR}{error}" if error else ""
    return Text(f"{word}{detail}", style=colour)


def wrap_groups(
    groups: tuple[Text, ...], width: int | None, separator: str = GROUP_SEPARATOR
) -> Text:
    """Join `groups` with grey `separator`s, breaking lines only between groups."""
    result = Text()
    line_width = 0
    for group in groups:
        gap = separator if line_width else ""
        group_width = cell_len(group.plain)
        exceeds_width = line_width + cell_len(gap) + group_width > width if width else False
        if width is not None and line_width and exceeds_width:
            result.append("\n")
            line_width = 0
            gap = ""
        result.append(gap, style=SECONDARY_TEXT_COLOR)
        result.append(group)
        line_width += cell_len(gap) + group_width
    return result


class WrappedGroups:
    """Renderable that wraps its groups with `separator` at the width it is rendered in."""

    def __init__(self, groups: tuple[Text, ...], separator: str = GROUP_SEPARATOR) -> None:
        self._groups = groups
        self._separator = separator

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        yield wrap_groups(self._groups, options.max_width, self._separator)


def labelled_rows(
    rows: list[Row], width: int, label_width: int, stack_below: int = STACKED_BELOW_WIDTH
) -> RenderableType:
    """Lay rows out as a label column and wrapped values, stacked below `stack_below` cells."""
    value_width = width - label_width - LABEL_GAP
    value_indent = labelled_value_indent(width, label_width, stack_below)
    if value_indent == 0 and width < stack_below and label_width <= width - LABEL_GAP:
        return Group(*_stacked_lines(rows, width))
    if value_indent == 0:
        grid = Table.grid()
        grid.add_column(width=width, overflow=VALUE_OVERFLOW)
        for label, groups in rows:
            if label.plain:
                grid.add_row(label)
            grid.add_row(wrap_groups(groups, width))
        return grid
    grid = Table.grid()
    grid.add_column(width=label_width + LABEL_GAP, no_wrap=True)
    grid.add_column(width=value_width, overflow=VALUE_OVERFLOW)
    for label, groups in rows:
        grid.add_row(label, wrap_groups(groups, value_width))
    return grid


def labelled_value_indent(
    width: int, label_width: int, stack_below: int = STACKED_BELOW_WIDTH
) -> int:
    """Return the value cell's indent when `labelled_rows` has a value column."""
    value_width = width - label_width - LABEL_GAP
    if value_width < MIN_VALUE_WIDTH:
        return 0
    if width < stack_below and label_width <= width - LABEL_GAP:
        return 0
    return label_width + LABEL_GAP


def _stacked_lines(rows: list[Row], width: int) -> list[Text]:
    lines: list[Text] = []
    for label, groups in rows:
        if label.plain:
            lines.append(label)
        lines.append(wrap_groups(groups, width))
    return lines


@dataclass(frozen=True)
class Section:
    """Render `body` below an optional rule and a heading, with an optional `trailing`."""

    title: str
    body: RenderableType
    trailing: Text | None = None
    ruled: bool = True
    title_style: str = SECONDARY_TEXT_COLOR

    def with_ruled(self, *, ruled: bool) -> Section:
        """Return this section drawn with or without its leading rule."""
        return replace(self, ruled=ruled)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the rule when ruled, the heading and the body."""
        del console
        if self.ruled:
            yield Rule(style=NEUTRAL_BORDER_COLOR)
        yield self._heading(options.max_width)
        yield self.body

    def _heading(self, width: int) -> RenderableType:
        title = Text()
        title.append(self.title, style=self.title_style)
        if self.trailing is None:
            return title
        gap = width - cell_len(title.plain) - cell_len(self.trailing.plain)
        if gap < HEADING_MIN_GAP:
            return Group(title, self.trailing)
        title.append(" " * gap)
        title.append(self.trailing)
        return title
