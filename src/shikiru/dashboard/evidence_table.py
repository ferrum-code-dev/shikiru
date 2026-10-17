"""A selectable table of evidence entries with a shaded, structured details block under it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text
from textual import on
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from shikiru.dashboard.row_list import RowCells, RowList
from shikiru.dashboard.section_rows import TONE_COLORS
from shikiru.ui.section import BOLD_STEEL, STACKED_BELOW_WIDTH, Row, grey, labelled_rows
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR, STEEL_BLUE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.app import ComposeResult

    from shikiru.domain.packet_evidence import DetailRow, EvidenceEntry, Part

MAX_LABEL_WIDTH = 19
DETAIL_STACKED_BELOW_WIDTH = 50
_KIND_GAP = "  "


def part_text(part: Part) -> Text:
    """Return `part` in its tone colour, or the accent colour."""
    style = STEEL_BLUE if part.accent else TONE_COLORS[part.tone.value] or PRIMARY_TEXT_COLOR
    return Text(part.text, style=style)


def detail_row(row: DetailRow) -> Row:
    """Return a details row as a grey label and its toned value."""
    return grey(row.label), (Text("").join(part_text(part) for part in row.parts),)


@dataclass(frozen=True)
class LabelledRows:
    """Rows as a capped-width label column and wrapped values; unlabelled rows as plain lines."""

    rows: list[Row]
    stack_below: int = STACKED_BELOW_WIDTH

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        label_width = min(
            max((cell_len(label.plain) for label, _ in self.rows), default=0), MAX_LABEL_WIDTH
        )
        if label_width:
            yield labelled_rows(self.rows, options.max_width, label_width, self.stack_below)
        else:
            yield Group(*(values[0] for _, values in self.rows))


class _PageScrollingRowList(RowList):
    def action_page(self, older: int) -> None:
        scroller = self.query_ancestor(VerticalScroll)
        if older:
            scroller.scroll_page_down(animate=False)
        else:
            scroller.scroll_page_up(animate=False)


class EvidenceTable(Vertical):
    """A titled one-line-per-entry table; the selected entry's details show beneath it."""

    DEFAULT_CSS = """
    EvidenceTable { height: auto; }
    EvidenceTable RowList { max-height: 8; overflow-y: auto; scrollbar-size-vertical: 1; }
    EvidenceTable .evidence-detail {
        height: auto; padding: 0 1; margin: 1 0 0 0;
        color: $primary-text; background: $detail-surface;
    }
    """

    def __init__(self, title: str, entries: Sequence[EvidenceEntry], *, table_id: str) -> None:
        """Show `title` over one row per entry in `entries`, which must not be empty."""
        super().__init__()
        self._title = title
        self._entries = tuple(entries)
        self._table_id = table_id

    @property
    def table(self) -> RowList:
        """The focusable row list."""
        return self.query_one(f"#{self._table_id}", RowList)

    def compose(self) -> ComposeResult:
        yield Static(Text(self._title, style=BOLD_STEEL))
        yield _PageScrollingRowList(widget_id=self._table_id)
        yield Static(classes="evidence-detail")

    def on_mount(self) -> None:
        kind_width = max(cell_len(entry.kind) for entry in self._entries)
        self.table.set_rows(
            [self._cells(entry, kind_width) for entry in self._entries], selected=0, empty=""
        )
        self._show(0)

    @on(RowList.Moved)
    def _moved(self, message: RowList.Moved) -> None:
        message.stop()
        self._show(message.index)

    @staticmethod
    def _cells(entry: EvidenceEntry, kind_width: int) -> RowCells:
        text = Text(entry.kind.ljust(kind_width), style=STEEL_BLUE)
        text.append(_KIND_GAP)
        text.append_text(part_text(entry.outcome))
        return RowCells(entry.lead, text)

    def _show(self, index: int) -> None:
        rows = [detail_row(row) for row in self._entries[index].details]
        self.query_one(".evidence-detail", Static).update(
            LabelledRows(rows, DETAIL_STACKED_BELOW_WIDTH)
        )
