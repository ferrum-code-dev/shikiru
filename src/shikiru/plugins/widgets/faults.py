"""The `faults` widget: recorded open engine fault streaks (06 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from shikiru.dashboard.contract import WidgetPlacement
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import is_first_child, widget_is_attached
from shikiru.ui.section import Section, grey, number, row_name, wrap_groups

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import EngineFaultView, WidgetContext

SECTION_TITLE = "Faults"
PROJECT_TARGET = "project"
RECOVERABLE_WORD = "recoverable"
UNRECOVERABLE_WORD = "unrecoverable"
REPEAT_SIGN = "\N{MULTIPLICATION SIGN}"
FIRST_WORD = "first"
LATEST_WORD = "latest"


def _fault_headline(fault: EngineFaultView) -> tuple[Text, ...]:
    recoverability = RECOVERABLE_WORD if fault.recoverable else UNRECOVERABLE_WORD
    return (
        row_name(fault.task_id or PROJECT_TARGET),
        Text(fault.scope),
        Text(fault.error),
        grey(recoverability),
        number(f"{REPEAT_SIGN}{fault.repeats}"),
    )


def _fault_timeline(fault: EngineFaultView) -> tuple[Text, ...]:
    return (
        Text.assemble(grey(f"{FIRST_WORD} "), Text(fault.first_seen)),
        Text.assemble(grey(f"{LATEST_WORD} "), Text(fault.last_seen)),
    )


@dataclass(frozen=True)
class _FaultLines:
    faults: tuple[EngineFaultView, ...]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        for fault in self.faults:
            yield wrap_groups(_fault_headline(fault), options.max_width)
            yield wrap_groups(_fault_timeline(fault), options.max_width)


def faults_section(faults: tuple[EngineFaultView, ...], *, ruled: bool) -> Group | Section:
    """Render open `faults` as the Faults section, and nothing when none is open."""
    return Section(SECTION_TITLE, _FaultLines(faults), ruled=ruled) if faults else Group()


class FaultsPanel(GatedStatic):
    """Show open engine fault streaks and take no room while there are none."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold the read-only dashboard view and its state-change subscription."""
        super().__init__(id="faults")
        self._widget_context = context
        self._unsubscribe_resources: Callable[[], None] | None = None

    def on_mount(self) -> None:
        """Render fault streaks; refresh with the Resources bar and on durable changes."""
        self._refresh()
        self._unsubscribe_resources = self._widget_context.views.on_resources_refreshed(
            self._refresh
        )
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)

    def on_unmount(self) -> None:
        if self._unsubscribe_resources is not None:
            self._unsubscribe_resources()
            self._unsubscribe_resources = None

    async def _refresh_if_attached(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
        faults = self._widget_context.views.engine_faults()
        self.display = bool(faults)
        ruled = not is_first_child(self)
        self.update_if_changed(faults_section(faults, ruled=ruled), key=(faults, ruled))


class FaultsWidget:
    """The `faults` dashboard widget plugin (06 §5.4)."""

    name = "faults"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the fault diagnostics panel for `context`."""
        return FaultsPanel(context)
