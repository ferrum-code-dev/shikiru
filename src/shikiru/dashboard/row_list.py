"""A keyboard-driven list of one-line rows shared by the task Events and Relationships views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rich.cells import cell_len
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from shikiru.dashboard.details_list_view import ROW_LEAD
from shikiru.ui.section import BOLD_PRIMARY, grey
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
    from textual import events
    from textual.binding import BindingType

_LEAD_GAP = "  "
_TAIL_GAP = "  "
_STEP_UP = -1
_STEP_DOWN = 1


@dataclass(frozen=True)
class RowCells:
    """One row: a grey lead, the primary text and an optional styled tail that is never cut."""

    lead: str
    text: str | Text
    tail: Text | None = None
    columns: tuple[str | Text, str | Text] | None = None
    column_width: int = 0


@dataclass(frozen=True)
class _RowLine:
    cells: RowCells
    selected: bool

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if self.cells.columns is not None:
            yield from self._column_lines(console, options)
            return
        line = Text(ROW_LEAD)
        line.append_text(grey(f"{self.cells.lead}{_LEAD_GAP}" if self.cells.lead else ""))
        tail = self.cells.tail or Text()
        gap = _TAIL_GAP if tail else ""
        body = Text(style=BOLD_PRIMARY if self.selected else PRIMARY_TEXT_COLOR)
        body.append_text(
            Text(self.cells.text) if isinstance(self.cells.text, str) else self.cells.text
        )
        room = max(options.max_width - line.cell_len - cell_len(gap) - tail.cell_len, 1)
        body.truncate(room, overflow="ellipsis")
        line.append_text(body)
        line.append(gap)
        line.append_text(tail)
        line.truncate(max(options.max_width, 1), overflow="ellipsis", pad=True)
        yield line

    def _column_lines(self, console: Console, options: ConsoleOptions) -> RenderResult:
        columns = self.cells.columns
        if columns is None:
            return
        label, value = columns
        label_text = Text(label) if isinstance(label, str) else label.copy()
        value_text = Text(value) if isinstance(value, str) else value.copy()
        label_text.pad_right(max(self.cells.column_width - label_text.cell_len, 0))
        prefix_width = len(ROW_LEAD) + self.cells.column_width + 1
        value_width = max(options.max_width - prefix_width, 1)
        lines = value_text.wrap(console, value_width, overflow="fold") or [Text()]
        for index, value_line in enumerate(lines):
            line = Text(ROW_LEAD)
            if index == 0:
                line.append_text(label_text)
            else:
                line.append(" " * self.cells.column_width)
            line.append(" ")
            line.append_text(value_line)
            yield line


class _Row(Static):
    class Chosen(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, index: int, content: RenderableType, *, selected: bool) -> None:
        super().__init__(content, classes="row-list-row", markup=False)
        self._index = index
        self.set_class(selected, "-selected")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self._index))


class RowList(Vertical, can_focus=True):
    """Rows with a selected background; arrows move, PgUp/PgDn page, End asks for the latest."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", f"move({_STEP_UP})", "Previous", show=False),
        Binding("down", f"move({_STEP_DOWN})", "Next", show=False),
        Binding("pageup", "page(0)", "Newer page", show=False),
        Binding("pagedown", "page(1)", "Older page", show=False),
        Binding("end", "latest", "Latest", show=False),
        Binding("enter", "open", "Open", show=False),
    ]
    DEFAULT_CSS = """
    RowList { height: auto; }
    RowList:focus { background-tint: transparent; }
    .row-list-row { height: 1; width: 100%; }
    .row-list-row.-selected { background: $selected-surface; }
    """

    class _Posted(Message):
        def __init__(self, row_list: RowList) -> None:
            super().__init__()
            self.row_list = row_list

        @property
        def control(self) -> RowList:
            return self.row_list

    class Moved(_Posted):
        """The selection moved to `index`."""

        def __init__(self, row_list: RowList, index: int) -> None:
            super().__init__(row_list)
            self.index = index

    class Opened(_Posted):
        """The row at `index` was opened."""

        def __init__(self, row_list: RowList, index: int) -> None:
            super().__init__(row_list)
            self.index = index

    class Paged(_Posted):
        """The next (`older`) or previous page was asked for, by an arrow past an end or by key."""

        def __init__(self, row_list: RowList, *, older: bool, by_arrow: bool = False) -> None:
            super().__init__(row_list)
            self.older = older
            self.by_arrow = by_arrow

    class Latest(_Posted):
        """The latest rows were asked for."""

    class Scrolled(_Posted):
        """The operator scrolled the rows away from the first one."""

    def __init__(self, *, widget_id: str, edge_paging: bool = False) -> None:
        """Create an empty list; rows arrive through `set_rows`.

        With `edge_paging`, moving past the last or first row asks for the older or newer page.
        """
        super().__init__(id=widget_id)
        self._edge_paging = edge_paging
        self._cells: tuple[RowCells, ...] = ()
        self._selected = 0
        self._empty = ""
        self._widgets: list[_Row] = []

    @property
    def selected(self) -> int:
        """The index of the selected row."""
        return self._selected

    def set_rows(self, rows: Sequence[RowCells], *, selected: int, empty: str) -> None:
        """Replace the rows, select `selected` (clamped) and show `empty` when none."""
        previous = self._selected
        self._cells = tuple(rows)
        self._empty = empty
        self._selected = min(max(selected, 0), max(len(self._cells) - 1, 0))
        self._paint()
        if self._selected != previous:
            self._reveal()

    def action_move(self, step: int) -> None:
        """Move the selection; at an end, page instead when edge paging is on."""
        target = self._selected + step
        if 0 <= target < len(self._cells):
            self._choose(target)
        elif self._edge_paging:
            self.post_message(self.Paged(self, older=step > 0, by_arrow=True))

    def action_page(self, older: int) -> None:
        """Ask for the older (PgDn) or newer (PgUp) page."""
        self.post_message(self.Paged(self, older=bool(older)))

    def action_latest(self) -> None:
        """Ask to jump to the latest rows and follow them."""
        self.scroll_home(animate=False)
        self.post_message(self.Latest(self))

    def action_open(self) -> None:
        """Open the selected row."""
        if self._cells:
            self.post_message(self.Opened(self, self._selected))

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Tell the owner when the rows are scrolled away from the first one."""
        super().watch_scroll_y(old_value, new_value)
        if new_value > 0:
            self.post_message(self.Scrolled(self))

    @on(_Row.Chosen)
    def _row_chosen(self, message: _Row.Chosen) -> None:
        message.stop()
        self._choose(message.index)
        self.action_open()

    def _choose(self, index: int) -> None:
        if index == self._selected:
            return
        self._selected = index
        self._paint()
        self._reveal()
        self.post_message(self.Moved(self, index))

    def _lines(self) -> list[RenderableType]:
        if not self._cells:
            return [grey(self._empty)]
        return [_RowLine(cells, index == self._selected) for index, cells in enumerate(self._cells)]

    def _paint(self) -> None:
        lines = self._lines()
        highlighted = bool(self._cells)
        for index, (row, line) in enumerate(zip(self._widgets, lines, strict=False)):
            row.update(line)
            row.set_class(highlighted and index == self._selected, "-selected")
        for row in self._widgets[len(lines) :]:
            row.remove()
        del self._widgets[len(lines) :]
        fresh = [
            _Row(index, line, selected=highlighted and index == self._selected)
            for index, line in enumerate(lines)
            if index >= len(self._widgets)
        ]
        self._widgets.extend(fresh)
        if fresh:
            self.mount(*fresh)

    def _reveal(self) -> None:
        if self._selected < len(self._widgets):
            self._widgets[self._selected].scroll_visible(animate=False)
