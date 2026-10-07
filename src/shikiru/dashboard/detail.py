"""Shared task detail presentation for the workspace and fixed task view."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from shikiru.agents.contracts import COST_BASIS_API_EQUIVALENT, COST_BASIS_BILLED
from shikiru.dashboard.contract import (
    PaletteRequest,
    TaskTimelineCategory,
    TaskTimelineSource,
)
from shikiru.dashboard.interaction import native_editor_has_focus
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Hold, Mode, Role, Status, StatusReason
from shikiru.errors import ShikiruError
from shikiru.ui.compact import (
    AgentUsageDetails,
    AgentUsageRow,
    ProjectAgentUsageDetails,
    ProjectUsageDetails,
    StageActivity,
    TaskUsageMetric,
    task_signal_text,
)
from shikiru.ui.glyphs import GLYPH_CURRENT_STAGE, GLYPH_STALLED
from shikiru.ui.screen import widget_is_active
from shikiru.ui.strip import PanelState
from shikiru.ui.tokens import (
    ALERT_COLOR,
    ENGINE_COLOR,
    HOLD_COLOR,
    IDLE_COLOR,
    ROLE_COLORS,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import BindingType
    from textual.events import MouseScrollUp
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.dashboard.app import DashboardApp
    from shikiru.dashboard.contract import (
        AgentUsageView,
        TaskDetailView,
        TaskModelHistoryView,
        TaskReferenceView,
        TaskRelationshipsView,
        TaskReportView,
        TaskSessionView,
        TaskTimelineEntryView,
        TaskTimelinePageView,
        WidgetContext,
    )
    from shikiru.ui.strip import TaskStripData

TASK_DETAIL_WORKER_GROUP = "task-detail-updates"
TASK_DETAIL_ESCAPE = "escape"
SESSION_STATE_CURRENT = "Current"
SESSION_STATE_USED = "Used"
TASK_DETAIL_WINDOW_BUTTON = "task-detail-window"
TASK_DETAIL_LEADER_BUTTON = "task-detail-leader"
TASK_DETAIL_IMPLEMENTER_BUTTON = "task-detail-implementer"
TASK_DETAIL_IDENTITY = "task-detail-identity"
TASK_DETAIL_OWNERSHIP = "task-detail-ownership"
TASK_DETAIL_SITUATION = "task-detail-situation"
TASK_DETAIL_PROGRESS = "task-detail-progress"
TASK_DETAIL_SIGNAL = "task-detail-signal"
TASK_DETAIL_SUMMARY_USAGE = "task-detail-summary-usage"
TASK_DETAIL_USAGE_INLINE_BUTTON = "task-detail-usage-inline"
TASK_DETAIL_EVIDENCE_INLINE_BUTTON = "task-detail-evidence-inline"
TASK_DETAIL_POPUP_FRAME = "task-detail-popup-frame"
TASK_DETAIL_POPUP_HEADER = "task-detail-popup-header"
TASK_DETAIL_POPUP_TITLE = "task-detail-popup-title"
TASK_DETAIL_POPUP_CLOSE = "task-detail-popup-close"
TASK_DETAIL_POPUP_CLOSE_LABEL = "Esc \u00d7"
TASK_DETAIL_POPUP_CLOSE_CLASS = "popup-close-control"
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
TASK_DETAIL_CONTEXT = "task-detail-context"
TASK_DETAIL_CONTEXT_INFO = "task-detail-context-info"
TASK_DETAIL_HINTS = "task-detail-hints"
TASK_DETAIL_VIEWS_BUTTON = "task-detail-views"
TASK_DETAIL_VIEWS_OPEN_BUTTON = "task-detail-views-open"
TASK_DETAIL_VIEWS_CLOSE_BUTTON = "task-detail-views-close"
TASK_DETAIL_VIEWS_CONTENT = "task-detail-views-content"
TASK_DETAIL_VIEWS_TINY_MAX_ROWS = 8
TASK_DETAIL_VIEWS_KEY = "v"
TASK_DETAIL_VIEWS_ACTION = "open_contextual_views"
TASK_USAGE_KEY = "u"
TASK_DETAIL_HELP_KEY = "question_mark"
TASK_DETAIL_WORKSPACE_TOOLS = "task-detail-workspace-tools"
TASK_DETAIL_BLOCKER = "task-detail-blocker"
TASK_DETAIL_RELATIONSHIPS = "task-detail-relationships"
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
TASK_DETAIL_TIMELINE_FILTERS = "task-detail-timeline-filters"
TASK_DETAIL_TIMELINE_NAVIGATION = "task-detail-timeline-navigation"
TASK_DETAIL_TIMELINE_NEWER = "task-detail-timeline-newer"
TASK_DETAIL_TIMELINE_OLDER = "task-detail-timeline-older"
TASK_DETAIL_TIMELINE_FOLLOW = "task-detail-timeline-follow"
TASK_DETAIL_TIMELINE_STATUS = "task-detail-timeline-status"
TASK_DETAIL_TIMELINE_TITLE = "task-detail-timeline-title"
TASK_DETAIL_TIMELINE_ENTRY = "task-detail-timeline-entry"
TASK_DETAIL_TIMELINE_SELECTED = "task-detail-timeline-selected"
TASK_VIEW_SUMMARY = "task-summary"
TASK_VIEW_CONTEXT = "task-context"
TASK_VIEW_ACTIONS = "task-actions"
TASK_VIEW_HELP = "task-help"
TASK_VIEW_USAGE = "task-usage"
TASK_VIEW_REPORTS = "task-reports"
TASK_VIEW_REQUEST = "task-request"
TASK_VIEW_EVIDENCE = "task-evidence"
TASK_VIEW_RELATIONSHIPS = "task-relationships"
TASK_VIEW_FACTS = "task-facts"
TASK_VIEW_WORKSPACE = "task-workspace"
TASK_VIEW_CHOICES = (
    ("Usage details", TASK_VIEW_USAGE),
    ("Task details", TASK_VIEW_CONTEXT),
    ("Reports and decisions", TASK_VIEW_REPORTS),
    ("Request and criteria", TASK_VIEW_REQUEST),
    ("Evidence timeline and signals", TASK_VIEW_EVIDENCE),
    ("Blockers and relationships", TASK_VIEW_RELATIONSHIPS),
    ("Engine facts", TASK_VIEW_FACTS),
    ("Workspace targets", TASK_VIEW_WORKSPACE),
    ("Operator actions", TASK_VIEW_ACTIONS),
    ("Help", TASK_VIEW_HELP),
)
TASK_VIEW_WIDGETS = {
    TASK_VIEW_SUMMARY: (
        TASK_DETAIL_PROGRESS,
        TASK_DETAIL_SITUATION,
        TASK_DETAIL_SIGNAL,
        TASK_DETAIL_SUMMARY_USAGE,
        TASK_DETAIL_SESSIONS,
        "task-detail-evidence-link",
    ),
    TASK_VIEW_USAGE: (TASK_DETAIL_SESSIONS,),
    TASK_VIEW_CONTEXT: (TASK_DETAIL_CONTEXT_INFO,),
    TASK_VIEW_REPORTS: (TASK_DETAIL_REPORT, TASK_DETAIL_VERDICT),
    TASK_VIEW_REQUEST: (TASK_DETAIL_REQUEST, TASK_DETAIL_CRITERIA),
    TASK_VIEW_EVIDENCE: (
        TASK_DETAIL_EVIDENCE,
        TASK_DETAIL_TIMELINE_TITLE,
        TASK_DETAIL_TIMELINE_FILTERS,
        TASK_DETAIL_TIMELINE_NAVIGATION,
        TASK_DETAIL_TIMELINE_STATUS,
        TASK_DETAIL_TIMELINE_ENTRY,
        TASK_DETAIL_TIMELINE_SELECTED,
    ),
    TASK_VIEW_RELATIONSHIPS: (
        TASK_DETAIL_BLOCKER,
        TASK_DETAIL_RELATIONSHIPS,
        TASK_DETAIL_RELATED_TOOLS,
    ),
    TASK_VIEW_FACTS: (TASK_DETAIL_FACTS,),
    TASK_VIEW_WORKSPACE: (TASK_DETAIL_WORKSPACE, TASK_DETAIL_WORKSPACE_TOOLS),
}
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
COST_VALUE_FORMAT = ".2f"
AGE_NOW = "now"
TERMINAL_TASK_STATES = {
    Status.ESCALATED.value: "! Escalated",
    Status.DONE.value: "✓ Done",
    Status.CANCELLED.value: "○ Cancelled",
    Status.QUEUED.value: "· Queued",
}
ACTIVE_STAGE_LABELS = {
    "triage": "Triaging",
    "plan": "Planning",
    "implement": "Implementing",
    "consult": "Consulting",
    "verify": "Verifying",
    "integrate": "Integrating",
}


class TaskDetailBody(VerticalScroll):
    can_focus = True

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

    BINDINGS: ClassVar[list[BindingType]] = [
        (TASK_DETAIL_VIEWS_KEY, "open_contextual_views", "Views")
    ]

    DEFAULT_CSS = """
    #task-detail-panel { width: 100%; height: auto; }
    #task-detail-identity { height: auto; padding: 0; color: $steel-blue; }
    #task-detail-context-info { height: auto; color: $primary-text; }
    #task-detail-ownership { height: auto; padding: 0; color: $amber; }
    #task-detail-feedback { height: auto; min-height: 0; color: $secondary-text; }
    #task-detail-body { height: auto; max-height: 32; padding: 0 1; }
    #task-detail-summary-usage {
        width: 100%; height: auto; margin-top: 1; padding-top: 0;
        border-top: solid $neutral-border;
    }
    #task-detail-summary-usage Static { width: 1fr; height: auto; }
    #task-detail-summary-usage Button,
    #task-detail-evidence-inline,
    #task-detail-actions-inline {
        width: auto; min-width: 1; height: 1; margin: 0; padding: 0;
        border: none; background: $background; color: $steel-blue;
        content-align: left middle; text-align: left; text-style: none;
    }
    #task-detail-summary-usage Button:focus,
    #task-detail-evidence-inline:focus,
    #task-detail-actions-inline:focus { text-style: reverse; }
    #task-detail-evidence-inline:hover,
    #task-detail-actions-inline:hover { color: $primary-text; }
    #task-detail-evidence-link {
        width: 100%; height: auto; min-height: 2; margin-top: 1;
        border-top: solid $neutral-border;
    }
    #task-detail-body.compact-summary #task-detail-identity,
    #task-detail-body.compact-summary #task-detail-ownership,
    #task-detail-body.compact-summary #task-detail-situation,
    #task-detail-body.compact-summary #task-detail-progress,
    #task-detail-body.compact-summary #task-detail-signal,
    #task-detail-body.compact-summary #task-detail-sessions { padding: 0; }
    #task-detail-body.compact-summary .task-detail-section { padding: 0; }
    #task-detail-timeline-filters, #task-detail-timeline-navigation,
    #task-detail-related-tools, #task-detail-workspace-tools { width: 100%; height: auto; }
    #task-detail-timeline-filters HorizontalScroll,
    #task-detail-timeline-navigation HorizontalScroll,
    #task-detail-related-tools HorizontalScroll,
    #task-detail-workspace-tools HorizontalScroll { width: 100%; }
    #task-detail-related-tools Horizontal,
    #task-detail-workspace-tools Horizontal { width: 100%; }
    .task-detail-section {
        height: auto; padding: 1 0; color: $primary-text;
    }
    """

    def __init__(
        self,
        context: WidgetContext,
        task_id: str | None,
        *,
        fixed: bool,
        embedded: bool = False,
        close_detail: CloseDetail | None = None,
        initial_view: str = TASK_VIEW_SUMMARY,
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
        self._remove_motion_callback: Callable[[], None] | None = None
        self._timeline_request_generation = 0
        self._body_focus_generation = 0
        self._views_return_state: tuple[float, Widget | None] | None = None
        self._rendered_task_id: str | None = None
        self._active_view = initial_view
        self._view: TaskDetailView | None = None

    def compose(self) -> ComposeResult:
        with TaskDetailBody(self._pause_timeline_follow):
            yield Static("Loading task detail…", id=TASK_DETAIL_IDENTITY, markup=False)
            yield Static("", id=TASK_DETAIL_OWNERSHIP, markup=False)
            yield Static("", id=TASK_DETAIL_FEEDBACK, markup=False)
            yield Static("", id=TASK_DETAIL_PROGRESS, classes="task-detail-section")
            yield Static("", id=TASK_DETAIL_SITUATION, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_SIGNAL, classes="task-detail-section", markup=False)
            with Horizontal(id=TASK_DETAIL_SUMMARY_USAGE):
                yield Static("", id="task-detail-task-usage", markup=False)
                yield Button("u Details", id=TASK_DETAIL_USAGE_INLINE_BUTTON)
            yield Static("", id=TASK_DETAIL_SESSIONS, classes="task-detail-section")
            with Horizontal(id="task-detail-evidence-link"):
                yield Button("e Evidence", id=TASK_DETAIL_EVIDENCE_INLINE_BUTTON)
            yield Static(
                "", id=TASK_DETAIL_CONTEXT_INFO, classes="task-detail-section", markup=False
            )
            yield Static("", id=TASK_DETAIL_BLOCKER, classes="task-detail-section", markup=False)
            yield Static(
                "", id=TASK_DETAIL_RELATIONSHIPS, classes="task-detail-section", markup=False
            )
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
            yield Static(
                "Evidence timeline",
                id=TASK_DETAIL_TIMELINE_TITLE,
                classes="task-detail-section",
                markup=False,
            )
            with HorizontalScroll(id=TASK_DETAIL_TIMELINE_FILTERS):
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
            with HorizontalScroll(id=TASK_DETAIL_TIMELINE_NAVIGATION):
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
            yield Static("", id=TASK_DETAIL_REQUEST, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_CRITERIA, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_REPORT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_VERDICT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_EVIDENCE, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_FACTS, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_WORKSPACE, classes="task-detail-section", markup=False)
            with HorizontalScroll(id=TASK_DETAIL_WORKSPACE_TOOLS):
                yield Button("Task window", id=TASK_DETAIL_WINDOW_BUTTON)
                yield Button("Leader pane", id=TASK_DETAIL_LEADER_BUTTON)
                yield Button("Implementer pane", id=TASK_DETAIL_IMPLEMENTER_BUTTON)

    async def on_mount(self) -> None:
        self.disabled = self._task_id is None
        self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody).add_class("compact-summary")
        self._apply_view_visibility(blocker_visible=False)
        identity = self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        identity.update(
            _task_identity_text(f"{self._task_id} · loading selected task")
            if self._task_id is not None
            else "Select a task to inspect it."
        )
        self._set_feedback(None)
        self._refresh_worker = self.run_worker(self._refresh(), group=TASK_DETAIL_WORKER_GROUP)
        self._change_worker = self.run_worker(
            self._widget_context.on_change(self._refresh),
            group=TASK_DETAIL_WORKER_GROUP,
        )
        self._timeline_worker = self.run_worker(
            self._watch_timeline(), group=TASK_DETAIL_WORKER_GROUP
        )
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_activity)

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
            if parent.id == TASK_DETAIL_CONTEXT:
                self.query_one(f"#{TASK_DETAIL_CONTEXT}", HorizontalScroll).scroll_to_widget(
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
            _task_identity_text(f"{task_id} · loading selected task")
            if task_id is not None
            else "Select a task to inspect it."
        )
        self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody).scroll_to(y=0, animate=False)
        if task_id is None:
            return
        self._queue_refresh()

    def _reset_task_identity_state(self) -> None:
        self._relationship_offset = 0
        self._active_view = TASK_VIEW_SUMMARY
        self._view = None
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
            TASK_DETAIL_SIGNAL,
            TASK_DETAIL_REQUEST,
            TASK_DETAIL_CRITERIA,
            TASK_DETAIL_REPORT,
            TASK_DETAIL_VERDICT,
            TASK_DETAIL_EVIDENCE,
            TASK_DETAIL_FACTS,
            TASK_DETAIL_WORKSPACE,
            TASK_DETAIL_SESSIONS,
            TASK_DETAIL_TIMELINE_TITLE,
            TASK_DETAIL_TIMELINE_STATUS,
            TASK_DETAIL_TIMELINE_SELECTED,
        ):
            self.query_one(f"#{section_id}", Static).update("")
        self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static).display = False
        self._set_feedback(None)
        self.query_one("#task-detail-task-usage", Static).update("")
        related_select = cast("Select[str]", self.query_one(f"#{TASK_DETAIL_RELATED_TASK}", Select))
        related_select.value = Select.BLANK
        related_select.set_options(())
        self.query_one(f"#{TASK_DETAIL_OPEN_RELATED}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_PREVIOUS_RELATIONSHIPS}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_MORE_RELATIONSHIPS}", Button).disabled = True
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIP_PAGE}", Static).update("Page 1")
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).update("")
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
        self._apply_view_visibility(blocker_visible=False)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == TASK_DETAIL_USAGE_INLINE_BUTTON:
            if self._task_id is not None and self._widget_context.open_task_detail_view is not None:
                await self._widget_context.open_task_detail_view(self._task_id, TASK_VIEW_USAGE)
            else:
                self.open_usage_details()
            return
        if event.button.id == TASK_DETAIL_EVIDENCE_INLINE_BUTTON:
            if self._task_id is not None and self._widget_context.open_task_detail_view is not None:
                await self._widget_context.open_task_detail_view(self._task_id, TASK_VIEW_EVIDENCE)
            else:
                self.open_evidence()
            return
        handlers = {
            TASK_DETAIL_WINDOW_BUTTON: self._navigate_task_window,
            TASK_DETAIL_LEADER_BUTTON: lambda: self._navigate_role_pane(Role.LEADER),
            TASK_DETAIL_IMPLEMENTER_BUTTON: lambda: self._navigate_role_pane(Role.IMPLEMENTER),
            TASK_DETAIL_VIEWS_BUTTON: self.open_contextual_views,
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
            if self._task_id is None or not self._screen_is_active():
                continue
            if self._active_view == TASK_VIEW_USAGE:
                await self._refresh_usage_observations()
            elif self._active_view == TASK_VIEW_EVIDENCE and self._timeline_following:
                await self._refresh_timeline()
            elif self._active_view == TASK_VIEW_SUMMARY:
                await self._refresh_activity()

    async def _refresh_activity(self) -> None:
        if (
            self._task_id is None
            or self._view is None
            or self._view.task_id != self._task_id
            or self._active_view != TASK_VIEW_SUMMARY
            or not self._screen_is_active()
        ):
            return
        strip = next(
            (item for item in self._widget_context.views.strips() if item.task_id == self._task_id),
            None,
        )
        if strip is None:
            return
        activity = self._activity_view(self._view, strip)
        self._render_activity(activity)

    def _activity_view(self, view: TaskDetailView, strip: TaskStripData) -> TaskDetailView:
        signal_is_newer = (
            strip.last_signal is not None
            and strip.last_signal_at is not None
            and view.latest_signal_at is not None
            and strip.last_signal_at > view.latest_signal_at
        )
        signal_has_no_recorded_predecessor = (
            strip.last_signal is not None
            and strip.last_signal_at is not None
            and view.latest_signal is None
            and view.latest_signal_error is None
        )
        signal_values = (
            {
                "latest_signal": strip.last_signal,
                "latest_signal_identity": strip.last_signal_identity,
                "latest_signal_at": strip.last_signal_at,
                "latest_signal_role": strip.last_signal_role,
                "latest_signal_turn": strip.last_signal_turn,
                "latest_signal_uncertain": view.latest_signal_uncertain,
                "latest_signal_error": view.latest_signal_error,
                "latest_signal_incomplete": False,
            }
            if signal_is_newer or signal_has_no_recorded_predecessor
            else {}
        )
        return replace(
            view,
            progress=strip,
            signal_observed=any(panel.spinner_frame for panel in strip.panels),
            actual_stage_started_at=strip.actual_step_started_at,
            **signal_values,
        )

    def _render_activity(self, view: TaskDetailView) -> None:
        now = self._widget_context.clock.now()
        self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
            f"{view.task_id} · {view.title}\n{_task_state_line(view)}"
        )
        self.query_one(f"#{TASK_DETAIL_PROGRESS}", Static).update(StageActivity(view).render(now))
        self.query_one(f"#{TASK_DETAIL_SIGNAL}", Static).update(task_signal_text(view, now))

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
        if not widget_is_active(self):
            return False
        app = cast("App[None]", self.app)
        return app.is_running

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

    async def _back(self) -> None:
        if self._active_view != TASK_VIEW_SUMMARY:
            self.close_contextual_view()
            return
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
        role = (
            Role(self._view.acting_role)
            if self._view is not None and self._view.task_id == self._task_id
            else None
        )
        await self._open_palette(role)

    async def open_help(self) -> None:
        """Open help scoped to the current task and detail view."""
        if self._task_id is None:
            return
        await cast("DashboardApp", self.app).open_task_detail_help(
            self._task_id,
        )

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def active_view(self) -> str:
        return self._active_view

    async def open_contextual_views(self) -> None:
        if self._task_id is None:
            return
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        self._views_return_state = (body.scroll_target_y, self.screen.focused)
        app = cast("App[None]", self.app)
        app.push_screen(
            TaskDetailViewsScreen(
                self._task_id,
                self._widget_context.task_contextual_views,
            ),
            callback=self._show_selected_view,
        )

    async def action_open_contextual_views(self) -> None:
        await self.open_contextual_views()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        if action == TASK_DETAIL_VIEWS_ACTION and native_editor_has_focus(
            cast("App[None]", self.app).focused
        ):
            return None
        return True

    def open_usage_details(self) -> None:
        self._show_task_view(TASK_VIEW_USAGE)

    def open_evidence(self) -> None:
        self._show_task_view(TASK_VIEW_EVIDENCE)

    def open_view(self, view_id: str) -> None:
        self._show_task_view(view_id)

    def close_contextual_view(self) -> None:
        if self._active_view != TASK_VIEW_SUMMARY:
            self._show_task_view(TASK_VIEW_SUMMARY)

    async def return_from_detail(self) -> None:
        await self._back()

    def _show_selected_view(self, selected: str | None) -> None:
        if selected is None:
            self._restore_contextual_position()
            return
        self._views_return_state = None
        if selected in TASK_VIEW_WIDGETS:
            if isinstance(self.screen, TaskDetailScreen):
                self._queue_refresh()
                self._show_task_view(selected)
            elif (
                self._task_id is not None and self._widget_context.open_task_detail_view is not None
            ):
                self.run_worker(
                    self._widget_context.open_task_detail_view(self._task_id, selected),
                    group=TASK_DETAIL_WORKER_GROUP,
                )
            else:
                self._queue_refresh()
                self._show_task_view(selected)
        elif selected == TASK_VIEW_ACTIONS:
            self.run_worker(self.open_palette(), group=TASK_DETAIL_WORKER_GROUP)
        elif selected == TASK_VIEW_HELP:
            self.run_worker(self.open_help(), group=TASK_DETAIL_WORKER_GROUP)
        elif self._widget_context.open_contextual_view is not None:
            self._queue_refresh()
            self.run_worker(
                self._widget_context.open_contextual_view(selected),
                group=TASK_DETAIL_WORKER_GROUP,
            )

    def _restore_contextual_position(self) -> None:
        state = self._views_return_state
        self._views_return_state = None
        self._queue_refresh()
        if state is None:
            return
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        self.call_after_refresh(self._restore_contextual_position_after_refresh, body, *state)

    def _restore_contextual_position_after_refresh(
        self,
        body: TaskDetailBody,
        scroll_y: float,
        focused: Widget | None,
    ) -> None:
        if not self._screen_is_active():
            return
        if focused is not None and focused.is_mounted and focused.display:
            cast("App[None]", self.app).set_focus(focused)
        body.scroll_to(y=scroll_y, animate=False, immediate=True)

    def _show_task_view(self, view_id: str) -> None:
        if view_id not in TASK_VIEW_WIDGETS:
            return
        self._active_view = view_id
        if isinstance(self.screen, TaskDetailScreen):
            self.screen.set_view_heading(view_id)
        if self._view is None or not self.is_mounted:
            return
        if view_id == TASK_VIEW_RELATIONSHIPS:
            self._relationship_offset = 0
            self._queue_refresh()
        self._render_view(self._view, render_timeline=False)
        target_id = {
            TASK_VIEW_SUMMARY: TASK_DETAIL_PROGRESS,
            TASK_VIEW_USAGE: TASK_DETAIL_SESSIONS,
            TASK_VIEW_CONTEXT: TASK_DETAIL_CONTEXT_INFO,
            TASK_VIEW_REPORTS: TASK_DETAIL_REPORT,
            TASK_VIEW_REQUEST: TASK_DETAIL_REQUEST,
            TASK_VIEW_EVIDENCE: TASK_DETAIL_TIMELINE_ENTRY,
            TASK_VIEW_RELATIONSHIPS: TASK_DETAIL_RELATED_TASK,
            TASK_VIEW_FACTS: TASK_DETAIL_FACTS,
            TASK_VIEW_WORKSPACE: TASK_DETAIL_WINDOW_BUTTON,
        }[view_id]
        app = cast("App[None]", self.app)
        self.call_after_refresh(app.set_focus, self.query_one(f"#{target_id}"))

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
        self._set_feedback(result)

    def _set_feedback(self, result: str | None) -> None:
        feedback = self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static)
        feedback.update(result or "")
        feedback.display = result is not None

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
                    self._set_feedback(f"Task detail refresh failed: {error}")
                return
            if not self._screen_is_active():
                return
            if self._task_id != task_id:
                return
            if view is None:
                self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
                    f"{task_id} · task record is unavailable"
                )
                self._set_feedback("Task state could not be read.")
                return
            self._render_view(
                view,
                render_timeline=(
                    include_timeline and generation == self._timeline_request_generation
                ),
            )

    async def _refresh_usage_observations(self) -> None:
        view = self._view
        if view is None or not view.sessions:
            return
        agents = {session.agent for session in view.sessions}
        observations = self._widget_context.views.usage()
        quota_observations = tuple(
            item for item in observations if item.agent in agents and item.quotas
        )
        if not self._screen_is_active() or self._view is not view:
            return
        refreshed = replace(view, quota_observations=quota_observations)
        self._view = refreshed
        self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(
            _sessions_text(refreshed, detailed=True)
        )

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
        self._view = view
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        scroll_y = body.scroll_target_y
        focus_generation = self._body_focus_generation
        focused = self.screen.focused
        restore_scroll = self._rendered_task_id == view.task_id
        self._rendered_task_id = view.task_id
        self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(_task_identity(view))
        self._render_ownership(view)
        self.query_one(f"#{TASK_DETAIL_SITUATION}", Static).update(_task_situation(view))
        self.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static).update(_task_context_text(view))
        blocker_lines = list(view.blocker.lines) if view.blocker is not None else []
        blocker = self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static)
        blocker.update("Blocker evidence\n" + "\n".join(blocker_lines) if blocker_lines else "")
        self._render_relationships(view)
        self._render_activity(view)
        self.query_one("#task-detail-task-usage", Static).update(
            TaskUsageMetric(view.usage).render()
        )
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
        self.query_one(f"#{TASK_DETAIL_WINDOW_BUTTON}", Button).disabled = view.window_id is None
        recorded_panes = {pane.role: pane.recorded_target for pane in view.role_panes}
        self.query_one(f"#{TASK_DETAIL_LEADER_BUTTON}", Button).disabled = (
            recorded_panes.get(Role.LEADER.value) is None
        )
        self.query_one(f"#{TASK_DETAIL_IMPLEMENTER_BUTTON}", Button).disabled = (
            recorded_panes.get(Role.IMPLEMENTER.value) is None
        )
        self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(
            _sessions_text(view, detailed=self._active_view == TASK_VIEW_USAGE)
        )
        self._apply_view_visibility(blocker_visible=bool(blocker_lines))
        if restore_scroll:
            body.call_after_refresh(
                self._restore_body_scroll,
                body,
                scroll_y,
                focus_generation,
                focused,
            )

    def _apply_view_visibility(self, *, blocker_visible: bool) -> None:
        detail_widgets = {
            widget_id for widgets in TASK_VIEW_WIDGETS.values() for widget_id in widgets
        } | {
            TASK_DETAIL_TIMELINE_FILTERS,
            TASK_DETAIL_TIMELINE_NAVIGATION,
            TASK_DETAIL_TIMELINE_STATUS,
            TASK_DETAIL_TIMELINE_ENTRY,
            TASK_DETAIL_TIMELINE_SELECTED,
        }
        visible = set(TASK_VIEW_WIDGETS[self._active_view])
        for widget_id in detail_widgets:
            self.query_one(f"#{widget_id}").display = widget_id in visible
        self.query_one(f"#{TASK_DETAIL_USAGE_INLINE_BUTTON}").display = (
            self._active_view != TASK_VIEW_USAGE
        )
        self.query_one(f"#{TASK_DETAIL_EVIDENCE_INLINE_BUTTON}").display = (
            self._active_view != TASK_VIEW_EVIDENCE
        )
        self.query_one(f"#{TASK_DETAIL_BLOCKER}").display = (
            blocker_visible and self._active_view == TASK_VIEW_RELATIONSHIPS
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
            body.scroll_to(y=scroll_y, animate=False, immediate=True)

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

    def _render_ownership(self, view: TaskDetailView) -> None:
        ownership_lines: list[str] = []
        if view.ownership:
            owner_label = (
                "Operator owns"
                if view.status in {Status.RUNNING.value, Status.INTEGRATING.value}
                and view.actual_stage is not None
                else "Recorded operator handoff"
            )
            ownership_lines.append(
                f"{owner_label} {', '.join(role.title() for role in view.ownership)}"
            )
        if view.holds:
            ownership_lines.append(f"Holds · {', '.join(view.holds)}")
        if view.ownership and Hold.PAUSED.value in view.holds:
            ownership_lines.append(
                "Hand back alone will not clear the pause; Resume is a separate action."
            )
        elif Hold.PAUSED.value in view.holds:
            ownership_lines.append("Use Resume to clear the pause.")
        self.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static).update("\n".join(ownership_lines))


class TaskDetailViewsScreen(ModalScreen[str | None]):
    """Route task detail to its views, actions or help."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Back")]
    DEFAULT_CSS = """
    TaskDetailViewsScreen { align: center middle; background: transparent; padding: 1; }
    #task-detail-views-frame {
        width: 100%; height: auto; max-height: 90%; max-width: 64; padding: 0 1;
        border: solid $neutral-border; background: $surface; color: $primary-text;
    }
    #task-detail-views-frame Select { width: 100%; }
    #task-detail-views-content { height: auto; max-height: 1fr; min-height: 0; }
    #task-detail-views-heading { width: 100%; height: auto; }
    #task-detail-views-title {
        width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis;
    }
    #task-detail-views-close { width: auto; min-width: 5; height: 1; padding: 0; }
    #task-detail-views-open { width: 100%; height: auto; min-width: 1; padding: 0; }
    """

    def __init__(self, task_id: str, extra_choices: tuple[tuple[str, str], ...]) -> None:
        super().__init__()
        self._task_id = task_id
        self._choices = (*TASK_VIEW_CHOICES, *extra_choices)

    def compose(self) -> ComposeResult:
        with Vertical(id="task-detail-views-frame"):
            with Horizontal(id="task-detail-views-heading"):
                yield Static(
                    f"Task views · {self._task_id}", id="task-detail-views-title", markup=False
                )
                yield Button(
                    TASK_DETAIL_POPUP_CLOSE_LABEL,
                    id=TASK_DETAIL_VIEWS_CLOSE_BUTTON,
                    classes=TASK_DETAIL_POPUP_CLOSE_CLASS,
                )
            with VerticalScroll(id=TASK_DETAIL_VIEWS_CONTENT):
                yield Select(self._choices, value=TASK_VIEW_USAGE, id="task-detail-view-choice")
                yield Button("Open", id=TASK_DETAIL_VIEWS_OPEN_BUTTON)

    async def on_mount(self) -> None:
        self._apply_tiny_layout(self.size.height)
        self.set_focus(cast("Select[str]", self.query_one("#task-detail-view-choice", Select)))

    def on_resize(self, event: events.Resize) -> None:
        self._apply_tiny_layout(event.size.height)

    def _apply_tiny_layout(self, height: int) -> None:
        frame = self.query_one("#task-detail-views-frame", Vertical)
        tiny = height <= TASK_DETAIL_VIEWS_TINY_MAX_ROWS
        self.styles.padding = 0 if tiny else 1
        frame.styles.max_height = height if tiny else "90%"
        frame.styles.height = height if tiny else "auto"

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == TASK_DETAIL_VIEWS_CLOSE_BUTTON:
            self.dismiss(None)
        elif event.button.id == TASK_DETAIL_VIEWS_OPEN_BUTTON:
            selected = cast("Select[str]", self.query_one("#task-detail-view-choice", Select)).value
            self.dismiss(selected if isinstance(selected, str) else None)

    async def action_close(self) -> None:
        self.dismiss(None)


