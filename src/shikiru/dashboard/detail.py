"""Shared task detail presentation for the workspace and fixed task view."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static

from shikiru.dashboard.briefing_view import (
    ACTION_LABELS,
    TERMINAL_STATUSES,
    Briefing,
    BriefingLayout,
    briefing_verbs,
    header_text,
)
from shikiru.dashboard.contract import (
    PaletteRequest,
    TaskTimelineCategory,
)
from shikiru.dashboard.detail_fold import DetailFold, DetailFolds
from shikiru.dashboard.detail_hint import DetailHintBar, HintTarget, hint_for
from shikiru.dashboard.details_list_view import DetailsSection, section_headline
from shikiru.dashboard.fold_bodies import (
    DecisionsBody,
    DescriptionBody,
    ResultsBody,
    SettingsBody,
    WorkspaceBody,
)
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.section_rows import RelatedRow, blocker_evidence, relation_rows
from shikiru.dashboard.timeline_filter import TimelineFilterBar
from shikiru.dashboard.timeline_section import (
    TimelineHost,
    TimelineIds,
    TimelineSection,
    screen_is_active,
)
from shikiru.dashboard.usage_body import UsageBody
from shikiru.domain.briefing import BriefingAction
from shikiru.domain.tasks import Role, Status, StatusReason
from shikiru.errors import ShikiruError
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.popup import (
    POPUP_CLOSE_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    popup_close_label,
    prepare_popup,
)
from shikiru.ui.screen import widget_is_attached
from shikiru.ui.section import BOLD_PRIMARY, grey, key_label

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.geometry import Size
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.dashboard.app import DashboardApp
    from shikiru.dashboard.contract import (
        TaskDetailView,
        WidgetContext,
    )
    from shikiru.ui.strip import TaskStripData

TASK_DETAIL_WORKER_GROUP = "task-detail-updates"
TASK_DETAIL_ESCAPE = "escape"
SESSION_STATE_CURRENT = "Current"
SESSION_STATE_USED = "Used"
TASK_DETAIL_IDENTITY = "task-detail-identity"
TASK_DETAIL_OWNERSHIP = "task-detail-ownership"
TASK_DETAIL_BRIEFING = "task-detail-briefing"
TASK_DETAIL_ACTIONS = "task-detail-actions"
TASK_DETAIL_VERBS = "task-detail-verbs"
TASK_DETAIL_ACTION_PREFIX = "task-detail-action-"
TASK_DETAIL_POPUP_FRAME = "task-detail-popup-frame"
TASK_DETAIL_POPUP_HEADER = "task-detail-popup-header"
TASK_DETAIL_POPUP_TITLE = "task-detail-popup-title"
TASK_DETAIL_POPUP_CLOSE = "task-detail-popup-close"
TASK_DETAIL_REQUEST = "task-detail-request"
TASK_DETAIL_RESULTS = "task-detail-results"
TASK_DETAIL_DECISIONS = "task-detail-decisions"
TASK_DETAIL_WORKSPACE = "task-detail-workspace"
TASK_DETAIL_SESSIONS = "task-detail-sessions"
TASK_DETAIL_FEEDBACK = "task-detail-feedback"
TASK_DETAIL_BODY = "task-detail-body"
TASK_DETAIL_FOLDS = "task-detail-folds"
FOLDS_COMPACT_CLASS = "folds-compact"
FOLDS_TINY_CLASS = "folds-tiny"
TASK_DETAIL_CONTEXT_INFO = "task-detail-context-info"
TASK_DETAIL_TINY_MAX_ROWS = 8
_TASK_DETAIL_COMPACT_MAX_COLUMNS = 48
_TASK_DETAIL_COMPACT_MAX_ROWS = 20
TASK_DETAIL_VIEWS_KEY = "v"
DETAILS_HINT_LABEL = "details"
DETAILS_HINT_TEXT = f"{TASK_DETAIL_VIEWS_KEY} {DETAILS_HINT_LABEL}"
TASK_DETAIL_DETAILS_HINT = "task-detail-details-hint"
VERB_MARGIN = 2
TASK_DETAIL_HELP_KEY = "question_mark"
TASK_DETAIL_HINT = "task-detail-hint"
TASK_WINDOW_KEY = "t"
TASK_WINDOW_ACTION = "task_window"
TASK_DETAIL_BLOCKER = "task-detail-blocker"
TASK_DETAIL_RELATIONSHIPS = "task-detail-relationships"
TASK_DETAIL_RELATED_LIST = "task-detail-related-list"
EVENTS_TIMELINE_IDS = TimelineIds.with_prefix("task-detail-timeline")
AGENT_TIMELINE_IDS = TimelineIds.with_prefix("task-detail-agent")
TASK_DETAIL_AGENT_LIST = AGENT_TIMELINE_IDS.list
TASK_DETAIL_AGENT_SELECTED = AGENT_TIMELINE_IDS.selected
TASK_DETAIL_TIMELINE_LIST = EVENTS_TIMELINE_IDS.list
TASK_DETAIL_TIMELINE_SELECTED = EVENTS_TIMELINE_IDS.selected
TASK_VIEW_SUMMARY = "task-summary"
TASK_VIEW_CONTEXT = "task-context"
TASK_VIEW_USAGE = "task-usage"
TASK_VIEW_RESULTS = "task-results"
TASK_VIEW_DECISIONS = "task-decisions"
TASK_VIEW_REQUEST = "task-request"
TASK_VIEW_EVIDENCE = "task-evidence"
TASK_VIEW_RELATIONSHIPS = "task-relationships"
TASK_VIEW_WORKSPACE = "task-workspace"
TASK_VIEW_AGENT = "task-agent"
DETAILS_SECTION_VIEWS = {
    DetailsSection.REQUEST: TASK_VIEW_REQUEST,
    DetailsSection.RESULTS: TASK_VIEW_RESULTS,
    DetailsSection.DECISIONS: TASK_VIEW_DECISIONS,
    DetailsSection.EVENTS: TASK_VIEW_EVIDENCE,
    DetailsSection.AGENT_ACTIVITY: TASK_VIEW_AGENT,
    DetailsSection.USAGE: TASK_VIEW_USAGE,
    DetailsSection.BLOCKERS: TASK_VIEW_RELATIONSHIPS,
    DetailsSection.WORKSPACE: TASK_VIEW_WORKSPACE,
    DetailsSection.SETTINGS: TASK_VIEW_CONTEXT,
}
TIMELINE_SECTIONS = {
    DetailsSection.EVENTS: (EVENTS_TIMELINE_IDS, TaskTimelineCategory.ACTIVITY, False),
    DetailsSection.AGENT_ACTIVITY: (AGENT_TIMELINE_IDS, TaskTimelineCategory.AGENT, True),
}
FOLD_STATICS = {
    TASK_VIEW_USAGE: (TASK_DETAIL_SESSIONS,),
    TASK_VIEW_CONTEXT: (TASK_DETAIL_CONTEXT_INFO,),
    TASK_VIEW_RESULTS: (TASK_DETAIL_RESULTS,),
    TASK_VIEW_DECISIONS: (TASK_DETAIL_DECISIONS,),
    TASK_VIEW_REQUEST: (TASK_DETAIL_REQUEST,),
    TASK_VIEW_RELATIONSHIPS: (TASK_DETAIL_BLOCKER, TASK_DETAIL_RELATIONSHIPS),
    TASK_VIEW_WORKSPACE: (TASK_DETAIL_WORKSPACE,),
}
FOLD_HINT_TARGETS = {
    TASK_VIEW_EVIDENCE: HintTarget.EVENTS,
    TASK_VIEW_AGENT: HintTarget.AGENT,
    TASK_VIEW_RELATIONSHIPS: HintTarget.DEPENDENCIES,
}
NO_DECISION_TO_ANSWER = "No open decision to answer for this task."
NO_RELATED_ROWS = "No related tasks are recorded."
RELATIONSHIPS_UNAVAILABLE = "Relationship evidence is unavailable."
NO_REPLACEMENT_TASKS = "No replacement task IDs are present in the recorded scope-change event."
CLOSED_RUN_STATUS = "closed"
PAGE_LABEL = "page"
COST_VALUE_FORMAT = ".2f"
AGE_NOW = "now"
COMPACT_INSPECTOR_CLASS = "compact-inspector"


class TaskDetailBody(VerticalScroll, can_focus=False):
    """Scrolls the detail to its focused child; it is never a Tab stop itself."""

    def on_resize(self, event: events.Resize) -> None:
        del event
        focused = self.screen.focused
        if focused is not None and self in focused.ancestors and not self.can_view_entire(focused):
            self.call_after_refresh(
                self.scroll_to_center,
                focused,
                animate=False,
            )


class TaskDetailHint(Static):
    """The right-aligned `v details` hint of the actions row; clicking it focuses the sections."""

    def __init__(self, hint: Text) -> None:
        super().__init__(hint, id=TASK_DETAIL_DETAILS_HINT, markup=False)

    def on_click(self) -> None:
        """Focus the section headers, like pressing the key."""
        self.query_ancestor(TaskDetailPanel).focus_folds()


class TaskDetailVerb(Button):
    """A briefing action button of the task detail, drawn without the stock line padding."""

    def on_mount(self) -> None:
        """Drop the one-cell line padding the stock button adds on each side of its label."""
        self.styles.line_pad = 0

    def get_content_width(self, container: Size, viewport: Size) -> int:
        """Return the label's own width, without the stock two cells of line padding."""
        del container, viewport
        return cell_len(str(self.label))


