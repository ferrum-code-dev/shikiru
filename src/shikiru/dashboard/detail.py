"""Shared task detail presentation for the workspace and fixed task view."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from shikiru.dashboard.contract import (
    PaletteRequest,
    TaskTimelineCategory,
    TaskTimelineSource,
)
from shikiru.domain.tasks import Hold, Mode, Role, Status, StatusReason
from shikiru.errors import ShikiruError
from shikiru.ui.compact import AgentUsageRow, ProjectUsageMetric, StageActivity

if TYPE_CHECKING:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.events import MouseScrollUp
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.dashboard.contract import (
        TaskDetailView,
        TaskReferenceView,
        TaskRelationshipsView,
        TaskReportView,
        TaskTimelineEntryView,
        TaskTimelinePageView,
        WidgetContext,
    )

TASK_DETAIL_WORKER_GROUP = "task-detail-updates"
TASK_DETAIL_ESCAPE = "escape"
TASK_DETAIL_BACK_BUTTON = "task-detail-back"
TASK_DETAIL_WINDOW_BUTTON = "task-detail-window"
TASK_DETAIL_LEADER_BUTTON = "task-detail-leader"
TASK_DETAIL_IMPLEMENTER_BUTTON = "task-detail-implementer"
TASK_DETAIL_ACTIONS_BUTTON = "task-detail-actions"
TASK_DETAIL_EXPORT_BUTTON = "task-detail-export"
TASK_DETAIL_ACTION_ROLE = "task-detail-action-role"
TASK_DETAIL_HAND_BACK_LEADER_BUTTON = "task-detail-hand-back-leader"
TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON = "task-detail-hand-back-implementer"
TASK_DETAIL_RESUME_BUTTON = "task-detail-resume"
TASK_DETAIL_ATTENTION_BUTTON = "task-detail-attention"
TASK_DETAIL_IDENTITY = "task-detail-identity"
TASK_DETAIL_OWNERSHIP = "task-detail-ownership"
TASK_DETAIL_SITUATION = "task-detail-situation"
TASK_DETAIL_PROGRESS = "task-detail-progress"
TASK_DETAIL_REQUEST = "task-detail-request"
TASK_DETAIL_CRITERIA = "task-detail-criteria"
TASK_DETAIL_REPORT = "task-detail-report"
TASK_DETAIL_VERDICT = "task-detail-verdict"
TASK_DETAIL_EVIDENCE = "task-detail-evidence"
TASK_DETAIL_FACTS = "task-detail-facts"
TASK_DETAIL_WORKSPACE = "task-detail-workspace"
TASK_DETAIL_SESSIONS = "task-detail-sessions"
TASK_DETAIL_FEEDBACK = "task-detail-feedback"
TASK_DETAIL_BODY = "task-detail-body"
TASK_DETAIL_TOOLBAR = "task-detail-toolbar"
TASK_DETAIL_BLOCKER = "task-detail-blocker"
TASK_DETAIL_RELATIONSHIPS = "task-detail-relationships"
TASK_DETAIL_RELATIONSHIPS_BUTTON = "task-detail-relationships-toggle"
TASK_DETAIL_RELATED_TOOLS = "task-detail-related-tools"
TASK_DETAIL_RELATED_TASK = "task-detail-related-task"
TASK_DETAIL_OPEN_RELATED = "task-detail-open-related"
TASK_DETAIL_PREVIOUS_RELATIONSHIPS = "task-detail-previous-relationships"
TASK_DETAIL_MORE_RELATIONSHIPS = "task-detail-more-relationships"
TASK_DETAIL_RELATIONSHIP_PAGE = "task-detail-relationship-page"
TASK_DETAIL_TIMELINE_ROLE = "task-detail-timeline-role"
TASK_DETAIL_TIMELINE_TURN = "task-detail-timeline-turn"
TASK_DETAIL_TIMELINE_CATEGORY = "task-detail-timeline-category"
TASK_DETAIL_TIMELINE_FILTER = "task-detail-timeline-filter"
TASK_DETAIL_TIMELINE_NEWER = "task-detail-timeline-newer"
TASK_DETAIL_TIMELINE_OLDER = "task-detail-timeline-older"
TASK_DETAIL_TIMELINE_FOLLOW = "task-detail-timeline-follow"
TASK_DETAIL_TIMELINE_STATUS = "task-detail-timeline-status"
TASK_DETAIL_TIMELINE_ENTRY = "task-detail-timeline-entry"
TASK_DETAIL_TIMELINE_SELECTED = "task-detail-timeline-selected"
TIMELINE_FILTER_OPTIONS = (
    ("Decisions and transitions", TaskTimelineCategory.ACTIVITY),
    ("All engine events", TaskTimelineCategory.ENGINE),
    ("Decisions", TaskTimelineCategory.DECISION),
    ("Transitions", TaskTimelineCategory.TRANSITION),
    ("Agent signals", TaskTimelineCategory.SIGNAL),
    ("Tools", TaskTimelineCategory.TOOL),
    ("Messages", TaskTimelineCategory.MESSAGE),
    ("Usage", TaskTimelineCategory.USAGE),
    ("All evidence", TaskTimelineCategory.ALL),
)
NO_VALUE = "\N{EN DASH}"
NO_REPORT = "No validated report is recorded."
NO_VERDICT = "No validated verdict is recorded."
NO_CRITERIA = "No validated acceptance criteria are recorded."
NO_EVIDENCE = "No recorded agent signals or task events are available."
NO_FACTS = "No engine-computed git facts are recorded."
CLOSED_RUN_STATUS = "closed"


class TaskDetailBody(VerticalScroll):
    def __init__(self, scroll_away: Callable[[], None]) -> None:
        super().__init__(id="task-detail-body")
        self._scroll_away = scroll_away

    def on_resize(self, event: events.Resize) -> None:
        del event
        focused = self.screen.focused
        if focused is not None and self in focused.ancestors and not self.can_view_entire(focused):
            self.call_after_refresh(
                self.scroll_to_center,
                focused,
                animate=False,
            )

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        del event
        self._pause_if_scrolled()

    def action_scroll_up(self) -> None:
        self._pause_if_scrolled()
        super().action_scroll_up()

    def action_page_up(self) -> None:
        self._pause_if_scrolled()
        super().action_page_up()

    def action_scroll_home(self) -> None:
        self._pause_if_scrolled()
        super().action_scroll_home()

    def _pause_if_scrolled(self) -> None:
        if self.scroll_y > 0:
            self._scroll_away()


class TaskDetailPanel(Vertical):
    """Render shared task detail from the injected dashboard view contract."""

    DEFAULT_CSS = """
    #task-detail-panel { width: 100%; height: auto; }
    #task-detail-identity {
        height: auto; padding: 1; border-bottom: solid $neutral-border;
        background: $panel; color: $primary-text; text-style: bold;
    }
    #task-detail-toolbar { width: 100%; height: 3; padding: 0 1; }
    #task-detail-toolbar Button {
        width: auto; min-width: 1; margin-right: 1;
        border: round $neutral-border; background: $surface; color: $primary-text;
    }
    #task-detail-toolbar Button:focus { border: round $focus; text-style: bold reverse; }
    #task-detail-related-tools { width: 100%; height: auto; }
    #task-detail-related-tools HorizontalScroll { width: 100%; }
    #task-detail-related-tools Horizontal { width: 100%; }
    #task-detail-feedback { height: auto; min-height: 1; color: $secondary-text; }
    #task-detail-body { height: auto; max-height: 32; padding: 0 1; }
    .task-detail-section {
        height: auto; padding: 1; margin-bottom: 1;
        border: round $neutral-border; background: $surface; color: $primary-text;
    }
    .task-detail-fixed #task-detail-body { max-height: 24; }
    """

    def __init__(
        self,
        context: WidgetContext,
        task_id: str | None,
        *,
        fixed: bool,
        embedded: bool = False,
        close_detail: CloseDetail | None = None,
    ) -> None:
        classes = "task-detail-fixed" if fixed else ""
        super().__init__(id="task-detail-panel", classes=classes)
        self._widget_context = context
        self._task_id = task_id
        self._fixed = fixed
        self._embedded = embedded
        self._close_detail = close_detail
        self._refresh_lock = asyncio.Lock()
        self._refresh_worker: Worker[None] | None = None
        self._change_worker: Worker[None] | None = None
        self._relationships_expanded = False
        self._relationship_offset = 0
        self._relationship_previous_offset: int | None = None
        self._relationship_next_offset: int | None = None
        self._shown_related_references: tuple[TaskReferenceView, ...] = ()
        self._selected_related_reference: TaskReferenceView | None = None
        self._timeline_cursors: list[str | None] = [None]
        self._timeline_following = True
        self._timeline_role: str | None = None
        self._timeline_turn: str | None = None
        self._timeline_category = TaskTimelineCategory.ACTIVITY.value
        self._timeline_selected_identity: str | None = None
        self._timeline_selected_entry: TaskTimelineEntryView | None = None
        self._timeline_page: TaskTimelinePageView | None = None
        self._timeline_worker: Worker[None] | None = None
        self._timeline_request_generation = 0
        self._body_focus_generation = 0
        self._rendered_task_id: str | None = None
        self._remove_motion_callback: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        with HorizontalScroll(id=TASK_DETAIL_TOOLBAR):
            back_label = "Full view" if self._embedded else "Home" if self._fixed else "Back"
            yield Button(back_label, id=TASK_DETAIL_BACK_BUTTON)
            yield Button("Task window", id=TASK_DETAIL_WINDOW_BUTTON)
            yield Button("Leader pane", id=TASK_DETAIL_LEADER_BUTTON)
            yield Button("Implementer pane", id=TASK_DETAIL_IMPLEMENTER_BUTTON)
            yield Button("Hand back Leader", id=TASK_DETAIL_HAND_BACK_LEADER_BUTTON)
            yield Button("Hand back Implementer", id=TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON)
            yield Button("Resume", id=TASK_DETAIL_RESUME_BUTTON)
            yield Select(
                ((role.value.title(), role.value) for role in (Role.LEADER, Role.IMPLEMENTER)),
                prompt="Acting role",
                allow_blank=True,
                value=Select.BLANK,
                id=TASK_DETAIL_ACTION_ROLE,
            )
            yield from self._operator_action_buttons()
        yield Static("Loading task detail…", id=TASK_DETAIL_IDENTITY, markup=False)
        yield Static("", id=TASK_DETAIL_OWNERSHIP, markup=False)
        yield Static("", id=TASK_DETAIL_FEEDBACK, markup=False)
        with TaskDetailBody(self._pause_timeline_follow):
            yield Static("", id=TASK_DETAIL_SITUATION, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_BLOCKER, classes="task-detail-section", markup=False)
            yield Button("Expand family and relationships", id=TASK_DETAIL_RELATIONSHIPS_BUTTON)
            with Vertical(id=TASK_DETAIL_RELATED_TOOLS):
                with HorizontalScroll():
                    yield Select(
                        (),
                        prompt="Related task",
                        allow_blank=True,
                        value=Select.BLANK,
                        id=TASK_DETAIL_RELATED_TASK,
                    )
                    yield Button("Open related task", id=TASK_DETAIL_OPEN_RELATED)
                yield Static("Page 1", id=TASK_DETAIL_RELATIONSHIP_PAGE)
                with Horizontal():
                    yield Button("Prev", id=TASK_DETAIL_PREVIOUS_RELATIONSHIPS)
                    yield Button("More", id=TASK_DETAIL_MORE_RELATIONSHIPS)
            yield Static("Evidence timeline", classes="task-detail-section", markup=False)
            with HorizontalScroll(id="task-detail-timeline-filters"):
                yield Select(
                    TIMELINE_FILTER_OPTIONS,
                    value=TaskTimelineCategory.ACTIVITY,
                    id=TASK_DETAIL_TIMELINE_CATEGORY,
                )
                yield Select(
                    (
                        ("Any role", ""),
                        ("Leader", Role.LEADER.value),
                        ("Implementer", Role.IMPLEMENTER.value),
                        ("Helper", Role.HELPER.value),
                    ),
                    prompt="Any role",
                    allow_blank=True,
                    value=Select.BLANK,
                    id=TASK_DETAIL_TIMELINE_ROLE,
                )
                yield Input(placeholder="Any turn", id=TASK_DETAIL_TIMELINE_TURN)
                yield Button("Apply filters", id=TASK_DETAIL_TIMELINE_FILTER)
            with HorizontalScroll(id="task-detail-timeline-navigation"):
                yield Button("Newer", id=TASK_DETAIL_TIMELINE_NEWER)
                yield Button("Older", id=TASK_DETAIL_TIMELINE_OLDER)
                yield Button("Follow latest", id=TASK_DETAIL_TIMELINE_FOLLOW)
            yield Static("", id=TASK_DETAIL_TIMELINE_STATUS, markup=False)
            yield Select(
                (),
                prompt="Recorded evidence",
                allow_blank=True,
                value=Select.BLANK,
                id=TASK_DETAIL_TIMELINE_ENTRY,
            )
            yield Static(
                "No evidence selected.",
                id=TASK_DETAIL_TIMELINE_SELECTED,
                classes="task-detail-section",
                markup=False,
            )
            yield Static(
                "",
                id=TASK_DETAIL_RELATIONSHIPS,
                classes="task-detail-section",
                markup=False,
            )
            yield Static("", id=TASK_DETAIL_PROGRESS, classes="task-detail-section")
            yield Static("", id=TASK_DETAIL_REQUEST, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_CRITERIA, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_REPORT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_VERDICT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_EVIDENCE, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_FACTS, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_WORKSPACE, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_SESSIONS, classes="task-detail-section", markup=False)

    @staticmethod
    def _operator_action_buttons() -> ComposeResult:
        yield Button("Actions · Ctrl+P", id=TASK_DETAIL_ACTIONS_BUTTON)
        yield Button("Export evidence", id=TASK_DETAIL_EXPORT_BUTTON)
        yield Button("Attention · Home", id=TASK_DETAIL_ATTENTION_BUTTON)

    async def on_mount(self) -> None:
        self.disabled = self._task_id is None
        identity = self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        identity.update(
            f"{self._task_id} · loading selected task"
            if self._task_id is not None
            else "Select a task to inspect it."
        )
        self._refresh_worker = self.run_worker(self._refresh(), group=TASK_DETAIL_WORKER_GROUP)
        self._change_worker = self.run_worker(
            self._widget_context.on_change(self._refresh),
            group=TASK_DETAIL_WORKER_GROUP,
        )
        self._timeline_worker = self.run_worker(
            self._watch_timeline(), group=TASK_DETAIL_WORKER_GROUP
        )
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_live)

    def on_unmount(self) -> None:
        if self._refresh_worker is not None:
            self._refresh_worker.cancel()
        if self._change_worker is not None:
            self._change_worker.cancel()
        if self._timeline_worker is not None:
            self._timeline_worker.cancel()
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        if body in event.widget.ancestors:
            self._body_focus_generation += 1
            if not body.can_view_entire(event.widget):
                self.call_after_refresh(
                    body.scroll_to_center,
                    event.widget,
                    animate=False,
                )
                return
        parent = event.widget.parent
        while parent is not None:
            if parent.id == TASK_DETAIL_TOOLBAR:
                self.query_one(f"#{TASK_DETAIL_TOOLBAR}", HorizontalScroll).scroll_to_widget(
                    event.widget,
                    animate=False,
                )
                return
            parent = parent.parent

    def select_task(self, task_id: str | None) -> None:
        if task_id == self._task_id:
            return
        self._task_id = task_id
        self._reset_task_identity_state()
        self._timeline_request_generation += 1
        self.disabled = task_id is None
        if not self.is_mounted:
            return
        identity = self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        identity.update(
            f"{task_id} · loading selected task"
            if task_id is not None
            else "Select a task to inspect it."
        )
        self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody).scroll_to(y=0, animate=False)
        if task_id is None:
            return
        self._queue_refresh()

    def _reset_task_identity_state(self) -> None:
        self._relationship_offset = 0
        self._relationships_expanded = False
        self._relationship_previous_offset = None
        self._relationship_next_offset = None
        self._shown_related_references = ()
        self._selected_related_reference = None
        self._timeline_cursors = [None]
        self._timeline_following = True
        self._timeline_role = None
        self._timeline_turn = None
        self._timeline_category = TaskTimelineCategory.ACTIVITY.value
        self._timeline_selected_identity = None
        self._timeline_selected_entry = None
        self._timeline_page = None
        if not self.is_mounted:
            return
        for section_id in (
            TASK_DETAIL_OWNERSHIP,
            TASK_DETAIL_SITUATION,
            TASK_DETAIL_BLOCKER,
            TASK_DETAIL_PROGRESS,
            TASK_DETAIL_REQUEST,
            TASK_DETAIL_CRITERIA,
            TASK_DETAIL_REPORT,
            TASK_DETAIL_VERDICT,
            TASK_DETAIL_EVIDENCE,
            TASK_DETAIL_FACTS,
            TASK_DETAIL_WORKSPACE,
            TASK_DETAIL_SESSIONS,
        ):
            self.query_one(f"#{section_id}", Static).update("")
        self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static).display = False
        self.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select).value = Select.BLANK
        self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update("")
        related_select = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_RELATED_TASK}", Select))
        related_select.value = Select.BLANK
        related_select.set_options(())
        self.query_one(f"#{TASK_DETAIL_OPEN_RELATED}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIP_PAGE}", Static).update("Page 1")
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).update("")
        self._render_relationship_visibility()
        self.query_one(f"#{TASK_DETAIL_TIMELINE_ROLE}", Select).value = Select.BLANK
        self.query_one(f"#{TASK_DETAIL_TIMELINE_TURN}", Input).value = ""
        self.query_one(
            f"#{TASK_DETAIL_TIMELINE_CATEGORY}", Select
        ).value = TaskTimelineCategory.ACTIVITY.value
        timeline_entry = cast(
            "Select[str]", self.query_one(f"#{TASK_DETAIL_TIMELINE_ENTRY}", Select)
        )
        timeline_entry.value = Select.BLANK
        timeline_entry.set_options(())
        self.query_one(f"#{TASK_DETAIL_TIMELINE_SELECTED}", Static).update(
            "No recorded evidence is available for this page."
        )
        self.query_one(f"#{TASK_DETAIL_TIMELINE_STATUS}", Static).update(
            "Loading evidence timeline…"
        )
        for control_id in (
            TASK_DETAIL_TIMELINE_NEWER,
            TASK_DETAIL_TIMELINE_OLDER,
            TASK_DETAIL_TIMELINE_FOLLOW,
        ):
            self.query_one(f"#{control_id}", Button).disabled = True

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            TASK_DETAIL_BACK_BUTTON: self._back,
            TASK_DETAIL_WINDOW_BUTTON: self._navigate_task_window,
            TASK_DETAIL_LEADER_BUTTON: lambda: self._navigate_role_pane(Role.LEADER),
            TASK_DETAIL_IMPLEMENTER_BUTTON: lambda: self._navigate_role_pane(Role.IMPLEMENTER),
            TASK_DETAIL_HAND_BACK_LEADER_BUTTON: lambda: self._open_palette(
                Role.LEADER, action="hand-back"
            ),
            TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON: lambda: self._open_palette(
                Role.IMPLEMENTER, action="hand-back"
            ),
            TASK_DETAIL_RESUME_BUTTON: lambda: self._open_palette(action="resume"),
            TASK_DETAIL_ACTIONS_BUTTON: self.open_palette,
            TASK_DETAIL_EXPORT_BUTTON: lambda: self._open_palette(action="export"),
            TASK_DETAIL_ATTENTION_BUTTON: self._navigate_home,
            TASK_DETAIL_RELATIONSHIPS_BUTTON: self._toggle_relationships,
            TASK_DETAIL_OPEN_RELATED: self._open_related_task,
            TASK_DETAIL_PREVIOUS_RELATIONSHIPS: self._previous_relationship_page,
            TASK_DETAIL_MORE_RELATIONSHIPS: self._next_relationship_page,
            TASK_DETAIL_TIMELINE_FILTER: self._apply_timeline_filters,
            TASK_DETAIL_TIMELINE_NEWER: self._newer_timeline_page,
            TASK_DETAIL_TIMELINE_OLDER: self._older_timeline_page,
            TASK_DETAIL_TIMELINE_FOLLOW: self._follow_latest,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            await handler()

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == TASK_DETAIL_TIMELINE_ENTRY:
            identity = event.value if isinstance(event.value, str) else None
            self._select_timeline_entry(identity)

    async def _watch_timeline(self) -> None:
        while True:
            await self._widget_context.clock.sleep(self._widget_context.timeline_refresh_seconds)
            if self._task_id is not None and self._timeline_following and self._screen_is_active():
                await self._refresh_timeline()

    async def _refresh_timeline(self) -> None:
        async with self._refresh_lock:
            if not self._screen_is_active():
                return
            task_id = self._task_id
            if task_id is None:
                return
            generation = self._timeline_request_generation
            cursor = None if self._timeline_following else self._timeline_cursors[-1]
            role = self._timeline_role
            turn = self._timeline_turn
            category = self._timeline_category
            try:
                page = await self._widget_context.views.task_timeline(
                    task_id,
                    cursor=cursor,
                    role=role,
                    turn=turn,
                    category=category,
                )
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                if self._screen_is_active() and generation == self._timeline_request_generation:
                    self.query_one(f"#{TASK_DETAIL_TIMELINE_STATUS}", Static).update(
                        f"Evidence timeline refresh failed: {error}"
                    )
                return
            if (
                self._screen_is_active()
                and task_id == self._task_id
                and generation == self._timeline_request_generation
            ):
                self._render_timeline(page)

    def _screen_is_active(self) -> bool:
        if not self.is_mounted:
            return False
        app = cast("App[None]", self.app)
        return app.is_running and app.screen is self.screen

    def _pause_timeline_follow(self) -> None:
        if not self._timeline_following:
            return
        self._timeline_following = False
        self._timeline_request_generation += 1
        if self._screen_is_active():
            page = self._timeline_page
            status = "Follow paused while viewing older evidence"
            if page is not None and page.has_older:
                status += " · older evidence available"
            self.query_one(f"#{TASK_DETAIL_TIMELINE_STATUS}", Static).update(status)
            self.query_one(f"#{TASK_DETAIL_TIMELINE_FOLLOW}", Button).disabled = False

    async def _apply_timeline_filters(self) -> None:
        role_value = cast(
            "Select[str]", self.query_one(f"#{TASK_DETAIL_TIMELINE_ROLE}", Select)
        ).value
        category_value = cast(
            "Select[str]", self.query_one(f"#{TASK_DETAIL_TIMELINE_CATEGORY}", Select)
        ).value
        turn_value = self.query_one(f"#{TASK_DETAIL_TIMELINE_TURN}", Input).value.strip()
        self._timeline_role = role_value or None if isinstance(role_value, str) else None
        self._timeline_category = (
            category_value
            if isinstance(category_value, str)
            else TaskTimelineCategory.ACTIVITY.value
        )
        self._timeline_turn = turn_value or None
        self._timeline_cursors = [None]
        self._timeline_following = True
        self._timeline_request_generation += 1
        self._timeline_selected_identity = None
        self._timeline_selected_entry = None
        self.query_one(f"#{TASK_DETAIL_TIMELINE_ENTRY}", Select).value = Select.BLANK
        self.query_one(f"#{TASK_DETAIL_TIMELINE_SELECTED}", Static).update("No evidence selected.")
        self._queue_refresh()

    async def _older_timeline_page(self) -> None:
        page = self._timeline_page
        if page is None or not page.has_older or page.older_cursor is None:
            return
        self._timeline_cursors.append(page.older_cursor)
        self._timeline_following = False
        self._timeline_request_generation += 1
        self._queue_refresh()

    async def _newer_timeline_page(self) -> None:
        if len(self._timeline_cursors) <= 1:
            return
        self._timeline_cursors.pop()
        self._timeline_following = len(self._timeline_cursors) == 1
        self._timeline_request_generation += 1
        self._queue_refresh()

    async def _follow_latest(self) -> None:
        self._timeline_cursors = [None]
        self._timeline_following = True
        self._timeline_request_generation += 1
        self._queue_refresh()

    async def _toggle_relationships(self) -> None:
        self._relationships_expanded = not self._relationships_expanded
        self._render_relationship_visibility()

    async def _back(self) -> None:
        if self._fixed:
            await self._navigate_home()
            return
        if self._embedded:
            if self._task_id is not None and self._widget_context.open_task_detail is not None:
                await self._widget_context.open_task_detail(self._task_id)
            return
        if self._close_detail is not None:
            await self._close_detail()

    async def _open_palette(
        self,
        role: Role | None = None,
        *,
        action: str | None = None,
    ) -> None:
        if self._task_id is None:
            return
        callback = self._widget_context.open_palette_context
        if callback is not None:
            await callback(
                PaletteRequest(
                    action=action,
                    task_id=self._task_id,
                    role=role.value if role is not None else None,
                )
            )
            return
        await self._widget_context.open_palette(action or "")

    async def open_palette(self) -> None:
        """Open the shared palette with this detail's current task and selected role."""
        role_value = cast(
            "Select[str]", self.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select)
        ).value
        role = Role(role_value) if isinstance(role_value, str) else None
        await self._open_palette(role)

    async def _navigate_task_window(self) -> None:
        if self._task_id is None:
            await self._show_navigation_result("Select a task before opening its window.")
            return
        callback = self._widget_context.navigate_task_window
        await self._show_navigation_result(
            await callback(self._task_id)
            if callback is not None
            else "Task window navigation is unavailable."
        )

    async def _navigate_role_pane(self, role: Role) -> None:
        if self._task_id is None:
            await self._show_navigation_result("Select a task before opening a role pane.")
            return
        callback = self._widget_context.navigate_role_pane
        await self._show_navigation_result(
            await callback(self._task_id, role.value)
            if callback is not None
            else f"{role.value} pane navigation is unavailable."
        )

    async def _navigate_home(self) -> None:
        callback = self._widget_context.navigate_home
        await self._show_navigation_result(
            await callback()
            if callback is not None
            else "Home workspace navigation is unavailable."
        )

    async def _show_navigation_result(self, result: str) -> None:
        self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(result)

    async def _refresh(self, *, include_timeline: bool = True) -> None:
        async with self._refresh_lock:
            if not self._screen_is_active():
                return
            task_id = self._task_id
            if task_id is None:
                self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
                    "Select a task to inspect it."
                )
                return
            generation = self._timeline_request_generation
            try:
                view = await self._widget_context.views.task_detail(
                    task_id,
                    relationship_offset=self._relationship_offset,
                    timeline_cursor=(
                        None if self._timeline_following else self._timeline_cursors[-1]
                    ),
                    timeline_role=self._timeline_role,
                    timeline_turn=self._timeline_turn,
                    timeline_category=self._timeline_category,
                    include_timeline=include_timeline,
                )
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                if self._screen_is_active() and generation == self._timeline_request_generation:
                    self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(
                        f"Task detail refresh failed: {error}"
                    )
                return
            if not self._screen_is_active():
                return
            if self._task_id != task_id:
                return
            if view is None:
                self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
                    f"{task_id} · task record is unavailable"
                )
                self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(
                    "Task state could not be read."
                )
                return
            self._render_view(
                view,
                render_timeline=(
                    include_timeline and generation == self._timeline_request_generation
                ),
            )

    async def _refresh_live(self) -> None:
        if self._task_id is not None and self._screen_is_active():
            await self._refresh(include_timeline=False)

    def _queue_refresh(self) -> None:
        if self._task_id is None:
            return
        self.run_worker(
            self._refresh(),
            group=TASK_DETAIL_WORKER_GROUP,
            exclusive=False,
        )

    def refresh_after_resume(self) -> None:
        """Refresh the current task after this detail screen becomes active again."""
        self._queue_refresh()

    def _render_view(self, view: TaskDetailView, *, render_timeline: bool = True) -> None:
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        scroll_y = body.scroll_target_y
        focus_generation = self._body_focus_generation
        focused = self.screen.focused
        restore_scroll = self._rendered_task_id == view.task_id
        self._rendered_task_id = view.task_id
        action_role = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_ACTION_ROLE}", Select))
        action_role.prompt = f"Acting · {view.acting_role.title()}"
        identity = (
            f"{view.task_id} · {view.title}\n"
            f"{view.status} · {view.step} · iteration {view.iteration}"
        )
        self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(identity)
        situation = [f"Situation · {view.status} · {view.step}"]
        if view.status_reason is not None:
            situation.append(f"Status reason · {view.status_reason}")
        if view.mode is not None:
            situation.append(f"Mode · {view.mode}")
        if view.holds:
            situation.append(f"Holds · {', '.join(view.holds)}")
        if view.wait_reason is not None:
            situation.append(f"Waiting · {view.wait_reason}")
        situation.append(f"Submitted age · {view.submitted_age}")
        if view.requeued_age is not None:
            situation.append(f"Recorded requeued age · {view.requeued_age}")
        if view.ownership:
            situation.append(f"Operator owns · {', '.join(view.ownership)}")
        self._render_ownership(view)
        attention = view.attention
        situation.append(
            f"Global attention · {attention.decisions} decisions · {attention.faults} engine faults"
        )
        self.query_one(f"#{TASK_DETAIL_SITUATION}", Static).update("\n".join(situation))
        blocker_lines = list(view.blocker.lines) if view.blocker is not None else []
        blocker = self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static)
        blocker.display = bool(blocker_lines)
        blocker.update("Blocker evidence\n" + "\n".join(blocker_lines) if blocker_lines else "")
        self._render_relationships(view)
        self.query_one(f"#{TASK_DETAIL_PROGRESS}", Static).update(StageActivity(view).render())
        self.query_one(f"#{TASK_DETAIL_REQUEST}", Static).update(f"Request\n{view.request}")
        criteria = "\n".join(f"- {item}" for item in view.acceptance_criteria)
        self.query_one(f"#{TASK_DETAIL_CRITERIA}", Static).update(
            f"Acceptance criteria\n{criteria or NO_CRITERIA}"
        )
        self.query_one(f"#{TASK_DETAIL_REPORT}", Static).update(
            _report_text("Latest validated report", view.latest_report, NO_REPORT)
        )
        self.query_one(f"#{TASK_DETAIL_VERDICT}", Static).update(
            _report_text("Latest validated verdict", view.latest_verdict, NO_VERDICT)
        )
        self.query_one(f"#{TASK_DETAIL_EVIDENCE}", Static).update(_evidence_text(view))
        if render_timeline:
            self._render_timeline(view.timeline)
        facts = (
            f"Engine-computed git facts · {view.facts_turn}\n{view.recorded_facts}"
            if view.recorded_facts is not None
            else NO_FACTS
        )
        self.query_one(f"#{TASK_DETAIL_FACTS}", Static).update(facts)
        self.query_one(f"#{TASK_DETAIL_WORKSPACE}", Static).update(_workspace_text(view))
        self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(_sessions_text(view))
        if restore_scroll:
            body.call_after_refresh(
                self._restore_body_scroll,
                body,
                scroll_y,
                focus_generation,
                focused,
            )

    def _restore_body_scroll(
        self,
        body: TaskDetailBody,
        scroll_y: float,
        focus_generation: int,
        focused: Widget | None,
    ) -> None:
        if (
            self._screen_is_active()
            and focus_generation == self._body_focus_generation
            and self.screen.focused is focused
            and body.scroll_target_y == scroll_y
        ):
            body.scroll_to(y=scroll_y, animate=False)

    def _render_timeline(self, page: TaskTimelinePageView | None) -> None:
        self._timeline_page = page
        entry_select = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_TIMELINE_ENTRY}", Select))
        page_entries = list(page.entries) if page is not None else []
        current = next(
            (item for item in page_entries if item.identity == self._timeline_selected_identity),
            None,
        )
        if current is not None:
            self._timeline_selected_entry = current
        elif self._timeline_selected_identity is None and page_entries:
            current = page_entries[0]
            self._timeline_selected_identity = current.identity
            self._timeline_selected_entry = current
        options = [(_timeline_option(item), item.identity) for item in page_entries]
        available = {identity for _, identity in options}
        if (
            self._timeline_selected_entry is not None
            and self._timeline_selected_identity not in available
        ):
            selected = self._timeline_selected_entry
            options.append(
                (f"Selected outside page · {_timeline_option(selected)}", selected.identity)
            )
        entry_select.set_options(options)
        entry_select.value = (
            self._timeline_selected_identity
            if self._timeline_selected_identity in {value for _, value in options}
            else Select.BLANK
        )
        self._select_timeline_entry(self._timeline_selected_identity)
        status = (
            "Following latest"
            if self._timeline_following
            else "Follow paused while viewing older evidence"
        )
        if page is None:
            status += " · evidence page unavailable"
        elif page.has_older:
            status += " · older evidence available"
        self.query_one(f"#{TASK_DETAIL_TIMELINE_STATUS}", Static).update(status)
        self.query_one(f"#{TASK_DETAIL_TIMELINE_NEWER}", Button).disabled = (
            len(self._timeline_cursors) <= 1
        )
        self.query_one(f"#{TASK_DETAIL_TIMELINE_OLDER}", Button).disabled = (
            page is None or not page.has_older
        )
        self.query_one(
            f"#{TASK_DETAIL_TIMELINE_FOLLOW}", Button
        ).disabled = self._timeline_following

    def _select_timeline_entry(self, identity: str | None) -> None:
        if identity is not None and self._timeline_page is not None:
            entry = next(
                (item for item in self._timeline_page.entries if item.identity == identity),
                None,
            )
            if entry is not None:
                self._timeline_selected_identity = identity
                self._timeline_selected_entry = entry
        selected = self._timeline_selected_entry
        target = self.query_one(f"#{TASK_DETAIL_TIMELINE_SELECTED}", Static)
        if selected is None:
            target.update("No recorded evidence is available for this page.")
            return
        lines = [
            f"{selected.source} · {selected.category}",
            f"Timestamp · {selected.timestamp or NO_VALUE}",
        ]
        if selected.timestamp_error:
            lines.append(f"Timestamp evidence · {selected.timestamp_error}")
        if selected.role is not None:
            lines.append(f"Role · {selected.role}")
        if selected.turn is not None:
            lines.append(f"Turn · {selected.turn}")
        if selected.event_id is not None:
            lines.append(f"Engine event ID · {selected.event_id}")
        if selected.incomplete:
            lines.append("Incomplete live JSONL row")
        lines.extend((selected.summary, selected.detail))
        target.update("\n".join(lines))

    async def _open_related_task(self) -> None:
        selected = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_RELATED_TASK}", Select)).value
        callback = self._widget_context.open_task_detail
        if isinstance(selected, str) and callback is not None:
            await callback(selected)

    async def _next_relationship_page(self) -> None:
        if self._relationship_next_offset is None:
            return
        more = self.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button)
        focused = more.has_focus
        self._relationship_offset = self._relationship_next_offset
        await self._refresh()
        if focused and more.disabled:
            self.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button).focus()

    async def _previous_relationship_page(self) -> None:
        if self._relationship_previous_offset is None:
            return
        previous = self.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button)
        focused = previous.has_focus
        self._relationship_offset = self._relationship_previous_offset
        await self._refresh()
        if focused and previous.disabled:
            self.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button).focus()

    def _render_relationships(self, view: TaskDetailView) -> None:
        related_select = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_RELATED_TASK}", Select))
        previous_value = related_select.value
        if isinstance(previous_value, str):
            self._selected_related_reference = next(
                (item for item in self._shown_related_references if item.task_id == previous_value),
                self._selected_related_reference,
            )
        references = list(_related_references(view))
        reference_ids = {item.task_id for item in references}
        if (
            isinstance(previous_value, str)
            and previous_value not in reference_ids
            and self._selected_related_reference is not None
            and self._selected_related_reference.task_id == previous_value
        ):
            references.append(self._selected_related_reference)
        self._shown_related_references = tuple(references)
        options = tuple(
            (f"{item.task_id} · {item.title} · {item.status}", item.task_id)
            for item in self._shown_related_references
        )
        related_select.set_options(options)
        valid_values = {value for _, value in options}
        related_select.value = (
            previous_value
            if isinstance(previous_value, str) and previous_value in valid_values
            else Select.BLANK
        )
        self.query_one(f"#{TASK_DETAIL_OPEN_RELATED}", Button).disabled = not options
        relationships = view.relationships
        self._relationship_previous_offset = (
            max(relationships.page_offset - relationships.page_size, 0)
            if relationships is not None and relationships.page_offset > 0
            else None
        )
        self._relationship_next_offset = (
            relationships.page_offset + relationships.page_size
            if relationships is not None and relationships.has_more
            else None
        )
        self.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button).disabled = (
            self._relationship_previous_offset is None
        )
        self.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button).disabled = (
            self._relationship_next_offset is None
        )
        if relationships is None:
            page_text = "Page unavailable."
        else:
            page = relationships.page_offset // relationships.page_size + 1
            start = relationships.page_offset + 1
            end = relationships.page_offset + relationships.page_size
            page_text = f"Page {page} · {start} to {end}"
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIP_PAGE}", Static).update(page_text)
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).update(
            _relationships_text(view.task_id, view.status_reason, view.relationships)
        )
        self._render_relationship_visibility()

    def _render_relationship_visibility(self) -> None:
        expanded = self._relationships_expanded
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).display = expanded
        self.query_one(f"#{TASK_DETAIL_RELATED_TOOLS}", Vertical).display = expanded
        toggle = self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS_BUTTON}", Button)
        toggle.label = (
            "Collapse family and relationships" if expanded else "Expand family and relationships"
        )

    def _render_ownership(self, view: TaskDetailView) -> None:
        for role, button_id in (
            (Role.LEADER, TASK_DETAIL_HAND_BACK_LEADER_BUTTON),
            (Role.IMPLEMENTER, TASK_DETAIL_HAND_BACK_IMPLEMENTER_BUTTON),
        ):
            self.query_one(f"#{button_id}", Button).disabled = role.value not in view.ownership
        ownership_lines: list[str] = []
        if view.ownership:
            ownership_lines.append(
                f"Operator owns · {', '.join(role.title() for role in view.ownership)}"
            )
        if view.holds:
            ownership_lines.append(f"Remaining holds · {', '.join(view.holds)}")
        if view.ownership and Hold.PAUSED.value in view.holds:
            ownership_lines.append(
                "Hand back alone will not clear the pause; Resume is a separate action."
            )
        elif Hold.PAUSED.value in view.holds:
            ownership_lines.append("Use Resume to clear the pause.")
        self.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static).update("\n".join(ownership_lines))


