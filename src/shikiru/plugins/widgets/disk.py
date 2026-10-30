"""The `disk` widget: free space of the project filesystem and the `.shikiru` size."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.dashboard.contract import DiagnosticSampleState, WidgetPlacement
from shikiru.ui.compact import free_space_text
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import is_first_child, widget_is_attached
from shikiru.ui.section import (
    FAILED_WORD,
    GROUP_SEPARATOR,
    UNAVAILABLE_WORD,
    UNKNOWN_AGE,
    Row,
    Section,
    grey,
    labelled_rows,
    number,
    observation,
    state_line,
)
from shikiru.ui.size import format_size
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import DiskView, WidgetContext

SECTION_TITLE = "Disk"
LABEL_FREE = "Free"
LABEL_SHIKIRU = ".shikiru"
MEASURING_WORD = "measuring…"
LABEL_REFRESH = "Refresh"


@dataclass(frozen=True)
class _DiskBody:
    view: DiskView
    refresh_error: str | None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        view = self.view
        rows: list[Row] = []
        if view.free_bytes is not None and view.total_bytes is not None:
            free = free_space_text(view.free_bytes, view.total_bytes, view.free_level)
            rows.append((grey(LABEL_FREE), (free,)))
        if view.shikiru_bytes is not None:
            rows.append((grey(LABEL_SHIKIRU), (number(format_size(view.shikiru_bytes)),)))
        elif self.refresh_error is None:
            rows.append((grey(LABEL_SHIKIRU), (grey(MEASURING_WORD),)))
        if self.refresh_error is not None:
            failure = Text(f"{FAILED_WORD}{GROUP_SEPARATOR}{self.refresh_error}", style=AMBER)
            rows.append((grey(LABEL_REFRESH), (failure,)))
        label_width = max((cell_len(label.plain) for label, _ in rows), default=0)
        yield labelled_rows(rows, options.max_width, label_width)


def disk_section(view: DiskView) -> Section:
    """Render `view` as the Disk section in every state."""
    ago = view.sampled_ago or UNKNOWN_AGE
    if view.state is DiagnosticSampleState.UNAVAILABLE:
        return Section(
            SECTION_TITLE, state_line(UNAVAILABLE_WORD, SECONDARY_TEXT_COLOR, view.error)
        )
    if view.state is DiagnosticSampleState.FAILED:
        return Section(SECTION_TITLE, state_line(FAILED_WORD, ALERT_COLOR, view.error))
    stale = view.state is DiagnosticSampleState.STALE
    return Section(
        SECTION_TITLE, _DiskBody(view, view.error if stale else None), observation(ago, stale=stale)
    )


class DiskPanel(GatedStatic):
    """Show the Disk section and re-render only when its content changed."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; disk sampling starts on mount."""
        super().__init__(id="disk")
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render and refresh disk state at the configured cadence."""
        await self._refresh()
        refresh_seconds = self._widget_context.settings.current().dashboard.disk_refresh_seconds
        self.set_interval(refresh_seconds, self._refresh)

    async def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
        view = await self._widget_context.views.disk()
        if not widget_is_attached(self):
            return
        if view.shikiru_bytes is None and view.state is DiagnosticSampleState.AVAILABLE:
            resources = self._widget_context.settings.current().orchestration.resources
            self.set_timer(resources.sample_seconds, self._refresh)
        ruled = not is_first_child(self)
        self.update_if_changed(disk_section(view).with_ruled(ruled=ruled), key=(view, ruled))


class DiskWidget:
    """The `disk` dashboard widget plugin."""

    name = "disk"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the Disk panel for `context`."""
        return DiskPanel(context)
