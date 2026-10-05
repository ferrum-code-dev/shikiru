"""The `escalations` widget: open packets as splash panels (06 §5.4, §6.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import VerticalScroll
from textual.geometry import Region
from textual.screen import ModalScreen
from textual.widgets import Static

from shikiru.dashboard.contract import (
    FOCUS_FAULTS_VIEW,
    AttentionGroupView,
    AttentionView,
    EscalationPanelView,
    PaletteRequest,
    WidgetPlacement,
    WorkspaceSummaryView,
)
from shikiru.ui import SplashPanel
from shikiru.ui.compact import FOCUS_ATTENTION_MAX_HEIGHT, attention_status_text
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

_SELECTED_MARKER = "▶ "
_MOVE_STEPS = {"up": -1, "down": 1}
_DECISION_MOVE_STEPS = {"left": -1, "right": 1}
_OPEN_TASK_KEY = "enter"
_OPEN_KEY = "o"
_RESOLVE_KEY = "r"
MAX_ESCALATION_PANEL_HEIGHT = 12
MINIMUM_GROUPED_DECISIONS = 2


class PacketScreen(ModalScreen[None]):
    """Show the complete escalation packet until the Operator dismisses it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    PacketScreen { background: $background; }
    #escalation-packet {
        background: $surface;
        color: $foreground;
        border: round $border;
        padding: 1 2;
    }
    #escalation-packet:focus { border: round $accent; text-style: bold reverse; }
    """

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
    """Show grouped decisions, diagnosed stalls and recorded engine faults."""

    DEFAULT_CSS = f"""
    EscalationsPanel {{ height: auto; }}
    #escalation-panels {{ height: auto; max-height: {MAX_ESCALATION_PANEL_HEIGHT}; }}
    EscalationsPanel.focus-row #escalation-panels {{
        height: auto; max-height: {FOCUS_ATTENTION_MAX_HEIGHT};
    }}
    """
    can_focus = True

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; views are read on mount and on each state change."""
        classes = "focus-row" if context.placement is WidgetPlacement.ATTENTION else ""
        super().__init__(id="escalations", classes=classes)
        self._widget_context = context
        self._focus_row = context.placement is WidgetPlacement.ATTENTION
        self._attention = AttentionView((), decisions=0, stalls=0, faults=0)
        self._summary: WorkspaceSummaryView | None = None
        self._selected = 0
        self._selected_escalation_id: str | None = None
        self._has_rendered = False
        self._keep_empty_visible = False
        self._remove_motion_callback: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        """Reserve a bounded, scrollable area for home attention groups."""
        yield VerticalScroll(id="escalation-panels")

    async def on_mount(self) -> None:
        """Render current attention and refresh on state or motion changes."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_while_active), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_while_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    def on_focus(self, event: events.Focus) -> None:
        del event
        self._keep_empty_visible = True

    def on_blur(self, event: events.Blur) -> None:
        del event
        if self._focus_row:
            self.display = True

    async def _refresh_while_active(self) -> None:
        if not self.is_mounted:
            return
        app = cast("App[None]", self.app)
        if app.is_running and self.screen.is_current:
            await self._refresh()

    def activate_attention(self) -> None:
        """Show the attention content, retaining its useful empty state while selected."""
        self._keep_empty_visible = True
        self.display = True
        self.focus()

    async def on_key(self, event: events.Key) -> None:
        """Select a group, open its task or evidence, or request the first decision's resolution."""
        if not self._attention.groups:
            if self._focus_row and event.key in _MOVE_STEPS:
                panels = self.query_one("#escalation-panels", VerticalScroll)
                panels.scroll_to(
                    y=panels.scroll_y + _MOVE_STEPS[event.key],
                    animate=False,
                    immediate=True,
                )
                event.stop()
            return
        if event.key in _MOVE_STEPS:
            await self._move_group(_MOVE_STEPS[event.key])
            event.stop()
        elif event.key in _DECISION_MOVE_STEPS:
            if await self._move_decision(_DECISION_MOVE_STEPS[event.key]):
                event.stop()
        elif event.key == _OPEN_TASK_KEY:
            if self._focus_row and self._selected_decision() is not None:
                await self._resolve_decision(event)
            else:
                await self._open_task_detail(event)
        elif event.key == _OPEN_KEY:
            self._open_evidence(event)
        elif event.key == _RESOLVE_KEY:
            await self._resolve_decision(event)

    async def on_click(self, event: events.Click) -> None:
        if not self._focus_row or not self._attention.groups:
            return
        self.focus()
        group = self._attention.groups[self._selected]
        if group.task_id is not None and self._widget_context.on_task_selected is not None:
            self._widget_context.on_task_selected(group.task_id)
        if group.escalations:
            decision = self._selected_decision() or group.escalations[0]
            await self._open_resolution(decision)
        elif group.task_id is not None and self._widget_context.open_task_detail is not None:
            await self._widget_context.open_task_detail(group.task_id)
        else:
            await self._open_fault_evidence()
        event.stop()

    async def _move_group(self, step: int) -> None:
        self._selected = (self._selected + step) % len(self._attention.groups)
        group = self._attention.groups[self._selected]
        self._selected_escalation_id = (
            group.escalations[0].escalation_id if group.escalations else None
        )
        await self._render_panels(reveal_selection=True)

    async def _move_decision(self, step: int) -> bool:
        decisions = self._attention.groups[self._selected].escalations
        if len(decisions) < MINIMUM_GROUPED_DECISIONS:
            return False
        current = next(
            (
                index
                for index, item in enumerate(decisions)
                if item.escalation_id == self._selected_escalation_id
            ),
            0,
        )
        current = (current + step) % len(decisions)
        self._selected_escalation_id = decisions[current].escalation_id
        await self._render_panels(reveal_selection=True)
        return True

    async def _open_task_detail(self, event: events.Key) -> None:
        group = self._attention.groups[self._selected]
        if group.task_id is not None and self._widget_context.open_task_detail is not None:
            on_task_selected = self._widget_context.on_task_selected
            if on_task_selected is not None:
                on_task_selected(group.task_id)
            await self._widget_context.open_task_detail(group.task_id)
            event.stop()

    def _open_evidence(self, event: events.Key) -> None:
        decision = self._selected_decision()
        if decision is None:
            return
        cast("App[None]", self.app).push_screen(PacketScreen(_packet_copy(decision)))
        event.stop()

    async def _resolve_decision(self, event: events.Key) -> None:
        decision = self._selected_decision()
        if decision is None:
            return
        await self._open_resolution(decision)
        event.stop()

    async def _open_resolution(self, decision: EscalationPanelView) -> None:
        request = PaletteRequest(
            action=f"resolve {decision.escalation_id}",
            task_id=decision.task_id,
            direct_answer=True,
        )
        if self._widget_context.open_palette_context is not None:
            await self._widget_context.open_palette_context(request)
        else:
            await self._widget_context.open_palette(request.action or "")

    async def _open_fault_evidence(self) -> None:
        open_view = self._widget_context.open_contextual_view
        if open_view is not None:
            await open_view(FOCUS_FAULTS_VIEW)
            return
        decision = self._selected_decision()
        if decision is not None:
            cast("App[None]", self.app).push_screen(PacketScreen(_packet_copy(decision)))

    async def _refresh(self) -> None:
        attention = self._widget_context.views.attention()
        summary = self._widget_context.views.workspace_summary()
        current_status = _attention_status_state(attention, summary)
        previous_status = (
            _attention_status_state(self._attention, self._summary)
            if self._summary is not None
            else None
        )
        groups_unchanged = attention == self._attention
        had_selection = bool(self._attention.groups)
        selected_id = self._selected_group_id()
        selected_escalation_id = self._selected_escalation_id
        self._attention = attention
        self._summary = summary
        self.display = self._focus_row or bool(attention.groups) or self._keep_empty_visible
        if not attention.groups:
            self._selected = 0
        elif had_selection:
            selected_index = next(
                (
                    index
                    for index, group in enumerate(attention.groups)
                    if group.task_id == selected_id
                ),
                None,
            )
            self._selected = selected_index if selected_index is not None else 0
        else:
            self._selected = min(self._selected, len(attention.groups) - 1)
        decision_group_index = next(
            (
                index
                for index, group in enumerate(attention.groups)
                if any(item.escalation_id == selected_escalation_id for item in group.escalations)
            ),
            None,
        )
        if decision_group_index is not None:
            self._selected = decision_group_index
            self._selected_escalation_id = selected_escalation_id
        elif attention.groups:
            selected_group = attention.groups[self._selected]
            self._selected_escalation_id = (
                selected_group.escalations[0].escalation_id if selected_group.escalations else None
            )
        else:
            self._selected_escalation_id = None
        if groups_unchanged and current_status == previous_status and self._has_rendered:
            return
        await self._render_panels()
        self._has_rendered = True

    def _selected_group_id(self) -> str | None:
        if not self._attention.groups or self._selected >= len(self._attention.groups):
            return None
        return self._attention.groups[self._selected].task_id

    def _selected_decision(self) -> EscalationPanelView | None:
        if not self._attention.groups or self._selected >= len(self._attention.groups):
            return None
        group = self._attention.groups[self._selected]
        return next(
            (
                item
                for item in group.escalations
                if item.escalation_id == self._selected_escalation_id
            ),
            group.escalations[0] if group.escalations else None,
        )

    async def _render_panels(self, *, reveal_selection: bool = False) -> None:
        panels = self.query_one("#escalation-panels", VerticalScroll)
        scroll_x, scroll_y = panels.scroll_x, panels.scroll_y
        if self._focus_row:
            status = self._render_compact_attention()
            children = list(panels.query(Static))
            if len(children) == 1:
                children[0].update(status)
                return
            await panels.remove_children()
            await panels.mount(Static(status, markup=False))
            return
        if not self._attention.groups:
            children = list(panels.query(Static))
            if len(children) == 1:
                children[0].update("No open decisions, diagnosed stalls or recorded engine faults.")
                return
            await panels.remove_children()
            await panels.mount(
                Static(
                    "No open decisions, diagnosed stalls or recorded engine faults.",
                    markup=False,
                )
            )
            return
        children = list(panels.query(Static))
        if len(children) == len(self._attention.groups):
            for index, (card, group) in enumerate(
                zip(children, self._attention.groups, strict=True)
            ):
                card.update(self._render_group(index, group))
            if reveal_selection:
                self._reveal_selection(panels, children[self._selected])
            else:
                panels.scroll_to(x=scroll_x, y=scroll_y, animate=False)
            return
        await panels.remove_children()
        cards = [
            Static(self._render_group(index, group), markup=False)
            for index, group in enumerate(self._attention.groups)
        ]
        await panels.mount(*cards)
        if reveal_selection:
            self._reveal_selection(panels, cards[self._selected])
        else:
            panels.scroll_to(x=scroll_x, y=scroll_y, animate=False)

    def _render_compact_attention(self) -> Text:
        if self._summary is None:
            return Text("○ Engine status unavailable", style=SECONDARY_TEXT_COLOR)
        action_hint = (
            "Enter answer · o evidence"
            if self._focus_row and self._selected_decision() is not None
            else "Enter inspect · Ctrl+P actions"
        )
        return attention_status_text(
            self._attention,
            self._summary,
            action_hint=action_hint,
            selected_group_index=self._selected,
            selected_escalation_id=self._selected_escalation_id,
        )

    def _reveal_selection(self, panels: VerticalScroll, card: Static) -> None:
        decision_id = self._selected_escalation_id
        if decision_id is None:
            panels.scroll_to_widget(card, top=True, animate=False)
            return
        lines = card.render_lines(Region(0, 0, card.size.width, card.size.height))
        decision_line = next(
            (index for index, line in enumerate(lines) if decision_id in line.text),
            0,
        )
        card_top = card.region.y - panels.region.y + panels.scroll_y
        decision_top = card_top + decision_line
        panels.scroll_to(
            x=panels.scroll_x,
            y=max(decision_top - panels.size.height + 1, 0),
            animate=False,
        )

    def _render_group(self, index: int, group: AttentionGroupView) -> Group:
        marker = _SELECTED_MARKER if index == self._selected else "  "
        identity = f"{group.task_id} · {group.title}" if group.task_id is not None else group.title
        lines = [f"{marker}{identity}"]
        lines.extend(f"  {reason}" for reason in group.reasons)
        content: list[RenderableType] = ["\n".join(lines)]
        for item in group.escalations:
            headline = f"{item.escalation_id} · {item.headline} · {item.age}"
            if index == self._selected and item.escalation_id == self._selected_escalation_id:
                headline = f"{_SELECTED_MARKER}{headline}"
            content.append(
                SplashPanel(
                    headline,
                    item.question or "Evidence unavailable",
                    item.options,
                    item.pulse_red,
                ).render()
            )
            if item.resolution_actions:
                content.append(f"  Allowed actions: {', '.join(item.resolution_actions)}")
            if item.evidence_error is not None:
                content.append(f"  Evidence error: {item.evidence_error}")
        if group.task_id is not None:
            content.append("  Enter: task detail")
        if group.escalations:
            content.append("  o: evidence   r: resolve")
        return Group(*content)


def _attention_status_state(
    attention: AttentionView,
    summary: WorkspaceSummaryView,
) -> tuple[object, ...]:
    return (
        attention,
        summary.engine_up,
        summary.held_tasks,
        summary.holds,
        summary.operator_owned,
        summary.held_task_states,
    )


def _packet_copy(item: EscalationPanelView) -> str:
    if item.evidence_error is None:
        return item.packet
    actions = ", ".join(item.resolution_actions) or "none recorded"
    lines = [
        f"{item.escalation_id} · {item.task_id} · {item.headline}",
        f"Allowed actions: {actions}",
        f"Evidence error: {item.evidence_error}",
    ]
    if item.packet:
        lines.extend(("", "Original packet:", item.packet))
    return "\n".join(lines)


class EscalationsWidget:
    """The `escalations` dashboard widget plugin (06 §5.4, §6.4)."""

    name = "escalations"
    placement = WidgetPlacement.ATTENTION

    def create(self, context: WidgetContext) -> Widget:
        """Build the escalation panel for `context`."""
        return EscalationsPanel(context)