class TaskDetailScreen(ModalScreen[None]):
    """Show a selected task's shared detail over the Home browser."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        (TASK_DETAIL_ESCAPE, "close", "Back"),
        ("ctrl+p", "open_palette", "Palette"),
    ]
    DEFAULT_CSS = """
    TaskDetailScreen { width: 100%; height: 100%; background: $background; }
    #task-detail-panel { height: 100%; }
    #task-detail-panel #task-detail-body { height: 1fr; min-height: 0; max-height: 1fr; }
    """

    def __init__(self, context: WidgetContext, task_id: str) -> None:
        super().__init__()
        self._widget_context = context
        self._task_id = task_id

    def compose(self) -> ComposeResult:
        yield TaskDetailPanel(
            self._widget_context,
            self._task_id,
            fixed=False,
            close_detail=self._close,
        )

    async def _close(self) -> None:
        self.dismiss()

    async def action_close(self) -> None:
        self.dismiss()

    async def action_open_palette(self) -> None:
        await self.query_one("#task-detail-panel", TaskDetailPanel).open_palette()


type CloseDetail = Callable[[], Awaitable[None]]


def _report_text(label: str, report: TaskReportView | None, missing: str) -> str:
    if report is None:
        return missing
    lines = [
        f"{label} · {report.kind} · {report.role} · {report.provenance} · {report.turn}",
        report.summary,
    ]
    if report.decision is not None:
        lines.append(f"Decision · {report.decision}")
    if report.question:
        lines.append(f"Question · {report.question}")
    if report.issues:
        lines.append("Issues\n" + "\n".join(f"- {item}" for item in report.issues))
    if report.risks:
        lines.append("Risks\n" + "\n".join(f"- {item}" for item in report.risks))
    if report.tests:
        lines.append(
            "Agent-reported tests\n"
            + "\n".join(
                f"- {item.name} · {item.result} · {item.provenance}"
                + (f" · {item.details}" if item.details else "")
                for item in report.tests
            )
        )
    return "\n".join(lines)


def _related_references(view: TaskDetailView) -> tuple[TaskReferenceView, ...]:
    references: list[TaskReferenceView] = []
    if view.blocker is not None:
        references.extend(view.blocker.related_tasks)
    relationships = view.relationships
    if relationships is not None:
        references.extend(
            (
                relationships.family_parent,
                relationships.family_root,
                *relationships.family_children,
                *relationships.dependencies,
                *relationships.blocking_dependencies,
                *relationships.dependents,
                *relationships.replacements,
            )
        )
    seen: set[str] = set()
    unique: list[TaskReferenceView] = []
    for reference in references:
        if reference.task_id not in seen:
            seen.add(reference.task_id)
            unique.append(reference)
    return tuple(unique)


def _relationships_text(
    task_id: str,
    status_reason: str | None,
    relationships: TaskRelationshipsView | None,
) -> str:
    if relationships is None:
        return "Relationships\nRelationship evidence is unavailable."
    lines = _family_relationship_lines(task_id, relationships)
    _append_paginated_relationships(
        lines,
        "Dependencies",
        relationships.dependencies,
        relationships.dependency_count,
        relationships.page_offset,
    )
    _append_paginated_relationships(
        lines,
        "Dependents",
        relationships.dependents,
        relationships.dependent_count,
        relationships.page_offset,
    )
    _append_paginated_relationships(
        lines,
        "Recorded replacements",
        relationships.replacements,
        relationships.replacement_count,
        relationships.page_offset,
    )
    if status_reason == StatusReason.SUPERSEDED.value and relationships.replacement_count == 0:
        lines.append("No replacement task IDs are present in the recorded scope-change event.")
    if relationships.has_more:
        lines.append("More recorded relationships are available from the next page.")
    return "Relationships\n" + "\n".join(lines)


def _family_relationship_lines(task_id: str, relationships: TaskRelationshipsView) -> list[str]:
    family_parent = relationships.family_parent
    family_root = relationships.family_root
    parent_label = "Root" if family_parent.task_id == task_id else "Parent"
    lines = [
        f"{parent_label} · {family_parent.task_id} {family_parent.title}",
        f"Family root · {family_root.task_id} {family_root.title}",
        f"Children of {task_id}",
    ]
    if relationships.family_children:
        lines.extend(
            f"  ↳ {child.task_id} · {child.title} · {child.status}"
            f"{' · selected' if child.task_id == task_id else ''}"
            for child in relationships.family_children
        )
    else:
        lines.append("  No child tasks are recorded under this parent.")
    if (
        relationships.page_offset + len(relationships.family_children)
        < relationships.family_child_count
    ):
        lines.append(
            f"Showing {relationships.page_offset + 1} to "
            f"{relationships.page_offset + len(relationships.family_children)} of "
            f"{relationships.family_child_count} children on this page."
        )
    outcomes = " · ".join(f"{item.outcome} {item.count}" for item in relationships.family_outcomes)
    root = relationships.family_root
    lines.append(f"Family-wide outcomes · root {root.task_id} · {outcomes}")
    unfinished_family = any(
        item.count > 0
        and item.outcome
        in {
            Status.QUEUED.value,
            Status.RUNNING.value,
            Status.INTEGRATING.value,
            Status.ESCALATED.value,
        }
        for item in relationships.family_outcomes
    )
    if root.mode == Mode.PLAN.value and root.status == Status.DONE.value and unfinished_family:
        lines.append(
            "The plan parent integrated; unfinished child tasks remain part of the family."
        )
    return lines


def _append_paginated_relationships(
    lines: list[str],
    label: str,
    references: tuple[TaskReferenceView, ...],
    count: int,
    offset: int,
) -> None:
    _append_relationship_group(lines, label, references)
    if offset + len(references) < count:
        noun = label.lower()
        lines.append(f"Showing {offset + 1} to {offset + len(references)} of {count} {noun}.")


def _append_relationship_group(
    lines: list[str],
    label: str,
    references: tuple[TaskReferenceView, ...],
) -> None:
    if references:
        lines.append(
            f"{label} · "
            + "; ".join(f"{item.task_id} {item.title} ({item.status})" for item in references)
        )


def _evidence_text(view: TaskDetailView) -> str:
    sections = ["Recorded evidence"]
    if view.signals:
        sections.extend(
            f"{signal.role} agent signals\n" + "\n".join(signal.lines) for signal in view.signals
        )
    else:
        sections.append(NO_EVIDENCE)
    if view.events:
        sections.append(
            "Recent engine events\n"
            + "\n".join(
                f"{event.timestamp} · {event.kind} · {event.message}" for event in view.events
            )
        )
    if view.evidence_errors:
        sections.append(
            "Evidence unavailable\n" + "\n".join(f"- {item}" for item in view.evidence_errors)
        )
    return "\n\n".join(sections)


def _timeline_option(entry: TaskTimelineEntryView) -> str:
    source = (
        "Engine event" if entry.source == TaskTimelineSource.ENGINE else "Normalized agent signal"
    )
    timestamp = entry.timestamp or "Timestamp missing"
    context = " · ".join(
        item for item in (entry.role, entry.turn, entry.summary) if item is not None
    )
    return f"{timestamp} · {source} · {context}"


def _workspace_text(view: TaskDetailView) -> str:
    lines = ["Workspace and existing targets"]
    lines.append(f"Branch · {view.branch or NO_VALUE}")
    lines.append(f"Recorded worktree path · {view.worktree_path}")
    lines.append(f"Recorded task window ID · {view.window_id or NO_VALUE}")
    lines.extend(
        f"{pane.role.title()} pane ID · {pane.recorded_target or NO_VALUE}"
        for pane in view.role_panes
    )
    return "\n".join(lines)


def _sessions_text(view: TaskDetailView) -> Group:
    lines: list[RenderableType] = []
    if view.usage is None:
        lines.append(Text("Task usage coverage unavailable", style="bold"))
    else:
        lines.append(ProjectUsageMetric(view.usage, f"Task usage · {view.task_id}").render())
    if not view.sessions:
        lines.append(Text("No agent sessions are recorded."))
    for session in view.sessions:
        context = _session_context_text(
            session.context_used_pct,
            session.status,
            usage_reported=session.usage_reported,
        )
        lines.extend(
            (
                AgentUsageRow(session).render(),
                Text(f"Session ID · {session.session_id or NO_VALUE}"),
                Text(f"Billing declaration · {session.billing_mode} · {session.billing_source}"),
                Text(f"{context}"),
            )
        )
    return Group(*lines)


def _session_context_text(
    context_used_pct: float | None,
    status: str,
    *,
    usage_reported: bool | None,
) -> str:
    if usage_reported is False or context_used_pct is None:
        return f"Context {NO_VALUE} · no reliable context provenance"
    age_label = "last reported context"
    if usage_reported is None:
        age_label = "legacy last reported context"
    if status == CLOSED_RUN_STATUS:
        age_label = f"{age_label} for closed session"
    return f"{age_label} · {context_used_pct:.0f}%"