class TaskDetailScreen(ModalScreen[None]):
    """Show a selected task's shared detail over the Home browser."""

    DEFAULT_CSS = """
    TaskDetailScreen { align: center middle; background: transparent; padding: 1; }
    #task-detail-popup-frame {
        width: 100%; max-width: 64; height: auto; max-height: 90%; min-height: 1;
        padding: 0 1; border: solid $neutral-border; background: $surface;
    }
    #task-detail-popup-header { width: 100%; height: 1; }
    #task-detail-popup-title { width: 1fr; height: 1; color: $steel-blue; }
    #task-detail-popup-close { width: auto; min-width: 5; height: 1; padding: 0; }
    #task-detail-panel { width: 100%; height: 1fr; min-height: 0; }
    #task-detail-panel #task-detail-body {
        height: 1fr; min-height: 0; max-height: 1fr; background: $surface;
    }
    """

    def __init__(
        self,
        context: WidgetContext,
        task_id: str,
        *,
        initial_view: str = TASK_VIEW_SUMMARY,
        open_views_on_mount: bool = False,
    ) -> None:
        super().__init__()
        self._widget_context = context
        self._task_id = task_id
        self._initial_view = initial_view
        self._open_views_on_mount = open_views_on_mount

    def compose(self) -> ComposeResult:
        with Vertical(id=TASK_DETAIL_POPUP_FRAME):
            with Horizontal(id=TASK_DETAIL_POPUP_HEADER):
                yield Label(self._heading(), id=TASK_DETAIL_POPUP_TITLE)
                yield Button(
                    TASK_DETAIL_POPUP_CLOSE_LABEL,
                    id=TASK_DETAIL_POPUP_CLOSE,
                    classes=TASK_DETAIL_POPUP_CLOSE_CLASS,
                )
            yield TaskDetailPanel(
                self._widget_context,
                self._task_id,
                fixed=False,
                close_detail=self._close,
                initial_view=self._initial_view,
            )

    async def on_mount(self) -> None:
        panel = self.query_one("#task-detail-panel", TaskDetailPanel)
        target = (
            panel.query_one(f"#{TASK_DETAIL_USAGE_INLINE_BUTTON}", Button)
            if panel.active_view == TASK_VIEW_SUMMARY
            else panel.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        )
        self.set_focus(target)
        if self._open_views_on_mount:
            await panel.open_contextual_views()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == TASK_DETAIL_POPUP_CLOSE:
            self.dismiss()

    def _heading(self) -> str:
        return self._view_heading(self._initial_view)

    def _view_heading(self, view_id: str) -> str:
        name = {
            TASK_VIEW_SUMMARY: "Task details",
            TASK_VIEW_USAGE: "Usage",
            TASK_VIEW_EVIDENCE: "Evidence",
            TASK_VIEW_CONTEXT: "Task context",
            TASK_VIEW_REPORTS: "Reports and decisions",
            TASK_VIEW_REQUEST: "Request and criteria",
            TASK_VIEW_RELATIONSHIPS: "Blockers and relationships",
            TASK_VIEW_FACTS: "Engine facts",
            TASK_VIEW_WORKSPACE: "Workspace targets",
        }.get(view_id, "Task details")
        return f"{name} · {self._task_id}"

    def set_view_heading(self, view_id: str) -> None:
        self.query_one(f"#{TASK_DETAIL_POPUP_TITLE}", Label).update(self._view_heading(view_id))

    async def _close(self) -> None:
        self.dismiss()

    async def action_open_contextual_views(self) -> None:
        await self.query_one("#task-detail-panel", TaskDetailPanel).open_contextual_views()

    def action_open_usage_details(self) -> None:
        self.query_one("#task-detail-panel", TaskDetailPanel).open_usage_details()

    async def action_close(self) -> None:
        self.dismiss()

    async def action_open_palette(self) -> None:
        await self.query_one("#task-detail-panel", TaskDetailPanel).open_palette()

    async def action_open_help(self) -> None:
        await cast("DashboardApp", self.app).action_show_help()


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


