"""The `faults` widget: recorded open engine fault streaks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Collapsible, Static

from shikiru.dashboard.contract import WidgetPlacement
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import is_first_child, widget_is_attached
from shikiru.ui.section import GROUP_SEPARATOR, Section, grey, row_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import EngineFaultView, FaultDetailRow, WidgetContext

SECTION_TITLE = "Faults"
TITLE_SEPARATOR = " \N{MIDDLE DOT} "
PROJECT_TARGET = "project"
DETAILS_TITLE = "Details"
COLLAPSED_MARK = "\N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
EXPANDED_MARK = "\N{BLACK DOWN-POINTING SMALL TRIANGLE}"
IMPACT_LABEL = "Impact:"
CAUSE_LABEL = "Cause:"
NEXT_LABEL = "Next:"
DETAIL_INDENT = "  "


def _summary(fault: EngineFaultView) -> Group:
    headline = Text.assemble(
        row_name(fault.task_id or PROJECT_TARGET),
        grey(GROUP_SEPARATOR),
        Text(fault.scope),
        grey(GROUP_SEPARATOR),
        Text(fault.status),
    )
    cause = Text.assemble(grey(f"{CAUSE_LABEL} "), Text(fault.cause))
    return Group(
        headline,
        Text.assemble(grey(f"{IMPACT_LABEL} "), Text(fault.impact)),
        cause,
        Text.assemble(grey(f"{NEXT_LABEL} "), Text(fault.next_step)),
    )


def _detail_lines(row: FaultDetailRow) -> Text:
    lines = row.value.strip().splitlines() or [row.value]
    if len(lines) == 1:
        return Text.assemble(grey(f"{row.label} "), Text(lines[0]))
    body = "\n".join(f"{DETAIL_INDENT}{line}" for line in lines)
    return Text.assemble(grey(row.label), Text("\n"), Text(body))


def _details(fault: EngineFaultView) -> Group:
    return Group(*(_detail_lines(row) for row in fault.details))


class FaultCard(Vertical):
    """One open fault: its compact summary and a Details disclosure that starts collapsed."""

    DEFAULT_CSS = """
    FaultCard { height: auto; margin-bottom: 1; }
    FaultCard Collapsible { padding: 0; border-top: none; }
    FaultCard Collapsible > Contents { padding: 0 0 0 2; }
    """

    def __init__(self, fault: EngineFaultView) -> None:
        """Show `fault`; later `show` calls update it in place, keeping the disclosure state."""
        super().__init__()
        self._summary = GatedStatic(_summary(fault))
        self._details = Static(_details(fault))
        self._fault = fault

    def compose(self) -> ComposeResult:
        """Lay out the summary above the collapsed Details disclosure."""
        yield self._summary
        yield Collapsible(
            self._details,
            title=DETAILS_TITLE,
            collapsed=True,
            collapsed_symbol=COLLAPSED_MARK,
            expanded_symbol=EXPANDED_MARK,
        )

    def show(self, fault: EngineFaultView) -> None:
        """Repaint from `fault`, the latest view of the same fault identity."""
        if fault == self._fault:
            return
        self._fault = fault
        self._summary.update_if_changed(_summary(fault), key=fault)
        self._details.update(_details(fault))


class FaultsPanel(Vertical):
    """Show one card per open fault and take no room while there are none."""

    DEFAULT_CSS = "FaultsPanel { height: auto; }"

    def __init__(self, context: WidgetContext) -> None:
        """Hold the read-only dashboard view and its state-change subscription."""
        super().__init__(id="faults")
        self._widget_context = context
        self._unsubscribe_resources: Callable[[], None] | None = None
        self._title = GatedStatic()
        self._cards: dict[str, FaultCard] = {}

    def compose(self) -> ComposeResult:
        """Start with the title; cards are mounted as faults open."""
        yield self._title

    def on_mount(self) -> None:
        """Render fault cards; refresh with the Resources bar and on durable changes."""
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
        title = f"{SECTION_TITLE}{TITLE_SEPARATOR}{len(faults)}"
        self._title.update_if_changed(Section(title, Group(), ruled=ruled), key=(title, ruled))
        open_keys = {fault.key for fault in faults}
        for key in [key for key in self._cards if key not in open_keys]:
            self._cards.pop(key).remove()
        cards = [self._card(fault) for fault in faults]
        anchor = self._anchor()
        self._arrange(cards)
        if anchor is not None:
            self.call_after_refresh(self._restore, *anchor)

    def _anchor(self) -> tuple[Widget, ScrollableContainer, int] | None:
        focused = self.screen.focused
        if focused is None or self not in focused.ancestors:
            return None
        scroller = next(
            (node for node in focused.ancestors if isinstance(node, ScrollableContainer)), None
        )
        if scroller is None:
            return None
        return focused, scroller, focused.region.y

    def _restore(self, focused: Widget, scroller: ScrollableContainer, old_y: int) -> None:
        if not focused.is_attached:
            return
        shift = focused.region.y - old_y
        if shift:
            scroller.scroll_to(y=scroller.scroll_y + shift, animate=False)

    def _card(self, fault: EngineFaultView) -> FaultCard:
        card = self._cards.get(fault.key)
        if card is None:
            card = self._cards[fault.key] = FaultCard(fault)
        else:
            card.show(fault)
        return card

    def _arrange(self, cards: list[FaultCard]) -> None:
        previous: Widget = self._title
        placed = [child for child in self.children if child in cards]
        in_order = placed == [card for card in cards if card in placed]
        for card in cards:
            if card.parent is None:
                self.mount(card, after=previous)
            elif not in_order:
                self.move_child(card, after=previous)
            previous = card


class FaultsWidget:
    """The `faults` dashboard widget plugin."""

    name = "faults"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the fault diagnostics panel for `context`."""
        return FaultsPanel(context)
