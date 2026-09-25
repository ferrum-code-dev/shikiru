"""The `queue` widget: queued tasks in dispatch order (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import QueueRow, WidgetContext

_NO_WAIT_REASON = "-"
_NO_DEPENDENCIES = "-"


def _queue_line(task: QueueRow) -> str:
    deps = ", ".join(task.dependencies) if task.dependencies else _NO_DEPENDENCIES
    reason = task.wait_reason or _NO_WAIT_REASON
    return f"{task.task_id} {task.title} · priority {task.priority} · deps {deps} · {reason}"


class QueuePanel(Static):
    """Lists every queued task, in the order the scheduler would dispatch it."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__()
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render the current queue, then refresh on every state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)

    async def _refresh(self) -> None:
        lines = [_queue_line(task) for task in self._widget_context.views.queue()]
        self.update("\n".join(lines) if lines else "queue empty")


class QueueWidget:
    """The `queue` dashboard widget plugin (06 §5.4)."""

    name = "queue"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `QueuePanel` for `context`."""
        return QueuePanel(context)
