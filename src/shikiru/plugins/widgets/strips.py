"""The `strips` widget: active tasks as comic-strip panels (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from shikiru.ui.strip import STRIP_WIDE_MIN_COLUMNS, TaskStrip

if TYPE_CHECKING:
    from textual import events
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext


class StripsPanel(Static):
    """Renders every active task's strip, in dispatch order, focused task first."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__()
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render the current strips, then refresh on every state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)
        self._widget_context.on_motion(self._refresh)

    async def on_resize(self, event: events.Resize) -> None:
        """Re-render at the new width: a narrower pane switches every strip to its one-line form."""
        del event
        await self._refresh()

    async def _refresh(self) -> None:
        width = self.size.width or STRIP_WIDE_MIN_COLUMNS
        strips = [TaskStrip(data).render(width) for data in self._widget_context.views.strips()]
        if not strips:
            self.update("no active tasks")
            return
        self.update(Text("\n\n").join(strips))


class StripsWidget:
    """The `strips` dashboard widget plugin (06 §5.4, §6.2)."""

    name = "strips"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `StripsPanel` for `context`."""
        return StripsPanel(context)