def _task_context_text(view: TaskDetailView) -> str:
    lines = ["Task details"]
    if view.mode is not None:
        lines.append(f"Mode · {view.mode}")
    lines.append(f"Submitted · {view.submitted_age}")
    if view.requeued_age is not None:
        lines.append(f"Recorded requeued · {view.requeued_age}")
    if view.status_reason is not None:
        lines.append(f"Status reason · {view.status_reason}")
    if view.priority is not None:
        lines.append(f"Dispatch priority · {view.priority}")
    attention = view.attention
    lines.append(
        f"Project attention · {attention.decisions} decisions · {attention.faults} engine faults"
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


def _task_state_line(view: TaskDetailView) -> str:
    terminal = TERMINAL_TASK_STATES.get(view.status)
    marker = GLYPH_CURRENT_STAGE
    state = (
        ACTIVE_STAGE_LABELS.get(view.actual_stage, view.actual_stage.title())
        if view.actual_stage is not None
        else terminal.split(" ", maxsplit=1)[1]
        if terminal is not None
        else view.status.title()
    )
    labels: list[str] = []
    if view.status == Status.ESCALATED.value:
        labels.append("Decision hold")
    labels.extend(_hold_label(hold) for hold in view.holds)
    current_ownership = (
        view.ownership
        if view.status in {Status.RUNNING.value, Status.INTEGRATING.value}
        and view.actual_stage is not None
        else ()
    )
    if current_ownership:
        labels.append("Operator-owned")
    if labels:
        marker = "@" if current_ownership else "=" if view.holds else "!"
        state = " · ".join((*labels, state))
    elif terminal is not None:
        marker = terminal.split(" ", maxsplit=1)[0]
    else:
        active_panel = next(
            (
                panel
                for panel in view.progress.panels
                if panel.state in {PanelState.ACTIVE, PanelState.STALLED}
            ),
            None,
        )
        if active_panel is not None:
            if active_panel.state is PanelState.STALLED:
                marker = GLYPH_STALLED
            elif active_panel.spinner_frame:
                marker = active_panel.spinner_frame
    return f"{marker} {state}"


def _task_identity(view: TaskDetailView) -> Text:
    identity = Text(f"{view.task_id} · {view.title}", style=STEEL_BLUE)
    identity.append("\n")
    identity.append(_task_state_line(view), style=_task_state_color(view))
    return identity


def _task_identity_text(identity: str) -> Text:
    return Text(identity, style=STEEL_BLUE)


def _task_state_color(view: TaskDetailView) -> str:
    if view.status == Status.ESCALATED.value:
        color = ALERT_COLOR
    elif view.holds:
        color = HOLD_COLOR
    elif view.status == Status.DONE.value:
        color = SUCCESS_COLOR
    elif view.status in {Status.CANCELLED.value, Status.QUEUED.value}:
        color = IDLE_COLOR
    else:
        active_panel = next(
            (panel for panel in view.progress.panels if panel.state is PanelState.ACTIVE),
            None,
        )
        if active_panel is not None:
            color = (
                ROLE_COLORS[active_panel.role] if active_panel.role is not None else ENGINE_COLOR
            )
        elif any(panel.state is PanelState.STALLED for panel in view.progress.panels):
            color = HOLD_COLOR
        else:
            color = SECONDARY_TEXT_COLOR
    return color


def _hold_label(hold: str) -> str:
    if hold == Hold.PAUSED.value:
        return "Paused"
    if hold == Hold.INTERVENTION.value:
        return "Intervention hold"
    return f"{hold.replace('_', ' ').title()} hold"


def _task_situation(view: TaskDetailView) -> str:
    decision = _decision_situation(view)
    if decision is not None:
        return decision
    blocking = _blocking_situation(view)
    if blocking is not None:
        return blocking
    report = _report_situation(view)
    if report is not None:
        return report
    return _status_situation(view)


def _decision_situation(view: TaskDetailView) -> str | None:
    if view.status != Status.ESCALATED.value:
        return None
    question = next(
        (
            report.question
            for report in (view.latest_report, view.latest_verdict)
            if report is not None and report.question
        ),
        None,
    )
    if question is not None:
        return _sentence(f"Decision needed · {question}")
    return "Escalation open; no validated decision question is recorded."


def _blocking_situation(view: TaskDetailView) -> str | None:
    if view.wait_reason is not None:
        return _sentence(f"Waiting on {view.wait_reason.replace('_', ' ')}")
    if view.status_reason == StatusReason.SUPERSEDED.value:
        return "Task was superseded"
    if view.holds:
        holds = ", ".join(view.holds)
        if view.ownership:
            return _sentence(f"Operator ownership is holding {holds}")
        return _sentence(f"Task is held on {holds}")
    return None


def _report_situation(view: TaskDetailView) -> str | None:
    if view.latest_report is not None and view.latest_report.summary.strip():
        return _sentence(view.latest_report.summary)
    if view.latest_verdict is not None and view.latest_verdict.summary.strip():
        return _sentence(view.latest_verdict.summary)
    return None


def _status_situation(view: TaskDetailView) -> str:
    status_situations = {
        Status.QUEUED.value: "Queued for execution.",
        Status.RUNNING.value: "Work is active.",
        Status.INTEGRATING.value: "Integration is in progress.",
        Status.DONE.value: "Completed; no validated report is recorded.",
        Status.CANCELLED.value: "Cancelled.",
        Status.ESCALATED.value: "Decision required.",
    }
    return status_situations.get(view.status, "Task state is recorded.")


def _sentence(value: str) -> str:
    sentence = value.strip().splitlines()[0].strip()
    return sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."


def _session_role_state(view: TaskDetailView, session: TaskSessionView) -> str:
    if session.run_id is None:
        return SESSION_STATE_USED
    active_task = view.status in {Status.RUNNING.value, Status.INTEGRATING.value}
    if (
        active_task
        and view.actual_stage is not None
        and session.status == RunStatus.TAKEN_OVER.value
        and session.role in view.ownership
    ):
        return SESSION_STATE_CURRENT
    if (
        session.status == RunStatus.LIVE.value
        and session.role == view.acting_role
        and active_task
        and view.actual_stage is not None
        and not view.holds
        and view.wait_reason is None
    ):
        return SESSION_STATE_CURRENT
    return SESSION_STATE_USED


def _summary_sessions(view: TaskDetailView) -> tuple[TaskSessionView, ...]:
    current = [
        session
        for session in view.sessions
        if _session_role_state(view, session) == SESSION_STATE_CURRENT
    ]
    current = _ordered_sessions(current)
    current_run_ids = {session.run_id for session in current}
    latest_used_by_role: dict[str, TaskSessionView] = {}
    for session in _ordered_sessions(list(view.sessions)):
        if session.role not in latest_used_by_role and session.run_id not in current_run_ids:
            latest_used_by_role[session.role] = session
    used = _ordered_sessions(list(latest_used_by_role.values()))
    return (*current, *used)


def _ordered_sessions(sessions: list[TaskSessionView]) -> list[TaskSessionView]:
    return sorted(
        sessions,
        key=lambda session: (
            session.run_id if session.run_id is not None else -1,
            session.role,
            session.agent,
            session.model,
            session.session_id or "",
        ),
        reverse=True,
    )


def _sessions_text(view: TaskDetailView, *, detailed: bool) -> Group:
    lines: list[RenderableType] = []
    if detailed:
        lines.append(Text(f"Task {view.task_id} · recorded usage", style="bold"))
        if view.usage is None:
            lines.append(Text("Task usage coverage unavailable", style="bold"))
        else:
            lines.append(ProjectUsageDetails(view.usage).render())
    if not view.sessions:
        lines.append(Text("No agent sessions are recorded."))
    sessions = view.sessions if detailed else _summary_sessions(view)
    for session in sessions:
        lines.append(AgentUsageRow(session, _session_role_state(view, session)).render())
        if detailed:
            lines.extend(_session_details(session))
    if detailed:
        if view.agent_usage:
            lines.append(
                Text("Agent totals · across recorded sessions, not model totals", style="bold")
            )
            lines.extend(ProjectAgentUsageDetails(item).render() for item in view.agent_usage)
        lines.extend(_quota_details(view.quota_observations, view.sessions))
    return Group(*lines)


def _session_details(session: TaskSessionView) -> list[RenderableType]:
    context = _session_context_text(
        session.context_used_pct,
        session.status,
        usage_reported=session.usage_reported,
    )
    lines: list[RenderableType] = [
        Text("Session-wide cumulative totals · across models", style="bold"),
        AgentUsageDetails(session).render(),
        Text(f"Billing declaration · {session.billing_mode} · source {session.billing_source}"),
        Text(f"Session ID · {session.session_id or NO_VALUE}"),
        Text(context),
    ]
    if session.model_history:
        lines.append(Text("Recorded model turn observations · not model totals", style="bold"))
        lines.extend(_model_history_text(item) for item in session.model_history)
    if session.history_gaps:
        gaps = ", ".join(gap.value for gap in session.history_gaps)
        lines.append(Text(f"Model history gaps · {gaps}", style="amber"))
    return lines


def _model_history_text(history: TaskModelHistoryView) -> Text:
    turn = history.turn or NO_VALUE
    effort = f" · effort {history.effort}" if history.effort else ""
    identity = f"Turn {turn} · {history.agent}/{history.model}{effort} · {history.status}"
    if not history.identity_complete:
        identity += " · identity incomplete"
    delta = history.usage_delta
    if delta is None:
        return Text(f"{identity} · no per-turn usage recorded")
    usage = (
        f"per-turn observation · input {delta.input_tokens:,} · output {delta.output_tokens:,} · "
        f"cache read {delta.cache_read_tokens:,} · cache write {delta.cache_write_tokens:,}"
    )
    if delta.cost_usd is None:
        cost = " · no per-turn monetary amount recorded"
    elif delta.cost_basis == COST_BASIS_BILLED:
        cost = f" · reported billed ${delta.cost_usd:{COST_VALUE_FORMAT}}"
    elif delta.cost_basis == COST_BASIS_API_EQUIVALENT:
        cost = f" · API-equivalent estimate ${delta.cost_usd:{COST_VALUE_FORMAT}}"
    else:
        cost = f" · amount ${delta.cost_usd:{COST_VALUE_FORMAT}} · cost basis unknown"
    return Text(f"{identity} · {usage}{cost}")


def _quota_details(
    observations: tuple[AgentUsageView, ...],
    sessions: tuple[TaskSessionView, ...],
) -> list[RenderableType]:
    quota_lines = [
        Text(
            f"Provider-reported quota · {item.agent} · {window.name} · "
            f"{window.used_pct:g}% used · reset {window.resets_in} · "
            f"observed {window.observed_ago}"
            f"{' ago' if window.observed_ago != AGE_NOW else ''}"
        )
        for item in observations
        for window in item.quotas
    ]
    if quota_lines:
        return [
            Text("Quota scope is agent-reported and may include other projects."),
            *quota_lines,
        ]
    if sessions:
        return [
            Text("No provider quota/reset observations are recorded for these agent identities.")
        ]
    return []


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
