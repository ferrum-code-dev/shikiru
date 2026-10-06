"""The `faults` widget: recorded open engine fault streaks (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from shikiru.dashboard.contract import WidgetPlacement
from shikiru.ui.screen import widget_is_attached

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import EngineFaultView, WidgetContext


def _fault_group(fault: EngineFaultView) -> Group:
    target = fault.task_id or "project"
    recoverability = "recoverable" if fault.recoverable else "unrecoverable"
    return Group(
        Text(
            f"{target} · {fault.scope} · {fault.error} · {recoverability} · repeats {fault.repeats}"
        ),
        Text(f"First recorded {fault.first_seen} · latest {fault.last_seen}"),
    )


class FaultsPanel(Static):
    """Show recorded engine faults that have not recovered."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold the read-only dashboard view and its state-change subscription."""
        super().__init__(id="faults")
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render current fault streaks and refresh after durable state changes."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)

    async def _refresh_if_attached(self) -> None:
        if widget_is_attached(self):
            await self._refresh()

    async def _refresh(self) -> None:
        faults = self._widget_context.views.engine_faults()
        self.update(
            Group(*(_fault_group(fault) for fault in faults))
            if faults
            else "No open engine fault streaks."
        )


class FaultsWidget:
    """The `faults` dashboard widget plugin (06 §5.4)."""

    name = "faults"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the fault diagnostics panel for `context`."""
        return FaultsPanel(context)
