"""The `escalations` widget: open packets as splash panels (06 §5.4, §6.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from shikiru.ui import SplashPanel

if TYPE_CHECKING:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import EscalationPanelView, WidgetContext

_SELECTED_MARKER = "▶ "
MAX_ESCALATION_PANEL_HEIGHT = 12


class PacketScreen(ModalScreen[None]):
    """Show the complete escalation packet until the Operator dismisses it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self, packet: str) -> None:
        """Hold the packet text to display in the modal."""
        super().__init__()
        self._packet = packet

    def compose(self) -> ComposeResult:
        """Render the full packet in a scrollable view."""
        with VerticalScroll():
            yield Static(self._packet, markup=False, id="escalation-packet")

    async def action_close(self) -> None:
        """Close the packet modal."""
        self.dismiss()


class EscalationsPanel(Static):
    """Show every open packet and route its actions to the palette and packet modal."""

    DEFAULT_CSS = f"""
    EscalationsPanel {{ height: auto; }}
    #escalation-panels {{ height: auto; max-height: {MAX_ESCALATION_PANEL_HEIGHT}; }}
    """
    can_focus = True

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; views are read on mount and on each state change."""
        super().__init__(id="escalations")
        self._widget_context = context
        self._escalations: list[EscalationPanelView] = []
        self._selected = 0

    def compose(self) -> ComposeResult:
        """Reserve a scrollable area for the open escalation panels."""
        yield VerticalScroll(id="escalation-panels")

    async def on_mount(self) -> None:
        """Render open packets and refresh on state changes."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)

    async def on_key(self, event: events.Key) -> None:
        """Move between open packets, show a packet, or request its resolution form."""
        if not self._escalations:
            return
        if event.key in ("up", "down"):
            step = -1 if event.key == "up" else 1
            self._selected = (self._selected + step) % len(self._escalations)
            await self._render_panels()
            event.stop()
        elif event.key == "o":
            cast("App[None]", self.app).push_screen(
                PacketScreen(self._escalations[self._selected].packet)
            )
            event.stop()
        elif event.key == "r":
            item = self._escalations[self._selected]
            await self._widget_context.open_palette(f"resolve {item.escalation_id}")
            event.stop()

    async def _refresh(self) -> None:
        self._escalations = self._widget_context.views.escalations()
        self.display = bool(self._escalations)
        self._selected = min(self._selected, max(len(self._escalations) - 1, 0))
        await self._render_panels()

    async def _render_panels(self) -> None:
        panels = self.query_one("#escalation-panels", VerticalScroll)
        await panels.remove_children()
        if not self._escalations:
            await panels.mount(Static("no open escalations"))
            return
        cards = [
            Static(self._render_item(index, item), markup=False)
            for index, item in enumerate(self._escalations)
        ]
        await panels.mount(*cards)

    def _render_item(self, index: int, item: EscalationPanelView) -> Group:
        headline = (
            f"{_SELECTED_MARKER}{item.headline}" if index == self._selected else item.headline
        )
        return Group(
            SplashPanel(headline, item.question, item.options).render(),
            "",
        )


class EscalationsWidget:
    """The `escalations` dashboard widget plugin (06 §5.4, §6.4)."""

    name = "escalations"

    def create(self, context: WidgetContext) -> Widget:
        """Build the escalation panel for `context`."""
        return EscalationsPanel(context)
