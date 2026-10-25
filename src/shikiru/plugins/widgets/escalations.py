"""The `escalations` widget: open packets as splash panels."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from shikiru.dashboard.contract import (
    FOCUS_FAULTS_VIEW,
    AttentionGroupView,
    AttentionView,
    EscalationPanelView,
    WidgetPlacement,
    WorkspaceSummaryView,
)
from shikiru.ui import SplashPanel
from shikiru.ui.compact import (
    ACTION_HINT,
    FOCUS_ATTENTION_MAX_HEIGHT,
    FocusAttentionStatus,
    display_escalation_headline,
)
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.keys import key_hint
from shikiru.ui.screen import widget_is_attached, widget_is_current
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import WidgetContext

_SELECTED_MARKER = "▶ "
_MOVE_STEPS = {"up": -1, "down": 1}
_OPEN_TASK_KEY = "enter"
MAX_ESCALATION_PANEL_HEIGHT = 12
ARMED_CLASS = "armed"


class EscalationsPanel(Static):
    """Show grouped decisions, diagnosed stalls and recorded engine faults."""

    DEFAULT_CSS = f"""
    EscalationsPanel {{ height: auto; }}
    #escalation-panels {{ height: auto; max-height: {MAX_ESCALATION_PANEL_HEIGHT}; }}
    EscalationsPanel.focus-row {{ width: 100%; }}
    EscalationsPanel.focus-row #escalation-panels {{
        width: 100%; height: auto; max-height: {FOCUS_ATTENTION_MAX_HEIGHT};
    }}
    EscalationsPanel.focus-row #escalation-panels Static {{ width: 100%; }}
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
        self._followed_task_id: str | None = None
        self._rendered_selection: tuple[object, ...] | None = None
        self._has_rendered = False
        self._keep_empty_visible = False
        self._focus_requested = False
        self._remove_motion_callback: Callable[[], None] | None = None

    def follow_task(self, task_id: str | None) -> None:
        """Show the decision of the selected task, or that it has none; `None` clears the follow."""
        if task_id == self._followed_task_id:
            return
        self._followed_task_id = task_id
        if task_id is None:
            self._selected = 0
        if widget_is_attached(self):
            self.call_later(self._refresh)

    def allow_focus(self) -> bool:
        """Join the Tab chain only while it holds attention groups or was opened on request."""
        return bool(self._attention.groups) or self._focus_requested

    def compose(self) -> ComposeResult:
        """Reserve a bounded, scrollable area for home attention groups."""
        yield VerticalScroll(id="escalation-panels", can_focus=False)

    async def on_mount(self) -> None:
        """Render current attention and refresh on state or motion changes."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)
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
        self._focus_requested = False
        if self._focus_row:
            self.display = True

    async def _refresh_while_active(self) -> None:
        if widget_is_current(self) and cast("App[None]", self.app).is_running:
            await self._refresh()

    async def _refresh_if_attached(self) -> None:
        if widget_is_attached(self):
            await self._refresh()

    def activate_attention(self) -> None:
        """Show the attention content, retaining its useful empty state while selected."""
        self._keep_empty_visible = True
        self._focus_requested = True
        self.display = True
        self.focus()

    async def activate_selected_decision(self) -> bool:
        if not self._focus_row or not widget_is_current(self):
            return False
        decision = self._selected_decision()
        if decision is None:
            return False
        await self._open_resolution(decision)
        return True

    async def on_key(self, event: events.Key) -> None:
        """Open the selected task or its resolution."""
        if event.key in _MOVE_STEPS and not self._attention.groups:
            self._handle_vertical_key(event)
            return
        if not self._attention.groups:
            return
        if event.key == _OPEN_TASK_KEY:
            if self._focus_row:
                await self._activate_focus_task(event)
            else:
                await self._open_task_detail(event)

    async def on_click(self, event: events.Click) -> None:
        if not self._focus_row or not self._attention.groups:
            return
        self.focus()
        if self._not_escalated_task_id() is not None:
            event.stop()
            return
        group = self._attention.groups[self._selected]
        if group.task_id is not None and self._widget_context.on_task_selected is not None:
            self._widget_context.on_task_selected(group.task_id)
        if group.escalations:
            await self._open_resolution(self._selected_decision() or group.escalations[0])
        elif self._focus_row and group.task_id is not None:
            event.stop()
        elif group.task_id is not None and self._widget_context.open_task_detail is not None:
            await self._widget_context.open_task_detail(group.task_id)
        else:
            await self._open_fault_evidence()
        event.stop()

    def _handle_vertical_key(self, event: events.Key) -> None:
        if self._focus_row:
            panels = self.query_one("#escalation-panels", VerticalScroll)
            panels.scroll_to(
                y=panels.scroll_y + _MOVE_STEPS[event.key],
                animate=False,
                immediate=True,
            )
        event.stop()

    async def _open_task_detail(self, event: events.Key) -> None:
        group = self._attention.groups[self._selected]
        if group.task_id is not None and self._widget_context.open_task_detail is not None:
            on_task_selected = self._widget_context.on_task_selected
            if on_task_selected is not None:
                on_task_selected(group.task_id)
            await self._widget_context.open_task_detail(group.task_id)
            event.stop()

    async def _activate_focus_task(self, event: events.Key) -> None:
        if self._not_escalated_task_id() is not None:
            event.stop()
            return
        decision = self._selected_decision()
        if decision is not None:
            await self._open_resolution(decision)
            event.stop()
            return
        group = self._attention.groups[self._selected]
        if group.task_id is not None and self._widget_context.on_task_selected is not None:
            self._widget_context.on_task_selected(group.task_id)
        event.stop()

    async def _open_resolution(self, decision: EscalationPanelView) -> None:
        if not widget_is_current(self):
            return
        if self._widget_context.open_resolution is not None:
            await self._widget_context.open_resolution(decision.escalation_id)

    async def _open_fault_evidence(self) -> None:
        open_view = self._widget_context.open_contextual_view
        if open_view is not None:
            await open_view(FOCUS_FAULTS_VIEW)

    async def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
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
        self._follow_selected_task()
        unchanged = groups_unchanged and current_status == previous_status
        if unchanged and self._has_rendered and self._rendered_selection == self._selection_state():
            return
        await self._render_panels()
        self._has_rendered = True

    def _follow_selected_task(self) -> None:
        followed = self._followed_task_id
        if followed is None:
            return
        groups = self._attention.groups
        candidates = [index for index, group in enumerate(groups) if group.task_id == followed] or [
            index for index, group in enumerate(groups) if not group.escalations
        ]
        if candidates:
            self._selected = candidates[0]

    def _not_escalated_task_id(self) -> str | None:
        followed = self._followed_task_id
        if followed is None or not self._focus_row:
            return None
        has_row = any(
            group.task_id == followed or not group.escalations for group in self._attention.groups
        )
        return None if has_row else followed

    def _selection_state(self) -> tuple[object, ...]:
        return (self._selected, self._selected_escalation_id(), self._not_escalated_task_id())

    def _selected_group_id(self) -> str | None:
        if not self._attention.groups or self._selected >= len(self._attention.groups):
            return None
        return self._attention.groups[self._selected].task_id

    def _selected_decision(self) -> EscalationPanelView | None:
        if self._not_escalated_task_id() is not None:
            return None
        if not self._attention.groups or self._selected >= len(self._attention.groups):
            return None
        escalations = self._attention.groups[self._selected].escalations
        return escalations[0] if escalations else None

    def _selected_escalation_id(self) -> str | None:
        decision = self._selected_decision()
        return decision.escalation_id if decision is not None else None

    async def _render_panels(self) -> None:
        self._rendered_selection = self._selection_state()
        self.set_class(self._focus_row and self._selected_decision() is not None, ARMED_CLASS)
        panels = self.query_one("#escalation-panels", VerticalScroll)
        scroll_x, scroll_y = panels.scroll_x, panels.scroll_y
        if self._focus_row or not self._attention.groups:
            status = (
                self._render_compact_attention()
                if self._focus_row
                else "No open decisions, diagnosed stalls or recorded engine faults."
            )
            children = list(panels.query(GatedStatic))
            if len(children) == 1:
                children[0].update_if_changed(status)
                return
            await self._replace_panels(panels, [GatedStatic(status, markup=False)])
            return
        children = list(panels.query(GatedStatic))
        if len(children) == len(self._attention.groups):
            for index, (card, group) in enumerate(
                zip(children, self._attention.groups, strict=True)
            ):
                card.update_if_changed(self._render_group(index, group))
            panels.scroll_to(x=scroll_x, y=scroll_y, animate=False)
            return
        cards = [
            GatedStatic(self._render_group(index, group), markup=False)
            for index, group in enumerate(self._attention.groups)
        ]
        if await self._replace_panels(panels, cards):
            panels.scroll_to(x=scroll_x, y=scroll_y, animate=False)

    async def _replace_panels(self, panels: VerticalScroll, cards: list[GatedStatic]) -> bool:
        await panels.remove_children()
        if not widget_is_attached(self):
            return False
        await panels.mount(*cards)
        return widget_is_attached(self)

    def _render_compact_attention(self) -> RenderableType:
        if self._summary is None:
            return Text("○ Engine status unavailable", style=SECONDARY_TEXT_COLOR)
        decision = self._selected_decision()
        action_hint = ACTION_HINT
        if self._focus_row and decision is not None:
            action_hint = "Enter answer ↵" if decision.answer_available else "Enter resolve ↵"
        return FocusAttentionStatus(
            self._attention,
            self._summary,
            action_hint=action_hint,
            selected_group_index=self._selected,
            selected_escalation_id=self._selected_escalation_id(),
            not_escalated_task_id=self._not_escalated_task_id(),
        )

    def _render_group(self, index: int, group: AttentionGroupView) -> Group:
        marker = _SELECTED_MARKER if index == self._selected else "  "
        identity = f"{group.task_id} · {group.title}" if group.task_id is not None else group.title
        lines = [f"{marker}{identity}"]
        lines.extend(f"  {reason}" for reason in group.reasons)
        content: list[RenderableType] = ["\n".join(lines)]
        selected_id = self._selected_escalation_id()
        for item in group.escalations:
            shown = display_escalation_headline(item.task_id, item.headline, item.reason)
            headline = f"{item.escalation_id} · {shown} · {item.age}"
            if index == self._selected and item.escalation_id == selected_id:
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
            content.append(key_hint("  Enter: task detail"))
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


class EscalationsWidget:
    """The `escalations` dashboard widget plugin."""

    name = "escalations"
    placement = WidgetPlacement.ATTENTION

    def create(self, context: WidgetContext) -> Widget:
        """Build the escalation panel for `context`."""
        return EscalationsPanel(context)