class TaskDetailPanel(Vertical):
    """Render shared task detail from the injected dashboard view contract."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(TASK_WINDOW_KEY, TASK_WINDOW_ACTION, "Task window", show=False)
    ]
    DEFAULT_CSS = """
    #task-detail-panel { width: 100%; height: auto; }
    #task-detail-identity { height: auto; padding: 0; color: $steel-blue; }
    #task-detail-context-info { height: auto; color: $primary-text; }
    #task-detail-ownership { height: auto; padding: 0; color: $amber; }
    #task-detail-feedback { height: auto; min-height: 0; color: $secondary-text; }
    #task-detail-body {
        height: auto; max-height: 32; padding: 0 1; scrollbar-size-vertical: $scrollbar-width;
    }
    #task-detail-briefing { height: auto; padding: 0; }
    #task-detail-actions {
        width: 100%; height: auto; border-top: solid $neutral-border;
    }
    #task-detail-verbs { width: 1fr; height: 1; }
    #task-detail-details-hint { width: auto; height: 1; }
    #task-detail-verbs Button {
        width: auto; min-width: 1; height: 1; margin: 0 2 0 0; padding: 0;
        border: none; background: $background; color: $steel-blue;
        content-align: left middle; text-align: left; text-style: none;
    }
    #task-detail-actions Button:focus { text-style: reverse; }
    #task-detail-actions Button:hover { color: $primary-text; }
    #task-detail-hint { height: 1; padding: 0 1; }
    .task-detail-section { height: auto; padding: 0; color: $primary-text; }
    TaskDetailPanel RowList { max-height: 10; overflow-y: auto; scrollbar-size-vertical: 1; }
    TaskDetailPanel.folds-compact RowList { max-height: 6; }
    TaskDetailPanel.folds-tiny RowList { max-height: 3; }
    """

    def __init__(
        self,
        context: WidgetContext,
        task_id: str | None,
        *,
        fixed: bool,
        close_detail: CloseDetail | None = None,
        initial_view: str = TASK_VIEW_SUMMARY,
        on_initial_content: Callable[[], None] | None = None,
    ) -> None:
        classes = "task-detail-fixed" if fixed else ""
        super().__init__(id="task-detail-panel", classes=classes)
        self._widget_context = context
        self._task_id = task_id
        self._fixed = fixed
        self._close_detail = close_detail
        self._refresh_lock = asyncio.Lock()
        self._refresh_worker: Worker[None] | None = None
        self._change_worker: Worker[None] | None = None
        self._relationship_offset = 0
        self._relationship_previous_offset: int | None = None
        self._relationship_next_offset: int | None = None
        self._related_rows: tuple[RelatedRow, ...] = ()
        host = TimelineHost(
            task_id=lambda: self._task_id,
            lock=self._refresh_lock,
            report=self._set_feedback,
        )
        self._timelines = {
            DETAILS_SECTION_VIEWS[section]: TimelineSection(
                context, host, ids=ids, category=category, filterable=filterable
            )
            for section, (ids, category, filterable) in TIMELINE_SECTIONS.items()
        }
        self._folds: dict[str, DetailFold] = {}
        self._initial_view = initial_view
        self._timeline_worker: Worker[None] | None = None
        self._remove_motion_callback: Callable[[], None] | None = None
        self._body_focus_generation = 0
        self._rendered_task_id: str | None = None
        self._view: TaskDetailView | None = None
        self._on_initial_content = on_initial_content
        self._initial_content_reported = False

    def compose(self) -> ComposeResult:
        with TaskDetailBody(id=TASK_DETAIL_BODY):
            yield GatedStatic("Loading task detail…", id=TASK_DETAIL_IDENTITY, markup=False)
            yield Static("", id=TASK_DETAIL_OWNERSHIP, markup=False)
            yield Static("", id=TASK_DETAIL_FEEDBACK, markup=False)
            yield GatedStatic("", id=TASK_DETAIL_BRIEFING, markup=False)
            with Horizontal(id=TASK_DETAIL_ACTIONS):
                with Horizontal(id=TASK_DETAIL_VERBS):
                    for action in BriefingAction:
                        yield TaskDetailVerb(
                            ACTION_LABELS[action], id=f"{TASK_DETAIL_ACTION_PREFIX}{action.value}"
                        )
                yield TaskDetailHint(key_label(TASK_DETAIL_VIEWS_KEY, DETAILS_HINT_LABEL))
            with DetailFolds(widget_id=TASK_DETAIL_FOLDS):
                for section in DetailsSection:
                    view_id = DETAILS_SECTION_VIEWS[section]
                    fold = DetailFold(
                        view_id,
                        section.value,
                        self._section_widgets(view_id),
                        is_open=view_id == self._initial_view,
                    )
                    self._folds[view_id] = fold
                    yield fold
        yield DetailHintBar(widget_id=TASK_DETAIL_HINT)

    def _section_widgets(self, view_id: str) -> list[Widget]:
        timeline = self._timelines.get(view_id)
        if timeline is not None:
            return [timeline]
        widgets: list[Widget] = [
            Static("", id=widget_id, classes="task-detail-section", markup=False)
            for widget_id in FOLD_STATICS.get(view_id, ())
        ]
        if view_id == TASK_VIEW_RELATIONSHIPS:
            widgets.append(RowList(widget_id=TASK_DETAIL_RELATED_LIST))
        return widgets

    async def on_mount(self) -> None:
        self.disabled = self._task_id is None
        self._folds_list().display = self._task_id is not None
        self.query_one(f"#{TASK_DETAIL_BLOCKER}").display = False
        self._sync_timelines()
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
        fold = self._fold_of(event.widget)
        if fold is not None:
            self._folds_list().make_current(fold)
        self._paint_hint()
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

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        del event
        self.call_after_refresh(self._paint_hint)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        return self._task_window_available() if action == TASK_WINDOW_ACTION else True

    async def action_task_window(self) -> None:
        await self._navigate_task_window()

    def _task_window_available(self) -> bool:
        view = self._view
        fold = self._fold_of(self.screen.focused)
        return (
            view is not None
            and fold is not None
            and fold.view_id == TASK_VIEW_WORKSPACE
            and view.status not in TERMINAL_STATUSES
            and view.window_id is not None
        )

    def _hint_target(self) -> HintTarget | None:
        focused = self.screen.focused
        fold = self._fold_of(focused)
        if fold is None:
            return None
        if focused is fold.header:
            return HintTarget.HEADER
        if isinstance(focused, TimelineFilterBar):
            return HintTarget.AGENT_FILTER
        return FOLD_HINT_TARGETS.get(fold.view_id)

    def _paint_hint(self) -> None:
        bars = self.query(DetailHintBar)
        if not widget_is_attached(self) or not bars:
            return
        target = self._hint_target()
        bars.first().show(
            None
            if target is None
            else hint_for(
                target,
                task_window=self._task_window_available(),
                keys_only=self._briefing_layout().tiny,
            )
        )

    def select_task(self, task_id: str | None) -> None:
        if task_id == self._task_id:
            return
        self._task_id = task_id
        self._reset_task_identity_state()
        self.disabled = task_id is None
        if not self.is_mounted:
            return
        self._folds_list().display = task_id is not None
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
        self._view = None
        self._relationship_previous_offset = None
        self._relationship_next_offset = None
        self._related_rows = ()
        for timeline in self._timelines.values():
            timeline.reset()
        if not self.is_mounted:
            return
        for section_id in (
            TASK_DETAIL_OWNERSHIP,
            TASK_DETAIL_BLOCKER,
            TASK_DETAIL_BRIEFING,
            TASK_DETAIL_REQUEST,
            TASK_DETAIL_RESULTS,
            TASK_DETAIL_DECISIONS,
            TASK_DETAIL_WORKSPACE,
            TASK_DETAIL_SESSIONS,
        ):
            self.query_one(f"#{section_id}", Static).update("")
        self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static).display = False
        self._set_feedback(None)
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).update("")
        self.query_one(f"#{TASK_DETAIL_RELATED_LIST}", RowList).set_rows(
            (), selected=0, empty=NO_RELATED_ROWS
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith(TASK_DETAIL_ACTION_PREFIX):
            await self._run_briefing_action(
                BriefingAction(button_id.removeprefix(TASK_DETAIL_ACTION_PREFIX))
            )

    @on(RowList.Paged, f"#{TASK_DETAIL_RELATED_LIST}")
    async def _related_paged(self, message: RowList.Paged) -> None:
        message.stop()
        await (
            self._next_relationship_page() if message.older else self._previous_relationship_page()
        )

    @on(RowList.Opened, f"#{TASK_DETAIL_RELATED_LIST}")
    async def _related_opened(self, message: RowList.Opened) -> None:
        message.stop()
        callback = self._widget_context.open_task_detail
        if callback is not None and message.index < len(self._related_rows):
            await callback(self._related_rows[message.index].task_id)

    async def _watch_timeline(self) -> None:
        while True:
            await self._widget_context.clock.sleep(self._widget_context.timeline_refresh_seconds)
            if self._task_id is None or not self._screen_is_active():
                continue
            if self._folds[TASK_VIEW_USAGE].is_open:
                await self._refresh_quota_consumption()
            await self._refresh_activity()
            for timeline in self._timelines.values():
                await timeline.refresh_when_due()

    async def _refresh_activity(self) -> None:
        if (
            self._task_id is None
            or self._view is None
            or self._view.task_id != self._task_id
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
        )
        signal_values = (
            {
                "latest_signal": strip.last_signal,
                "latest_signal_at": strip.last_signal_at,
            }
            if signal_is_newer or signal_has_no_recorded_predecessor
            else {}
        )
        return replace(
            view,
            progress=strip,
            last_words=strip.last_words if strip.last_words is not None else view.last_words,
            signal_observed=strip.last_signal_at is not None
            and any(panel.spinner_frame for panel in strip.panels),
            actual_stage_started_at=strip.actual_step_started_at,
            **signal_values,
        )

    def _render_activity(self, view: TaskDetailView) -> None:
        now = self._widget_context.clock.now()
        layout = self._briefing_layout()
        self.set_class(layout.compact, FOLDS_COMPACT_CLASS)
        self.set_class(layout.tiny, FOLDS_TINY_CLASS)
        self.query_one(f"#{TASK_DETAIL_IDENTITY}", GatedStatic).update_if_changed(
            header_text(view, now, compact=layout.compact)
        )
        self.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).update_if_changed(
            Briefing(view, now, layout)
        )
        self._render_verbs(view)

    def _briefing_layout(self) -> BriefingLayout:
        compact = self.size.width <= _TASK_DETAIL_COMPACT_MAX_COLUMNS or self.has_class(
            COMPACT_INSPECTOR_CLASS
        )
        context = self._widget_context
        return BriefingLayout(
            now_lines=context.now_compact_lines if compact else context.now_max_lines,
            compact=compact,
            tiny=self.screen.size.height <= TASK_DETAIL_TINY_MAX_ROWS,
        )

    def _render_verbs(self, view: TaskDetailView) -> None:
        verbs = briefing_verbs(view)
        for action in BriefingAction:
            self.query_one(f"#{TASK_DETAIL_ACTION_PREFIX}{action.value}", Button).display = (
                action in verbs
            )
        used = sum(cell_len(ACTION_LABELS[action]) + VERB_MARGIN for action in verbs)
        free = self.query_one(f"#{TASK_DETAIL_ACTIONS}").size.width - used
        hint = self.query_one(f"#{TASK_DETAIL_DETAILS_HINT}", TaskDetailHint)
        hint.display = free >= cell_len(DETAILS_HINT_TEXT)
        focused = self.screen.focused
        if isinstance(focused, TaskDetailVerb) and not focused.display:
            self.focus_summary()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._view is not None and self.is_mounted:
            self._render_activity(self._view)
            self._paint_hint()

    def _screen_is_active(self) -> bool:
        return screen_is_active(self)

    async def _back(self) -> None:
        if self._fixed:
            await self._navigate_home()
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
        await self._open_palette(self._action_role())

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
    def open_views(self) -> frozenset[str]:
        """The view ids of the folds that are open."""
        return frozenset(view_id for view_id, fold in self._folds.items() if fold.is_open)

    def focus_folds(self) -> None:
        """Move focus down to the section headers."""
        folds = self._folds_list()
        folds.focus_header(folds.current_fold)

    def focus_summary(self) -> None:
        """Focus the first verb the task offers, or the current fold header when it offers none."""
        first_verb = next(iter(self.query_one(f"#{TASK_DETAIL_VERBS}").displayed_children), None)
        if first_verb is not None:
            first_verb.focus()
            return
        self.focus_folds()

    def open_view(self, view_id: str) -> None:
        """Open the fold for `view_id`, bring it into view and focus it."""
        fold = self._folds.get(view_id)
        if fold is None:
            return
        fold.set_open(True)
        self.call_after_refresh(self._folds_list().focus_fold, fold)

    def step_out(self) -> bool:
        """Move focus from inside a fold body to its header; False when it is not in a body."""
        focused = self.screen.focused
        fold = self._fold_of(focused)
        if focused is None or fold is None or focused is fold.header:
            return False
        if isinstance(focused, TimelineFilterBar):
            focused.action_close()
            return True
        self._folds_list().focus_header(fold)
        return True

    def _fold_of(self, widget: Widget | None) -> DetailFold | None:
        ancestors = widget.ancestors if widget is not None else ()
        return next((item for item in ancestors if isinstance(item, DetailFold)), None)

    def _folds_list(self) -> DetailFolds:
        return self.query_one(f"#{TASK_DETAIL_FOLDS}", DetailFolds)

    async def return_from_detail(self) -> None:
        await self._back()

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

    async def _refresh(self) -> None:
        await self._load_view()
        for timeline in self._timelines.values():
            if timeline.shown:
                await timeline.refresh_page()

    async def _load_view(self) -> None:
        async with self._refresh_lock:
            if not self._screen_is_active():
                return
            task_id = self._task_id
            if task_id is None:
                self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
                    "Select a task to inspect it."
                )
                return
            try:
                view = await self._widget_context.views.task_detail(
                    task_id,
                    relationship_offset=self._relationship_offset,
                )
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                if self._screen_is_active():
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
            self._render_view(view)

    async def _refresh_quota_consumption(self) -> None:
        view = self._view
        if view is None:
            return
        quota_consumption = self._widget_context.views.task_quota_consumption(view.task_id)
        if not self._screen_is_active() or self._view is not view:
            return
        refreshed = replace(view, quota_consumption=quota_consumption)
        self._view = refreshed
        self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(self._usage_body(refreshed))

    def _usage_body(self, view: TaskDetailView) -> UsageBody:
        return UsageBody(view)

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

    def _render_view(self, view: TaskDetailView) -> None:
        self._view = view
        body = self.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody)
        scroll_y = body.scroll_target_y
        focus_generation = self._body_focus_generation
        focused = self.screen.focused
        restore_scroll = self._rendered_task_id == view.task_id
        self._rendered_task_id = view.task_id
        self._render_ownership(view)
        self.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static).update(SettingsBody(view))
        evidence = blocker_evidence(view)
        blocker = self.query_one(f"#{TASK_DETAIL_BLOCKER}", Static)
        blocker.update(evidence or "")
        self._render_relationships(view)
        self._render_activity(view)
        self.query_one(f"#{TASK_DETAIL_REQUEST}", Static).update(DescriptionBody(view))
        self.query_one(f"#{TASK_DETAIL_RESULTS}", Static).update(ResultsBody(view.results))
        self.query_one(f"#{TASK_DETAIL_DECISIONS}", Static).update(DecisionsBody(view))
        self.query_one(f"#{TASK_DETAIL_WORKSPACE}", Static).update(WorkspaceBody(view, Path.home()))
        if self._folds[TASK_VIEW_USAGE].is_open:
            self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(self._usage_body(view))
        blocker.display = evidence is not None
        self._paint_headlines(view)
        self._paint_hint()
        if self._on_initial_content is not None and not self._initial_content_reported:
            self._initial_content_reported = True
            self.call_after_refresh(self._on_initial_content)
        if restore_scroll:
            body.call_after_refresh(
                self._restore_body_scroll,
                body,
                scroll_y,
                focus_generation,
                focused,
            )

    def _paint_headlines(self, view: TaskDetailView) -> None:
        for section in DetailsSection:
            view_id = DETAILS_SECTION_VIEWS[section]
            if view_id not in self._timelines:
                self._folds[view_id].set_headline(section_headline(section, view))

    @on(TimelineSection.StatusChanged)
    def _timeline_status_changed(self, message: TimelineSection.StatusChanged) -> None:
        message.stop()
        view_id = next(
            view_id for view_id, timeline in self._timelines.items() if timeline is message.section
        )
        self._folds[view_id].set_headline(message.status)

    def _sync_timelines(self) -> None:
        for view_id, timeline in self._timelines.items():
            timeline.shown = self._folds[view_id].is_open

    @on(DetailFold.Toggled)
    def _fold_toggled(self, message: DetailFold.Toggled) -> None:
        message.stop()
        self._sync_timelines()
        fold = message.fold
        if not fold.is_open:
            return
        timeline = self._timelines.get(fold.view_id)
        if timeline is not None:
            self.run_worker(
                timeline.refresh_page(), group=TASK_DETAIL_WORKER_GROUP, exclusive=False
            )
        elif fold.view_id == TASK_VIEW_USAGE and self._view is not None:
            self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(self._usage_body(self._view))

    def _restore_body_scroll(
        self,
        body: TaskDetailBody,
        scroll_y: float,
        focus_generation: int,
        focused: Widget | None,
    ) -> None:
        if (
            not self._screen_is_active()
            or focus_generation != self._body_focus_generation
            or self.screen.focused is not focused
            or body.scroll_target_y != scroll_y
        ):
            return
        body.scroll_to(y=scroll_y, animate=False, immediate=True)
        if (
            focused is not None
            and focused.is_mounted
            and focused.display
            and body in focused.ancestors
            and not body.can_view_entire(focused)
        ):
            body.scroll_to_center(focused, animate=False)

    async def _next_relationship_page(self) -> None:
        if self._relationship_next_offset is not None:
            await self._show_relationship_page(self._relationship_next_offset)

    async def _previous_relationship_page(self) -> None:
        if self._relationship_previous_offset is not None:
            await self._show_relationship_page(self._relationship_previous_offset)

    async def _show_relationship_page(self, offset: int) -> None:
        self._relationship_offset = offset
        self._related_rows = ()
        await self._refresh()

    def _render_relationships(self, view: TaskDetailView) -> None:
        related = self.query_one(f"#{TASK_DETAIL_RELATED_LIST}", RowList)
        chosen = (
            self._related_rows[related.selected].task_id
            if related.selected < len(self._related_rows)
            else None
        )
        self._related_rows = relation_rows(view)
        related.set_rows(
            [row.cells for row in self._related_rows],
            selected=next(
                (index for index, row in enumerate(self._related_rows) if row.task_id == chosen),
                0,
            ),
            empty=NO_RELATED_ROWS,
        )
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
        notes: list[str] = []
        if relationships is None:
            notes.append(RELATIONSHIPS_UNAVAILABLE)
        else:
            if relationships.page_offset > 0 or relationships.has_more:
                page = relationships.page_offset // relationships.page_size + 1
                notes.append(f"{PAGE_LABEL} {page}")
            if (
                view.status_reason == StatusReason.SUPERSEDED.value
                and relationships.replacement_count == 0
            ):
                notes.append(NO_REPLACEMENT_TASKS)
        self.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).update(
            Group(*(grey(note) for note in notes))
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
        ownership = self.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
        ownership.update("\n".join(ownership_lines))
        ownership.display = bool(ownership_lines)

    async def _run_briefing_action(self, action: BriefingAction) -> None:
        if action is not BriefingAction.ANSWER:
            await self._open_palette(self._action_role(action), action=action.value)
            return
        resolve = self._widget_context.open_resolution
        escalation = next(
            (
                item
                for item in self._widget_context.views.escalations()
                if item.task_id == self._task_id
            ),
            None,
        )
        if resolve is None or escalation is None:
            self._set_feedback(NO_DECISION_TO_ANSWER)
            return
        await resolve(escalation.escalation_id)

    def _action_role(self, action: BriefingAction | None = None) -> Role | None:
        view = self._view
        if view is None or view.task_id != self._task_id:
            return None
        if action is BriefingAction.HAND_BACK and view.ownership:
            return Role(view.ownership[0])
        return Role(view.acting_role)


class TaskDetailScreen(ModalScreen[None]):
    """Show a selected task's shared detail over the Home browser."""

    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #task-detail-popup-frame { min-height: 1; }
    #task-detail-popup-frame.compact { margin: 0 1; padding: 0 1; }
    #task-detail-popup-frame.tiny {
        height: 100%; margin: 0; padding: 0;
    }
    #task-detail-panel { width: 100%; height: 1fr; min-height: 0; }
    #task-detail-panel #task-detail-body {
        height: 1fr; min-height: 0; max-height: 1fr; background: $surface;
    }
    """
    )

    def __init__(
        self,
        context: WidgetContext,
        task_id: str,
        *,
        initial_view: str = TASK_VIEW_SUMMARY,
    ) -> None:
        super().__init__()
        prepare_popup(self)
        self._widget_context = context
        self._task_id = task_id
        self._initial_view = initial_view

    def compose(self) -> ComposeResult:
        with PopupFrame(widget_id=TASK_DETAIL_POPUP_FRAME, classes=POPUP_FRAME_CLASS):
            with Horizontal(id=TASK_DETAIL_POPUP_HEADER, classes=POPUP_HEADER_CLASS):
                yield Label(
                    self._heading(),
                    id=TASK_DETAIL_POPUP_TITLE,
                    classes=POPUP_TITLE_CLASS,
                )
                yield Button(
                    popup_close_label(),
                    id=TASK_DETAIL_POPUP_CLOSE,
                    classes=POPUP_CLOSE_CLASS,
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            yield TaskDetailPanel(
                self._widget_context,
                self._task_id,
                fixed=False,
                close_detail=self._close,
                initial_view=self._initial_view,
                on_initial_content=self.mark_popup_content_ready,
            )

    def on_mount(self) -> None:
        self._apply_layout(self.size.width, self.size.height)
        panel = self.query_one("#task-detail-panel", TaskDetailPanel)
        if self._initial_view == TASK_VIEW_SUMMARY:
            panel.focus_summary()
        else:
            panel.open_view(self._initial_view)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width, event.size.height)
        self.call_after_refresh(
            self.query_one(f"#{TASK_DETAIL_POPUP_FRAME}", PopupFrame).update_preferred_size
        )

    def _apply_layout(self, width: int, height: int) -> None:
        frame = self.query_one(f"#{TASK_DETAIL_POPUP_FRAME}", PopupFrame)
        compact = (
            width <= _TASK_DETAIL_COMPACT_MAX_COLUMNS or height <= _TASK_DETAIL_COMPACT_MAX_ROWS
        )
        frame.set_class(compact, "compact")
        frame.set_class(height <= TASK_DETAIL_TINY_MAX_ROWS, "tiny")

    def mark_popup_content_ready(self) -> None:
        """Mark meaningful task content ready for popup sizing."""
        if not self.is_mounted or not widget_is_attached(self):
            return
        self.query_one(f"#{TASK_DETAIL_POPUP_FRAME}", PopupFrame).content_ready()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == TASK_DETAIL_POPUP_CLOSE:
            self.dismiss()

    def _heading(self) -> str:
        return f"Task details · {self._task_id}"

    async def _close(self) -> None:
        self.dismiss()

    async def action_close(self) -> None:
        self.dismiss()

    async def action_open_palette(self) -> None:
        await self.query_one("#task-detail-panel", TaskDetailPanel).open_palette()

    async def action_open_help(self) -> None:
        await cast("DashboardApp", self.app).action_show_help()


type CloseDetail = Callable[[], Awaitable[None]]


def _task_identity_text(identity: str) -> Text:
    return Text(identity, style=BOLD_PRIMARY)
