"""The `dash --home | --task T-n` Textual workspace shell (06 §5.4)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from rich.text import Text
from textual.app import App
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, ListItem, ListView, Rule, Select, Static

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_PROVIDER_REPORTED,
    BILLING_SOURCE_UNKNOWN,
)
from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import (
    DEFAULT_NOW_COMPACT_LINES,
    DEFAULT_NOW_MAX_LINES,
    DEFAULT_TASK_TIMELINE_REFRESH_SECONDS,
    FOCUS_FAULTS_VIEW,
    FOCUS_PROJECT_DIAGNOSTICS_VIEW,
    WIDGET_ENTRY_POINT_GROUP,
    CommandFeedbackEntryView,
    CommandFeedbackStatus,
    CommandHistoryView,
    PaletteDraft,
    PaletteRequest,
    TaskBrowserFilter,
    TaskPaletteRoute,
    WidgetContext,
    WidgetPlacement,
)
from shikiru.dashboard.detail import (
    COMPACT_INSPECTOR_CLASS,
    TASK_DETAIL_ESCAPE,
    TASK_DETAIL_HELP_KEY,
    TASK_DETAIL_VIEWS_KEY,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_SUMMARY,
    TASK_VIEW_USAGE,
    TaskDetailPanel,
    TaskDetailScreen,
)
from shikiru.dashboard.diagnostics import ProjectDiagnosticsPanel
from shikiru.dashboard.interaction import native_editor_has_focus
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.tick import pinned_tick
from shikiru.domain.commands import command_rejection_cause
from shikiru.errors import ConfigError
from shikiru.ui.compact import FocusAttentionStatus
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_CONTROL_CSS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_SECONDARY_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    prepare_popup,
)
from shikiru.ui.screen import (
    TabStopScreen,
    widget_is_attached,
    widget_is_current,
)
from shikiru.ui.section import GROUP_SEPARATOR, grey, row_name
from shikiru.ui.theme import install_theme
from shikiru.ui.tokens import (
    AMBER,
    DUSTY_ROSE,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.screen import Screen
    from textual.worker import Worker

    from shikiru.clock import Clock
    from shikiru.dashboard.contract import (
        AttentionView,
        DashboardViews,
        DashboardWidget,
        NavigateHome,
        NavigateRolePane,
        NavigateTaskWindow,
        OnChange,
        OnChangeRegistration,
        OpenPalette,
        OpenPaletteContext,
        ResolutionScreenFactory,
    )

_WIDGET_PLUGIN_KIND = "dashboard widget plugin"
_WIDGET_DIALOGUE = "dialogue"
_WIDGET_ESCALATIONS = "escalations"
_WIDGET_QUEUE = "queue"
_TASK_VIEW_OMITTED_PLACEMENTS = frozenset(
    {
        WidgetPlacement.IDENTITY,
        WidgetPlacement.TASK_LIST,
        WidgetPlacement.ATTENTION,
        WidgetPlacement.RESOURCES,
        WidgetPlacement.WORKFLOW,
        WidgetPlacement.DIAGNOSTICS,
    }
)
_HELP_CLOSE_BUTTON = "workspace-help-close"
_HELP_CONTEXT = "workspace-help-context"
_COMMAND_HISTORY_CLOSE = "command-history-close"
_COMMAND_HISTORY_COPY = "command-history-copy"
_COMMAND_HISTORY_WORKER = "command-history-updates"
_COMMAND_HISTORY_SCROLL = "command-history-scroll"
_COMMAND_HISTORY_FILTER = "command-history-filter"
_COMMAND_HISTORY_PREVIOUS = "command-history-previous"
_COMMAND_HISTORY_NEXT = "command-history-next"
_COMMAND_HISTORY_PAGE = "command-history-page"
_COMMAND_HISTORY_HEADER = "command-history-header"
_COMMAND_HISTORY_PAGE_SIZE = 20
_COMMAND_HISTORY_COMPACT_MAX_COLUMNS = 32
_COMMAND_HISTORY_PAGE_COMPACT_MAX_COLUMNS = 48
_COMMAND_HISTORY_TINY_MAX_ROWS = 8
_COMMAND_HISTORY_ALL = "all"
_COMMAND_HISTORY_PREVIOUS_KEY = "left"
_COMMAND_HISTORY_NEXT_KEY = "right"
_COMMAND_HISTORY_PREVIOUS_ACTION = "previous_page"
_COMMAND_HISTORY_NEXT_ACTION = "next_page"
COMMAND_ACTION_SAY = "Say"
COMMAND_RESULT_TASK_ID = "task_id"
COMMAND_RESULT_STATUS = "status"
COMMAND_RESULT_EFFECT = "effect"
COMMAND_RESULT_STOPPING = "stopping"
COMMAND_RESULT_PRIORITY = "priority"
COMMAND_RESULT_PAUSED = "paused"
COMMAND_RESULT_NOW = "now"
_SEARCH_KEY = "slash"
_ACTIONS_KEY = "ctrl+p"
_HOME_FOOTER_CLEAR = "focus-footer-clear"
_HOME_FOOTER_ACTIONS = "focus-footer-actions"
_RESOURCES_RULE = "resources-rule"
_HOME_FOOTER_HELP = "focus-footer-help"
_HOME_FOOTER_DETACH = "focus-footer-detach"
_HOME_FOOTER_COMMAND_STATUS = "workspace-command-status"
_CONTEXT_VIEWS_HEADER = "context-views-header"
_CONTEXT_VIEWS_CLOSE = "context-views-close"
_CONTEXT_WIDGET_HEADER = "context-widget-header"
_PROJECT_USAGE_TITLE = "Project usage"
_CONTEXT_WIDGET_CLOSE = "context-widget-close"
_CONTEXT_WIDGET_COMPACT_MAX_ROWS = 20
_CONTEXT_WIDGET_COMPACT_MAX_COLUMNS = 48
_CONTEXT_WIDGET_FRAME_VERTICAL_MARGIN_ROWS = 2
_POPUP_CLOSE_LABEL = "Esc \u00d7"
_DASHBOARD_PALETTE_EVENT_KEY = "f12"
_SUMMARY_ATTENTION = "attention"
_SUMMARY_RUNNING = "running"
_SUMMARY_WAITING = "waiting"
HOME_SPLIT_MIN_COLUMNS = 73
FOCUS_FOOTER_GROUP_MIN_COLUMNS = 48
FOCUS_FOOTER_STATUS_WIDE_LABEL_MIN_COLUMNS = 54
FOCUS_FOOTER_COMPACT_CONTROL_COLUMN_WIDTH = 13
FOCUS_FOOTER_COMPACT_WRAP_ROWS = 1
FOCUS_FOOTER_STACKED_COLUMN_COUNT = 1
FOCUS_FOOTER_GROUP_COUNT = 2
FOCUS_FOOTER_ACTION_COUNT = 5
FOCUS_SUMMARY_MAX_ROWS = 12
FOCUS_MAIN_MIN_ROWS = 3
FOCUS_COMPACT_MAIN_ROWS = 8
FOCUS_COMPACT_TASK_LIST_ROWS = 6
FOCUS_MIN_TASK_LIST_ROWS = 12
FOCUS_TASK_LIST_RECENT_RESERVE_ROWS = 3
FOCUS_FRAME_BORDER_ROWS = 2
FOCUS_FOOTER_MIN_HEIGHT = 9
FOCUS_ATTENTION_HEIGHT = 3
FOCUS_SHORT_HOME_HEIGHT = 14
FOCUS_COMPACT_SHORT_HOME_HEIGHT = 20
FOCUS_COMPACT_SHORT_MAIN_ROWS = 7
FOCUS_VIEWS_LIST = "focus-views-list"
FOCUS_VIEW_PREFIX = "focus-view-"
FOCUS_COMMAND_HISTORY = "commands"
FOCUS_ATTENTION = "attention"
FOCUS_REVEAL_TASK = "reveal-task"
FOCUS_USAGE_DETAILS = "usage"
FOCUS_TASK_FILTER_PREFIX = "filter:"
FOCUS_CONTEXT_VIEW_WORKER = "focus-context-view"
FOCUS_OPERATOR_ACTIONS = "operator-actions"
FOCUS_CONTEXT_HELP = "context-help"
EMPTY_CONTEXT_CHOICE = ""
TASK_PALETTE_ROUTE_WORKER = "task-palette-route"
_MAX_CONTEXTUAL_PALETTE_DRAFTS = 16
_TASK_MOVE_STEPS = {"up": -1, "down": 1}
_ARROW_KEY_OWNERS = (ListView, RowList)


@dataclass
class _MotionCallback:
    callback: OnChange
    task: asyncio.Future[None] | None = None

    @property
    def in_flight(self) -> bool:
        return self.task is not None and not self.task.done()

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


class _TaskBrowserNavigation(Protocol):
    async def activate_filter(self, task_filter: TaskBrowserFilter) -> None: ...

    async def move_selection(self, step: int) -> None: ...

    def focus_task_results(self) -> None: ...

    async def reveal_selected_task(self) -> None: ...

    def refresh_after_resume(self) -> None: ...

    def clear_selection(self) -> None: ...

    def sync_context_selection(self, task_id: str | None) -> None: ...


class _AttentionPanelNavigation(Protocol):
    def activate_attention(self) -> None: ...

    async def activate_selected_decision(self) -> bool: ...

    def follow_task(self, task_id: str | None) -> None: ...


class WidgetPluginError(ConfigError):
    """A configured dashboard widget name has no matching, loadable entry point."""


def _load_widget(name: str) -> DashboardWidget:
    loaded = load_unique_entry_point(
        WIDGET_ENTRY_POINT_GROUP, name, WidgetPluginError, _WIDGET_PLUGIN_KIND
    )
    widget_class = cast("type[DashboardWidget]", loaded)
    try:
        plugin = widget_class()
    except Exception as error:
        message = f"configured dashboard widget {name!r} failed to initialize: {error}"
        raise WidgetPluginError(message) from error
    if not callable(getattr(plugin, "create", None)):
        message = f"configured dashboard widget {name!r} has no create(context) entry point"
        raise WidgetPluginError(message)
    return plugin


def _placement_for_widget(name: str, plugin: DashboardWidget) -> WidgetPlacement:
    configured = getattr(plugin, "placement", None)
    if configured is None:
        return WidgetPlacement.EXTENSIONS
    try:
        return WidgetPlacement(configured)
    except (TypeError, ValueError) as error:
        message = (
            f"configured dashboard widget {name!r} has an unsupported placement {configured!r}"
        )
        raise WidgetPluginError(message) from error


def _contextual_widget_label(name: str, placement: WidgetPlacement) -> str:
    category = {
        WidgetPlacement.ACTIVITY: "Activity",
        WidgetPlacement.DETAILED_USAGE: "Usage",
        WidgetPlacement.DIAGNOSTICS: "Diagnostics",
        WidgetPlacement.WORKFLOW: "Workflow",
        WidgetPlacement.EXTENSIONS: "Extensions",
    }.get(placement)
    label = name.replace("_", " ")
    return f"{category} · {label}" if category is not None else label.capitalize()


@dataclass(frozen=True)
class _ContextChoice:
    choice_id: str
    label: str
    title: str | None = None
    task_filter: TaskBrowserFilter | None = None
    widget_name: str | None = None
    placement: WidgetPlacement | None = None
    command_history: bool = False
    attention: bool = False
    reveal_task: bool = False
    operator_actions: bool = False
    context_help: bool = False


class _ContextViewsScreen(ModalScreen[str]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Back")]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #focus-views-list { max-height: 1fr; min-height: 0; border: none; background: $surface; }
    #context-views-hint { height: 1; }
    #context-views-frame.compact { margin: 0 1; padding: 0 1; }
    """
    )

    def __init__(self, choices: tuple[_ContextChoice, ...]) -> None:
        super().__init__()
        prepare_popup(self)
        self._choices = {
            f"{FOCUS_VIEW_PREFIX}{index}": choice for index, choice in enumerate(choices)
        }

    def compose(self) -> ComposeResult:
        with PopupFrame(widget_id="context-views-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id=_CONTEXT_VIEWS_HEADER, classes=POPUP_HEADER_CLASS):
                yield Label("Views", id="context-views-title", classes=POPUP_TITLE_CLASS)
                yield Button(_POPUP_CLOSE_LABEL, id=_CONTEXT_VIEWS_CLOSE, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            items = [
                ListItem(Label(choice.label), id=choice_id)
                for choice_id, choice in self._choices.items()
            ]
            yield ListView(*items, id=FOCUS_VIEWS_LIST, classes=POPUP_BODY_CLASS)
            yield Static(
                "↑↓ select · Enter open · Esc back",
                id="context-views-hint",
                classes=POPUP_SECONDARY_CLASS,
            )

    def on_mount(self) -> None:
        self._apply_compact_layout(self.size.width, self.size.height)
        self.set_focus(self.query_one(f"#{FOCUS_VIEWS_LIST}", ListView))
        self.call_after_refresh(self.query_one("#context-views-frame", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)
        self.call_after_refresh(
            self.query_one("#context-views-frame", PopupFrame).update_preferred_size
        )

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one("#context-views-frame", PopupFrame)
        compact = (
            width <= _CONTEXT_WIDGET_COMPACT_MAX_COLUMNS
            or height <= _CONTEXT_WIDGET_COMPACT_MAX_ROWS
        )
        frame.set_class(compact, "compact")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        choice_id = event.item.id
        if choice_id is not None and choice_id in self._choices:
            self.dismiss(self._choices[choice_id].choice_id)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == _CONTEXT_VIEWS_CLOSE:
            self.dismiss(EMPTY_CONTEXT_CHOICE)

    async def action_close(self) -> None:
        self.dismiss("")


class _ContextWidgetScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Back")]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #context-widget-frame.compact { margin: 0 2; padding: 0 2; }
    #context-widget-content { max-height: 1fr; min-height: 0; }
    """
    )

    def __init__(self, label: str, widget: Widget) -> None:
        super().__init__()
        prepare_popup(self)
        self._label = label
        self._widget = widget

    def compose(self) -> ComposeResult:
        with PopupFrame(widget_id="context-widget-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id=_CONTEXT_WIDGET_HEADER, classes=POPUP_HEADER_CLASS):
                yield Label(self._label, id="context-widget-title", classes=POPUP_TITLE_CLASS)
                yield Button(
                    _POPUP_CLOSE_LABEL, id=_CONTEXT_WIDGET_CLOSE, classes=POPUP_CLOSE_CLASS
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            with VerticalScroll(id="context-widget-content", classes=POPUP_BODY_CLASS):
                yield self._widget

    def on_mount(self) -> None:
        self._apply_compact_layout(self.size.width, self.size.height)
        self.set_focus(self.query_one("#context-widget-content", VerticalScroll))
        self.call_after_refresh(self.query_one("#context-widget-frame", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)
        self.call_after_refresh(
            self.query_one("#context-widget-frame", PopupFrame).update_preferred_size
        )

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one("#context-widget-frame")
        compact = (
            height <= _CONTEXT_WIDGET_COMPACT_MAX_ROWS
            or width <= _CONTEXT_WIDGET_COMPACT_MAX_COLUMNS
        )
        frame.set_class(compact, "compact")
        frame.styles.max_height = max(
            1,
            height - (0 if compact else _CONTEXT_WIDGET_FRAME_VERTICAL_MARGIN_ROWS),
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == _CONTEXT_WIDGET_CLOSE:
            self.dismiss()

    async def action_close(self) -> None:
        self.dismiss()


_COMMAND_STATUS_STYLES = {
    CommandFeedbackStatus.PENDING: f"{AMBER} bold",
    CommandFeedbackStatus.APPLIED: f"{SAGE} bold",
    CommandFeedbackStatus.REJECTED: f"{DUSTY_ROSE} bold",
}


def _command_history_text(history: CommandHistoryView) -> Text:
    if not history.entries:
        return Text("No commands recorded.")
    return Text("\n").join(
        _command_entry_text(entry, engine_up=history.engine_up) for entry in history.entries
    )


def _command_history_page_label(arrow: str, word: str, *, compact: bool = False) -> Text:
    if compact:
        return Text(arrow, style=STEEL_BLUE)
    return Text.assemble((arrow, STEEL_BLUE), f" {word}")


def _command_queue_time(timestamp: str | None) -> str | None:
    if timestamp is None:
        return None
    queued_at = datetime.fromisoformat(timestamp).astimezone(UTC)
    return f"{queued_at.month}/{queued_at.day} {queued_at:%H:%M}Z"


def _command_entry_text(entry: CommandFeedbackEntryView, *, engine_up: bool) -> Text:
    result = _command_result_data(entry)
    if entry.status is CommandFeedbackStatus.PENDING:
        engine_state = "running" if engine_up else "stopped"
        queued_at = _command_queue_time(entry.queued_at)
        queued = f"queued {queued_at}; " if queued_at is not None else ""
        detail = (
            f"{queued}engine currently {engine_state}; "
            "original acknowledgement wait context unknown"
        )
    elif entry.status is CommandFeedbackStatus.APPLIED:
        detail = _command_applied_effect(entry, result)
    else:
        detail = command_rejection_cause(result)
    return Text.assemble(
        grey(f"#{entry.command_id}"),
        grey(GROUP_SEPARATOR),
        Text(entry.status.value, style=_COMMAND_STATUS_STYLES[entry.status]),
        grey(GROUP_SEPARATOR),
        Text(entry.action, style=PRIMARY_TEXT_COLOR),
        grey(GROUP_SEPARATOR),
        row_name(entry.target),
        grey(GROUP_SEPARATOR),
        grey(detail),
    )


def _command_result_data(entry: CommandFeedbackEntryView) -> dict[str, object] | None:
    if entry.result is None:
        return None
    return cast("dict[str, object]", json.loads(entry.result))


def _command_applied_effect(
    entry: CommandFeedbackEntryView,
    result: dict[str, object] | None,
) -> str:
    if entry.action == COMMAND_ACTION_SAY:
        return "guidance queued for the selected role's next turn"
    if result is None:
        return "engine applied the command effect"
    effect = result.get(COMMAND_RESULT_EFFECT)
    if isinstance(effect, str):
        return effect
    if result.get(COMMAND_RESULT_STOPPING) is True:
        return "shutdown requested"
    task_id = result.get(COMMAND_RESULT_TASK_ID)
    if not isinstance(task_id, str):
        return "engine applied the command effect"
    return (
        _task_command_effect(result, task_id, entry.target) or "engine applied the command effect"
    )


def _task_command_effect(result: dict[str, object], task_id: str, target: str) -> str | None:
    task_prefix = "" if task_id == target else f"{task_id} "
    status = result.get(COMMAND_RESULT_STATUS)
    if isinstance(status, str):
        return f"{task_prefix}{status}"
    priority = result.get(COMMAND_RESULT_PRIORITY)
    if isinstance(priority, int) and not isinstance(priority, bool):
        return f"{task_prefix}priority {priority}"
    paused = result.get(COMMAND_RESULT_PAUSED)
    if paused is True:
        suffix = " now" if result.get(COMMAND_RESULT_NOW) is True else ""
        return f"{task_prefix}paused{suffix}"
    if paused is False:
        return f"{task_prefix}resumed"
    return None


def _command_summary_text(history: CommandHistoryView) -> Text:
    entry = history.entries[0]
    summary = Text()
    summary.append(entry.status.value, style=_COMMAND_STATUS_STYLES[entry.status])
    detail = f"{entry.action} {entry.target}"
    if entry.status is CommandFeedbackStatus.PENDING:
        queued_at = _command_queue_time(entry.queued_at)
        detail = f"{detail}: queued {queued_at}" if queued_at is not None else detail
    elif entry.status is CommandFeedbackStatus.APPLIED:
        result = _command_result_data(entry)
        detail = f"{detail}: {_command_applied_effect(entry, result)}"
    else:
        result = _command_result_data(entry)
        detail = f"{detail}: {command_rejection_cause(result)}"
    summary.append(f": {detail}", style=SECONDARY_TEXT_COLOR)
    return summary


def _truncate_command_summary(summary: Text, width: int) -> Text:
    truncated = summary.copy()
    truncated.truncate(max(0, width), overflow="ellipsis")
    return truncated


class _WorkspaceHelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #workspace-help-scroll { max-height: 1fr; min-height: 0; }
    #workspace-help-copy { height: auto; color: $primary-text; }
    #workspace-help-frame.compact { margin: 0 1; padding: 0 1; }
    """
    )

    def __init__(
        self,
        task_id: str | None,
        widget_names: Sequence[str],
        tmux_detach_hint: str | None,
    ) -> None:
        super().__init__()
        prepare_popup(self)
        self._task_id = task_id
        self._widget_names = tuple(widget_names)
        self._tmux_detach_hint = tmux_detach_hint

    def compose(self) -> ComposeResult:
        """Render help for Home or the selected task workspace."""
        view = f"Task detail · {self._task_id}" if self._task_id else "Focus"
        if self._task_id:
            lines = [
                "Escape: return to the compact task summary or Home",
                "v: choose reports, request, evidence, relationships, workspace or plugins",
                "Ctrl+P: open applicable operator actions for this task",
                "Events and relationships: Up/Down select · PgUp/PgDn page · End follows",
                "Mouse: Views offers task sections, Actions and Help",
                "Navigation to task and role windows is under Workspace and existing role targets",
                "Command applied means queued/applied by the engine;",
                "an agent may consume guidance later",
            ]
        else:
            lines = [
                "Up/Down: select a task",
                "Escape: close popup, clear search, then clear task selection",
                "Ctrl+P: open contextual operator actions",
                "/: search task ID or title",
                "v: open filters, history and configured secondary views",
                "History: ← newer · → older",
                "Mouse: select a task; use its detail controls for views and actions",
                "?: show this help",
            ]
            if self._tmux_detach_hint is None:
                lines.append("tmux detach is unavailable outside the project session")
            else:
                lines.append(f"{self._tmux_detach_hint} d: detach from tmux")
        if _WIDGET_ESCALATIONS in self._widget_names:
            lines.append(
                "Escalations: Up/Down select, Left/Right choose a decision, "
                "o opens evidence, r opens resolution"
            )
        if _WIDGET_DIALOGUE in self._widget_names:
            lines.append("Dialogue: Enter opens full text")
        if _WIDGET_QUEUE in self._widget_names and self._task_id is None:
            lines.append(
                "Task browser: / search, Up/Down select, v opens filters, "
                "Escape clears search first"
            )
        lines.extend(
            (
                "Usage separates unknown billing modes from unreported API charges",
                (
                    f"{BILLING_SOURCE_OPERATOR_CONFIG} + {BILLING_MODE_UNKNOWN} means "
                    f"explicitly set to {BILLING_MODE_UNKNOWN}"
                ),
                (
                    f"{BILLING_SOURCE_PROVIDER_REPORTED} + {BILLING_MODE_UNKNOWN} means "
                    f"a provider-reported {BILLING_MODE_UNKNOWN}"
                ),
                (
                    f"{BILLING_SOURCE_UNKNOWN} + {BILLING_MODE_UNKNOWN} means "
                    "unavailable/not recorded; historic cause unknown"
                ),
                (
                    "Set [agents.<name>.options].billing_mode to "
                    f"{BILLING_MODE_API}, {BILLING_MODE_SUBSCRIPTION} or "
                    f"{BILLING_MODE_UNKNOWN} for new sessions"
                ),
                "Existing sessions keep the mode and source recorded when they started",
            )
        )
        with PopupFrame(widget_id="workspace-help-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id="workspace-help-header", classes=POPUP_HEADER_CLASS):
                yield Label(f"Help · {view}", id=_HELP_CONTEXT, classes=POPUP_TITLE_CLASS)
                yield Button(_POPUP_CLOSE_LABEL, id=_HELP_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            with VerticalScroll(id="workspace-help-scroll", classes=POPUP_BODY_CLASS):
                yield Static("\n".join(lines), id="workspace-help-copy", markup=False)

    def on_mount(self) -> None:
        self._apply_compact_layout(self.size.width, self.size.height)
        self.set_focus(self.query_one("#workspace-help-scroll", VerticalScroll))
        self.call_after_refresh(self.query_one("#workspace-help-frame", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)
        self.call_after_refresh(
            self.query_one("#workspace-help-frame", PopupFrame).update_preferred_size
        )

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one("#workspace-help-frame", PopupFrame)
        compact = (
            width <= _CONTEXT_WIDGET_COMPACT_MAX_COLUMNS
            or height <= _CONTEXT_WIDGET_COMPACT_MAX_ROWS
        )
        frame.set_class(compact, "compact")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close help when its button is activated."""
        if event.button.id == _HELP_CLOSE_BUTTON:
            self.dismiss()

    async def action_close(self) -> None:
        """Dismiss contextual help."""
        self.dismiss()


class _CommandHistoryScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "close", "Back"),
        (_COMMAND_HISTORY_PREVIOUS_KEY, _COMMAND_HISTORY_PREVIOUS_ACTION, "Newer history"),
        (_COMMAND_HISTORY_NEXT_KEY, _COMMAND_HISTORY_NEXT_ACTION, "Older history"),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #command-history-filter { width: 100%; height: 3; }
    #command-history-scroll { min-height: 0; }
    #command-history-copy { height: auto; padding: 0; }
    #command-history-page { height: 1; }
    #command-history-controls { height: 1; width: 100%; }
    #command-history-controls Button {
        width: auto; min-width: 1; height: 1; margin: 0; padding: 0; border: none;
    }
    #command-history-frame.compact { margin: 0 1; padding: 0 1; }
    #command-history-frame.tiny #command-history-filter,
    #command-history-frame.tiny #command-history-page { display: none; }
    """
    )

    def __init__(self, views: DashboardViews, on_change: OnChangeRegistration) -> None:
        super().__init__()
        prepare_popup(self)
        self._views = views
        self._on_change = on_change
        self._offset = 0
        self._status: CommandFeedbackStatus | None = None
        self._has_more = False
        self._change_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        with PopupFrame(widget_id="command-history-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id=_COMMAND_HISTORY_HEADER, classes=POPUP_HEADER_CLASS):
                yield Label(
                    "Command history",
                    id="command-history-title",
                    classes=POPUP_TITLE_CLASS,
                )
                yield Button(
                    _POPUP_CLOSE_LABEL, id=_COMMAND_HISTORY_CLOSE, classes=POPUP_CLOSE_CLASS
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            yield Select(
                (
                    ("All statuses", _COMMAND_HISTORY_ALL),
                    *((status.value, status.value) for status in CommandFeedbackStatus),
                ),
                value=_COMMAND_HISTORY_ALL,
                id=_COMMAND_HISTORY_FILTER,
            )
            with VerticalScroll(id=_COMMAND_HISTORY_SCROLL, classes=POPUP_BODY_CLASS):
                yield Static("Loading command history…", id=_COMMAND_HISTORY_COPY, markup=False)
            yield Label("", id=_COMMAND_HISTORY_PAGE, classes=POPUP_SECONDARY_CLASS)
            with Horizontal(id="command-history-controls"):
                yield Button(
                    _command_history_page_label("←", "newer"),
                    id=_COMMAND_HISTORY_PREVIOUS,
                    disabled=True,
                )
                yield Button(
                    _command_history_page_label("→", "older"),
                    id=_COMMAND_HISTORY_NEXT,
                    disabled=True,
                )

    async def on_mount(self) -> None:
        self._apply_compact_layout(self.size.width, self.size.height)
        self.set_focus(cast("Select[str]", self.query_one(f"#{_COMMAND_HISTORY_FILTER}", Select)))
        self._display_history()
        self.call_after_refresh(self._limit_history_body, self.size.height)
        self.call_after_refresh(self.query_one("#command-history-frame", PopupFrame).content_ready)
        self._change_worker = self.run_worker(
            self._on_change(self._refresh), group=_COMMAND_HISTORY_WORKER
        )

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)
        self.call_after_refresh(self._limit_history_body, event.size.height)
        self.call_after_refresh(
            self.query_one("#command-history-frame", PopupFrame).update_preferred_size
        )

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one("#command-history-frame", PopupFrame)
        compact = width <= _COMMAND_HISTORY_COMPACT_MAX_COLUMNS
        tiny = height <= _COMMAND_HISTORY_TINY_MAX_ROWS
        frame.set_class(compact or tiny, "compact")
        frame.set_class(tiny, "tiny")
        self.query_one("#command-history-title", Label).update(
            "Hist." if compact or tiny else "Command history"
        )
        self.query_one(f"#{_COMMAND_HISTORY_PREVIOUS}", Button).label = _command_history_page_label(
            "←", "newer", compact=compact
        )
        self.query_one(f"#{_COMMAND_HISTORY_NEXT}", Button).label = _command_history_page_label(
            "→", "older", compact=compact
        )

    def _limit_history_body(self, viewport_height: int) -> None:
        if not self.is_mounted:
            return
        frame = self.query_one("#command-history-frame")
        body = self.query_one("#command-history-scroll", VerticalScroll)
        fixed_height = sum(child.region.height for child in frame.children if child is not body)
        body.styles.max_height = max(
            0,
            viewport_height
            - frame.styles.margin.height
            - frame.styles.gutter.height
            - fixed_height,
        )

    def on_unmount(self) -> None:
        if self._change_worker is not None:
            self._change_worker.cancel()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        pages = action in {_COMMAND_HISTORY_PREVIOUS_ACTION, _COMMAND_HISTORY_NEXT_ACTION}
        if pages and not self._arrow_paging_available():
            return None
        if action == _COMMAND_HISTORY_PREVIOUS_ACTION:
            return self._offset > 0
        if action == _COMMAND_HISTORY_NEXT_ACTION:
            return self._has_more
        return True

    def _arrow_paging_available(self) -> bool:
        filter_select = cast("Select[str]", self.query_one(f"#{_COMMAND_HISTORY_FILTER}", Select))
        if filter_select.expanded:
            return False
        return self.focused is filter_select or not native_editor_has_focus(self.focused)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == _COMMAND_HISTORY_CLOSE:
            self.dismiss()
        elif event.button.id == _COMMAND_HISTORY_PREVIOUS:
            self.action_previous_page()
        elif event.button.id == _COMMAND_HISTORY_NEXT and self._has_more:
            self.action_next_page()

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != _COMMAND_HISTORY_FILTER:
            return
        selected = event.value if isinstance(event.value, str) else _COMMAND_HISTORY_ALL
        self._status = None if selected == _COMMAND_HISTORY_ALL else CommandFeedbackStatus(selected)
        self._offset = 0
        self._display_history()

    async def action_close(self) -> None:
        self.dismiss()

    def action_previous_page(self) -> None:
        if self._offset == 0:
            return
        self._offset = max(0, self._offset - _COMMAND_HISTORY_PAGE_SIZE)
        self._display_history()

    def action_next_page(self) -> None:
        if self._has_more:
            self._offset += _COMMAND_HISTORY_PAGE_SIZE
            self._display_history()

    async def _refresh(self) -> None:
        if widget_is_attached(self):
            self._display_history(preserve_scroll=True)

    def _display_history(self, *, preserve_scroll: bool = False) -> None:
        scroll = self.query_one(f"#{_COMMAND_HISTORY_SCROLL}", VerticalScroll)
        scroll_y = scroll.scroll_y if preserve_scroll and self.is_mounted else 0
        scroll_at_bottom = preserve_scroll and scroll_y >= scroll.max_scroll_y
        history = self._views.command_history(
            expanded=True,
            limit=_COMMAND_HISTORY_PAGE_SIZE,
            offset=self._offset,
            status=self._status,
        )
        if history.total_count and history.offset >= history.total_count:
            self._offset = max(0, (history.total_count - 1) // history.limit * history.limit)
            history = self._views.command_history(
                expanded=True,
                limit=_COMMAND_HISTORY_PAGE_SIZE,
                offset=self._offset,
                status=self._status,
            )
        elif not history.total_count:
            self._offset = 0
        self.query_one(f"#{_COMMAND_HISTORY_COPY}", Static).update(_command_history_text(history))
        self._has_more = history.has_more
        start = history.offset + 1 if history.entries else 0
        end = history.offset + len(history.entries)
        page_number = history.offset // history.limit + 1
        filter_name = history.status.value if history.status is not None else "all statuses"
        page_text = (
            f"{page_number} · {end}/{history.total_count}"
            if self.size.width <= _COMMAND_HISTORY_PAGE_COMPACT_MAX_COLUMNS
            else f"{filter_name} · page {page_number} · rows {start}-{end} of {history.total_count}"
        )
        self.query_one(f"#{_COMMAND_HISTORY_PAGE}", Label).update(page_text)
        self.query_one(f"#{_COMMAND_HISTORY_PREVIOUS}", Button).disabled = history.offset == 0
        self.query_one(f"#{_COMMAND_HISTORY_NEXT}", Button).disabled = not history.has_more
        if preserve_scroll:
            self.call_after_refresh(
                lambda: scroll.scroll_to(
                    y=scroll.max_scroll_y if scroll_at_bottom else scroll_y,
                    animate=False,
                )
            )


class _CommandStatusLine(Button):
    def __init__(self, views: DashboardViews, on_change: OnChangeRegistration) -> None:
        super().__init__(
            "",
            id=_HOME_FOOTER_COMMAND_STATUS,
            tooltip="Open full command history",
            flat=True,
        )
        self._views = views
        self._on_change = on_change
        self._history: CommandHistoryView | None = None
        self._transient_message: str | None = None

    async def on_mount(self) -> None:
        self._display_history()
        self.call_after_refresh(self._render_status)
        self.run_worker(self._on_change(self._refresh), group=_COMMAND_HISTORY_WORKER)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._render_status)

    async def _refresh(self) -> None:
        if widget_is_attached(self):
            self._display_history()

    def _display_history(self) -> None:
        self._transient_message = None
        self._history = self._views.command_history()
        self._render_status()

    def show_transient(self, message: str) -> None:
        self._transient_message = message
        self._render_status()

    def _render_status(self) -> None:
        if not widget_is_attached(self):
            return
        was_displayed = self.display
        if self._transient_message is not None:
            self.label = _truncate_command_summary(
                Text(self._transient_message, style=SECONDARY_TEXT_COLOR),
                self._summary_render_width(),
            )
            self.display = True
        else:
            history = self._history
            self.display = bool(history and history.entries)
            if history is not None and history.entries:
                self.label = _truncate_command_summary(
                    _command_summary_text(history), self._summary_render_width()
                )
        if (
            was_displayed != self.display
            and isinstance(self.parent, _HomeFooter)
            and widget_is_attached(self.parent)
        ):
            app = cast("DashboardApp", self.app)
            self.call_after_refresh(app.refresh_layout)
            self.call_after_refresh(self._render_status)

    def _summary_render_width(self) -> int:
        return max(0, self.content_size.width)


class _HomeAttentionStatus(Static):
    def __init__(self, context: WidgetContext) -> None:
        super().__init__("", id="escalations", markup=False)
        self._widget_context = context
        self._attention: AttentionView | None = None
        self._remove_motion_callback: Callable[[], None] | None = None

    async def on_mount(self) -> None:
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_if_attached), exclusive=True)
        self._remove_motion_callback = self._widget_context.on_motion(self._refresh_if_active)

    def on_unmount(self) -> None:
        if self._remove_motion_callback is not None:
            self._remove_motion_callback()
            self._remove_motion_callback = None

    async def on_click(self, event: events.Click) -> None:
        attention = self._attention
        if attention is None:
            return
        if not attention.groups:
            summary = self._widget_context.views.workspace_summary()
            if summary.engine_up is False and self._widget_context.open_contextual_view is not None:
                await self._widget_context.open_contextual_view(FOCUS_COMMAND_HISTORY)
                event.stop()
            return
        group = attention.groups[0]
        if group.task_id is not None and self._widget_context.on_task_selected is not None:
            self._widget_context.on_task_selected(group.task_id)
        if group.escalations:
            if self._widget_context.open_resolution is not None:
                await self._widget_context.open_resolution(group.escalations[0].escalation_id)
            event.stop()
        elif group.task_id is not None:
            if self._widget_context.on_task_selected is not None:
                self._widget_context.on_task_selected(group.task_id)
            event.stop()
        elif self._widget_context.open_contextual_view is not None:
            await self._widget_context.open_contextual_view(FOCUS_FAULTS_VIEW)
            event.stop()

    async def _refresh_if_active(self) -> None:
        if widget_is_current(self):
            await self._refresh()

    async def _refresh_if_attached(self) -> None:
        if widget_is_attached(self):
            await self._refresh()

    async def _refresh(self) -> None:
        attention = self._widget_context.views.attention()
        summary = self._widget_context.views.workspace_summary()
        self._attention = attention
        self.update(
            FocusAttentionStatus(
                attention,
                summary,
                action_hint="Ctrl+P actions",
            )
        )


class _HomeFooter(Grid):
    def __init__(
        self,
        tmux_detach_hint: str | None,
        views: DashboardViews,
        on_change: OnChangeRegistration,
    ) -> None:
        super().__init__(id="focus-footer")
        self._tmux_detach_hint = tmux_detach_hint
        self._views = views
        self._on_change = on_change

    def compose(self) -> ComposeResult:
        yield Static(
            Text.assemble(("↑↓", STEEL_BLUE), (" select", SECONDARY_TEXT_COLOR)),
            markup=False,
        )
        yield Button(
            Text.assemble(("Esc", STEEL_BLUE), (" clear", SECONDARY_TEXT_COLOR)),
            id=_HOME_FOOTER_CLEAR,
        )
        yield Button(
            Text.assemble(("Ctrl+P", STEEL_BLUE), (" actions", SECONDARY_TEXT_COLOR)),
            id=_HOME_FOOTER_ACTIONS,
        )
        detach_hint = (
            Text.assemble(
                (self._tmux_detach_hint, STEEL_BLUE),
                (" d", STEEL_BLUE),
                (" detach", SECONDARY_TEXT_COLOR),
            )
            if self._tmux_detach_hint is not None
            else Text("tmux detach unavailable", style=SECONDARY_TEXT_COLOR)
        )
        yield Button(
            detach_hint,
            id=_HOME_FOOTER_DETACH,
            disabled=self._tmux_detach_hint is None,
        )
        yield Button(
            Text.assemble(("?", STEEL_BLUE), (" help", SECONDARY_TEXT_COLOR)),
            id=_HOME_FOOTER_HELP,
        )
        yield _CommandStatusLine(self._views, self._on_change)


class DashboardApp(App[None]):
    """Present the configured Home focus or one shared task detail view."""

    ENABLE_COMMAND_PALETTE = False
    CSS = (
        POPUP_CONTROL_CSS
        + """
    Screen { color: $primary-text; }
    #focus-root {
        height: 100%; border: solid $neutral-border; padding: 0 1;
        background: $background; scrollbar-size-vertical: 0;
    }
    #focus-readouts {
        width: 100%; height: auto; padding: 0; background: $background;
        scrollbar-size-vertical: 0;
    }
    #header { height: auto; margin-bottom: 0; text-wrap: wrap; text-overflow: clip; }
    #usage { height: auto; margin-bottom: 0; }
    #resources { height: auto; margin-bottom: 1; }
    #resources-rule { margin: 0; color: $neutral-border; }
    #escalations {
        width: 100%; height: auto; max-height: 3; padding: 0 1; margin-bottom: 1;
        background: $surface;
    }
    #usage:focus, #resources:focus, #escalations:focus, #escalations.armed,
    #focus-main #task-detail-body:focus {
        background: $selected-surface;
    }
    #focus-main { height: 1fr; min-height: 3; }
    #focus-main.wide { layout: horizontal; }
    #focus-main.compact { layout: vertical; overflow-y: auto; scrollbar-size-vertical: 0; }
    #focus-main.wide #focus-task-list-column {
        width: 42%; min-width: 0; height: 100%; padding-right: 1;
    }
    #focus-main.wide #queue {
        width: 100%; min-width: 0; height: 100%;
    }
    #focus-main.wide #task-browser-active { height: 1fr; min-height: 4; }
    #focus-main.wide #task-browser-list { height: 1fr; min-height: 2; }
    #focus-main.wide #task-detail-panel {
        width: 58%; min-width: 0; height: 100%; padding-left: 2;
        border-left: solid $neutral-border;
    }
    #focus-main.wide #task-detail-body { height: 1fr; max-height: 1fr; }
    #focus-main.compact #focus-task-list-column { width: 100%; height: auto; }
    #focus-main.compact #queue { width: 100%; height: auto; }
    #focus-main.compact #task-browser-list { width: 100%; height: auto; min-height: 3; }
    #focus-main.compact #task-detail-panel { width: 100%; height: auto; }
    #focus-main.compact #task-detail-body { max-height: 12; }
    #task-view-root { height: 100%; }
    #task-view-root #task-detail-panel { height: 1fr; min-height: 0; }
    #task-view-root #task-detail-body { height: 1fr; min-height: 0; max-height: 1fr; }
    #focus-empty { height: 1fr; padding: 1; color: $secondary-text; }
    #focus-footer {
        layout: grid; grid-size: 3 0; grid-columns: auto auto auto; grid-gutter: 0 1;
        height: auto; min-height: 1; padding: 0; margin-top: 0; border-top: solid $neutral-border;
        background: $background; color: $secondary-text;
    }
    #focus-footer Static { width: auto; height: auto; padding: 0 1; color: $secondary-text; }
    #focus-footer Button {
        width: auto; min-width: 1; height: auto; min-height: 1; margin: 0; padding: 0;
        border: none; background: $background; color: $secondary-text;
        content-align: left top; text-align: left; text-wrap: wrap; text-style: none;
    }
    #focus-footer Button:hover { color: $steel-blue; text-style: none; }
    #focus-footer Button:focus { color: $primary-text; text-style: reverse; }
    #workspace-command-status {
        width: 100%; height: 1; min-width: 0; max-height: 1; padding: 0 1;
        border: none; background: $background; color: $secondary-text;
        content-align: right middle; text-align: right; text-wrap: nowrap;
    }
    #workspace-command-status:hover { color: $steel-blue; }
    #workspace-command-status:focus { color: $primary-text; text-style: reverse; }
    #focus-footer #workspace-command-status {
        width: 1fr; min-width: 0; padding: 0; text-wrap: nowrap;
    }
    #focus-footer.grouped #workspace-command-status {
        content-align: right middle; text-align: right;
    }
    """
    )

    def __init__(
        self,
        *,
        focus_task: str | None,
        views: DashboardViews,
        on_change: OnChangeRegistration,
        clock: Clock,
        motion_interval_seconds: float | None,
        resources_refresh_seconds: float,
        docker_refresh_seconds: float,
        disk_refresh_seconds: float,
        quota_warn_pct: int,
        quota_alert_pct: int,
        animations: bool = True,
        open_palette: OpenPalette,
        widget_names: Sequence[str],
        open_palette_context: OpenPaletteContext | None = None,
        resolution_screen: ResolutionScreenFactory | None = None,
        navigate_task_window: NavigateTaskWindow | None = None,
        navigate_role_pane: NavigateRolePane | None = None,
        navigate_home: NavigateHome | None = None,
        timeline_refresh_seconds: float = DEFAULT_TASK_TIMELINE_REFRESH_SECONDS,
        now_max_lines: int = DEFAULT_NOW_MAX_LINES,
        now_compact_lines: int = DEFAULT_NOW_COMPACT_LINES,
        tmux_detach_hint: str | None = None,
        detach_tmux_client: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """Build the home or task workspace with the configured widgets."""
        super().__init__()
        install_theme(self)
        self._focus_task = focus_task
        self._views = views
        self._on_change = on_change
        self._clock = clock
        self._motion_interval_seconds = motion_interval_seconds
        self._animations = animations
        self._resources_refresh_seconds = resources_refresh_seconds
        self._docker_refresh_seconds = docker_refresh_seconds
        self._disk_refresh_seconds = disk_refresh_seconds
        self._timeline_refresh_seconds = timeline_refresh_seconds
        self._now_max_lines = now_max_lines
        self._now_compact_lines = now_compact_lines
        self._quota_warn_pct = quota_warn_pct
        self._quota_alert_pct = quota_alert_pct
        self._open_palette = open_palette
        self._open_palette_context = open_palette_context
        self._resolution_screen = resolution_screen
        self._palette_drafts: dict[tuple[str, str, str | None], PaletteDraft] = {}
        self._selected_task_id = focus_task
        self._navigate_task_window = navigate_task_window
        self._navigate_role_pane = navigate_role_pane
        self._navigate_home = navigate_home
        self._tmux_detach_hint = tmux_detach_hint
        self._detach_tmux_client = detach_tmux_client
        self._configured_widget_names = tuple(widget_names)
        self._widget_names = self._configured_widget_names
        self._motion_callbacks: list[_MotionCallback] = []
        self._home_detail_panel: TaskDetailPanel | None = None
        self._widget_plugins: dict[str, DashboardWidget] = {}
        self._widget_placements: dict[str, WidgetPlacement] = {}
        self._widget_names_by_placement: dict[WidgetPlacement, list[str]] = {}
        self._widgets_configured = False
        self._widget_instances: dict[str, Widget] = {}

    def compose(self) -> ComposeResult:
        """Mount configured primary content and keep other widgets behind contextual views."""
        self._configure_widgets()
        if self._focus_task is None:
            yield from self._compose_focus()
        else:
            yield from self._compose_task_workspace()

    def _configure_widgets(self) -> None:
        if self._widgets_configured:
            return
        for name in self._configured_widget_names:
            if name in self._widget_plugins:
                message = f"configured dashboard widget {name!r} appears more than once"
                raise WidgetPluginError(message)
            plugin = _load_widget(name)
            placement = _placement_for_widget(name, plugin)
            self._widget_plugins[name] = plugin
            self._widget_placements[name] = placement
            self._widget_names_by_placement.setdefault(placement, []).append(name)
        self._widget_names = self._configured_widget_names
        self._widgets_configured = True

    def _compose_focus(self) -> ComposeResult:
        context = self._widget_context()
        with VerticalScroll(id="focus-root", can_focus=False):
            with VerticalScroll(id="focus-readouts", can_focus=False):
                for placement in (
                    WidgetPlacement.IDENTITY,
                    WidgetPlacement.PROJECT_USAGE,
                    WidgetPlacement.RESOURCES,
                ):
                    for name in self._widget_names_by_placement.get(placement, ()):
                        if placement is WidgetPlacement.RESOURCES:
                            yield Rule(id=_RESOURCES_RULE)
                        widget = self._create_widget(name)
                        self._widget_instances[name] = widget
                        yield widget
            attention_names = self._widget_names_by_placement.get(WidgetPlacement.ATTENTION, ())
            if attention_names:
                for name in attention_names:
                    widget = self._create_widget(name)
                    self._widget_instances[name] = widget
                    yield widget
            else:
                yield _HomeAttentionStatus(context)
            with Horizontal(id="focus-main"):
                task_list_names = self._widget_names_by_placement.get(WidgetPlacement.TASK_LIST, ())
                if task_list_names:
                    with Vertical(id="focus-task-list-column"):
                        for name in task_list_names:
                            widget = self._create_widget(name)
                            self._widget_instances[name] = widget
                            yield widget
                    self._home_detail_panel = TaskDetailPanel(
                        context,
                        self._selected_task_id,
                        fixed=False,
                    )
                    yield self._home_detail_panel
                else:
                    yield Static("Task list is not configured.", id="focus-empty", markup=False)
            yield _HomeFooter(self._tmux_detach_hint, self._views, self._on_change)

    def _compose_task_workspace(self) -> ComposeResult:
        context = self._widget_context()
        with Vertical(id="task-view-root"):
            yield TaskDetailPanel(
                context,
                self._focus_task,
                fixed=True,
            )
            yield _CommandStatusLine(self._views, self._on_change)

    def _create_widget(
        self,
        name: str,
        *,
        placement: WidgetPlacement | None = None,
    ) -> Widget:
        plugin = self._widget_plugins[name]
        selected_placement = placement or self._widget_placements[name]
        try:
            widget = cast("object", plugin.create(self._widget_context(selected_placement)))
        except Exception as error:
            message = f"configured dashboard widget {name!r} failed to create: {error}"
            raise WidgetPluginError(message) from error
        if not isinstance(widget, Widget):
            message = (
                f"configured dashboard widget {name!r} returned {type(widget).__name__}, "
                "expected a Textual Widget"
            )
            raise WidgetPluginError(message)
        return widget

    def _widget_context(
        self,
        placement: WidgetPlacement = WidgetPlacement.EXTENSIONS,
    ) -> WidgetContext:
        async def open_task_detail(task_id: str) -> None:
            await self._open_task_detail(task_id)

        async def open_task_detail_view(task_id: str, view_id: str) -> None:
            await self.open_task_detail_view(task_id, view_id)

        return WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            disk_refresh_seconds=self._disk_refresh_seconds,
            timeline_refresh_seconds=self._timeline_refresh_seconds,
            now_max_lines=self._now_max_lines,
            now_compact_lines=self._now_compact_lines,
            quota_warn_pct=self._quota_warn_pct,
            quota_alert_pct=self._quota_alert_pct,
            clock=self._clock,
            animations=self._animations,
            on_change=self._on_change,
            on_motion=self.register_motion_callback,
            open_palette=self._open_palette,
            open_palette_context=self._open_contextual_palette,
            open_resolution=self.open_resolution,
            open_task_detail=open_task_detail,
            open_task_detail_view=open_task_detail_view,
            navigate_task_window=self._navigate_task_window,
            navigate_role_pane=self._navigate_role_pane,
            navigate_home=self._navigate_home,
            on_task_selected=self._select_task_context,
            activate_home_summary=self._activate_home_summary,
            activate_attention_decision=self._activate_attention_decision,
            placement=placement,
            home_attention_present=self._focus_task is None,
            open_contextual_view=self._open_contextual_view,
            task_contextual_views=tuple(
                (choice.label, choice.choice_id) for choice in self._task_context_choices()
            ),
        )

    async def _activate_home_summary(self, summary: str) -> None:
        if self._focus_task is not None:
            return
        if summary == _SUMMARY_ATTENTION:
            panel = self._widget_instances.get(_WIDGET_ESCALATIONS)
            if panel is None:
                self._show_navigation_feedback("Attention is not configured.")
                return
            attention_panel = cast("_AttentionPanelNavigation", panel)
            attention_panel.activate_attention()
            return
        task_filter = {
            _SUMMARY_RUNNING: TaskBrowserFilter.ACTIVE,
            _SUMMARY_WAITING: TaskBrowserFilter.WAITING,
        }.get(summary)
        if task_filter is None:
            message = f"unknown home summary {summary!r}"
            raise ValueError(message)
        browser = self._widget_instances.get(_WIDGET_QUEUE)
        if browser is None:
            self._show_navigation_feedback("Task browser is not configured.")
            return
        await cast("_TaskBrowserNavigation", browser).activate_filter(task_filter)

    async def _activate_attention_decision(self) -> bool:
        if self._focus_task is not None:
            return False
        panel = self._widget_instances.get(_WIDGET_ESCALATIONS)
        if panel is None or not widget_is_current(panel):
            return False
        return await cast("_AttentionPanelNavigation", panel).activate_selected_decision()

    def _show_navigation_feedback(self, message: str) -> None:
        self.query_one(f"#{_HOME_FOOTER_COMMAND_STATUS}", _CommandStatusLine).show_transient(
            message
        )

    def _context_choices(self) -> tuple[_ContextChoice, ...]:
        choices = [
            _ContextChoice(
                FOCUS_OPERATOR_ACTIONS,
                "Operator actions",
                operator_actions=True,
            ),
            _ContextChoice(FOCUS_CONTEXT_HELP, "Help", context_help=True),
        ]
        if _WIDGET_QUEUE in self._widget_plugins:
            choices.extend(
                _ContextChoice(
                    f"{FOCUS_TASK_FILTER_PREFIX}{task_filter.value}",
                    f"Task filter · {task_filter.value}",
                    task_filter=task_filter,
                )
                for task_filter in TaskBrowserFilter
            )
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None and getattr(queue, "selected_task_missing", False):
                choices.append(
                    _ContextChoice(
                        FOCUS_REVEAL_TASK,
                        "Reveal selected task in its matching filter",
                        reveal_task=True,
                    )
                )
        choices.append(
            _ContextChoice(
                FOCUS_COMMAND_HISTORY,
                "Command history · pending, applied and rejected",
                command_history=True,
            )
        )
        if _WIDGET_ESCALATIONS in self._widget_plugins:
            choices.append(
                _ContextChoice(
                    FOCUS_ATTENTION,
                    "Attention · decisions, stalls and faults",
                    attention=True,
                )
            )
        for name in self._configured_widget_names:
            placement = self._widget_placements[name]
            if placement is WidgetPlacement.PROJECT_USAGE:
                choices.append(
                    _ContextChoice(
                        FOCUS_USAGE_DETAILS,
                        "Usage details",
                        title=_PROJECT_USAGE_TITLE,
                        widget_name=name,
                        placement=WidgetPlacement.DETAILED_USAGE,
                    )
                )
            elif placement in {
                WidgetPlacement.ACTIVITY,
                WidgetPlacement.DETAILED_USAGE,
                WidgetPlacement.DIAGNOSTICS,
                WidgetPlacement.WORKFLOW,
                WidgetPlacement.EXTENSIONS,
            }:
                label = _contextual_widget_label(name, placement)
                choices.append(
                    _ContextChoice(
                        f"widget-{name}",
                        label,
                        widget_name=name,
                        placement=placement,
                    )
                )
        return tuple(choices)

    def _task_context_choices(self) -> tuple[_ContextChoice, ...]:
        choices = [
            _ContextChoice(
                FOCUS_COMMAND_HISTORY,
                "Command history · pending, applied and rejected",
                command_history=True,
            )
        ]
        for name in self._configured_widget_names:
            placement = self._widget_placements[name]
            if placement is WidgetPlacement.PROJECT_USAGE:
                choices.append(
                    _ContextChoice(
                        FOCUS_USAGE_DETAILS,
                        _PROJECT_USAGE_TITLE,
                        widget_name=name,
                        placement=WidgetPlacement.DETAILED_USAGE,
                    )
                )
            else:
                choices.append(
                    _ContextChoice(
                        f"widget-{name}",
                        _contextual_widget_label(name, placement),
                        widget_name=name,
                        placement=placement,
                    )
                )
        return tuple(choices)

    async def _open_contextual_view(self, choice_id: str) -> None:
        if choice_id == EMPTY_CONTEXT_CHOICE:
            return
        if choice_id == FOCUS_PROJECT_DIAGNOSTICS_VIEW:
            await self._show_project_diagnostics()
            return
        choice = self._find_context_choice(choice_id)
        if choice is None and choice_id == FOCUS_FAULTS_VIEW:
            self._show_navigation_feedback("Faults not configured")
            history = self._find_context_choice(FOCUS_COMMAND_HISTORY)
            if history is not None:
                await self._run_context_choice(history)
            return
        if choice is None:
            message = f"unknown contextual dashboard view {choice_id!r}"
            raise ValueError(message)
        await self._run_context_choice(choice)

    async def _show_project_diagnostics(self) -> None:
        panel = ProjectDiagnosticsPanel(
            self._views,
            self._on_change,
            tuple(
                self._create_widget(name, placement=WidgetPlacement.DIAGNOSTICS)
                for name in self._widget_names_by_placement.get(WidgetPlacement.DIAGNOSTICS, ())
            ),
        )
        await self.push_screen(
            _ContextWidgetScreen("Project diagnostics", panel),
            callback=lambda _: self.refresh_task_detail_after_resume(),
        )

    async def _open_contextual_palette(self, request: PaletteRequest) -> None:
        callback = self._open_palette_context
        if callback is None:
            await self._open_palette(request.action or "")
            return
        drafts = tuple(
            draft
            for (task_id, _, _), draft in self._palette_drafts.items()
            if task_id == request.task_id
        )
        if drafts:
            request = replace(request, drafts=drafts)
        await callback(request)

    async def open_resolution(self, escalation_id: str) -> None:
        """Open the resolution screen for one escalation."""
        factory = self._resolution_screen
        if factory is None:
            message = "the escalation resolution screen is not configured"
            raise ConfigError(message)
        await self.push_screen(
            factory(escalation_id),
            callback=lambda _: self.refresh_task_detail_after_resume(),
        )

    def remember_contextual_palette_draft(self, draft: PaletteDraft) -> None:
        """Keep the latest contextual form draft in memory while its caller is visible."""
        key = (draft.task_id, draft.action, draft.role)
        self._palette_drafts.pop(key, None)
        self._palette_drafts[key] = draft
        if len(self._palette_drafts) > _MAX_CONTEXTUAL_PALETTE_DRAFTS:
            self._palette_drafts.pop(next(iter(self._palette_drafts)))

    def route_task_palette(self, task_id: str, route: TaskPaletteRoute) -> None:
        """Open a task detail destination selected from its contextual action surface."""
        self.run_worker(
            self._open_task_palette_route(task_id, route),
            group=TASK_PALETTE_ROUTE_WORKER,
        )

    def palette_return_callback(
        self, task_id: str | None
    ) -> Callable[[TaskPaletteRoute | None], None]:
        """Return from a palette to its task context and optional secondary route."""

        def on_return(route: TaskPaletteRoute | None) -> None:
            self.refresh_task_detail_after_resume()
            if route is not None and task_id is not None:
                self.route_task_palette(task_id, route)

        return on_return

    async def _open_task_palette_route(self, task_id: str, route: TaskPaletteRoute) -> None:
        if self._focus_task is not None and self._focus_task != task_id:
            return
        if self._focus_task is None:
            self._select_task_context(task_id)
        if route is TaskPaletteRoute.INSPECT:
            await self._open_task_detail(task_id)
            return
        if route is TaskPaletteRoute.EVIDENCE:
            await self.action_open_task_evidence()
            return
        if route is TaskPaletteRoute.USAGE:
            await self.action_open_task_usage()
            return
        if route is TaskPaletteRoute.COMMANDS:
            await self.push_screen(
                _CommandHistoryScreen(self._views, self._on_change),
                callback=lambda _: self.refresh_task_detail_after_resume(),
            )
        elif route is TaskPaletteRoute.DIAGNOSTICS:
            await self._open_contextual_view(FOCUS_PROJECT_DIAGNOSTICS_VIEW)
        else:
            panel = self._context_task_detail_panel()
            if (
                panel is None
                and self._home_detail_panel is not None
                and self._home_detail_panel.display
            ):
                panel = self._home_detail_panel
            if panel is not None:
                await panel.open_contextual_views()
            else:
                self.push_screen(
                    TaskDetailScreen(
                        self._widget_context(),
                        task_id,
                        open_views_on_mount=True,
                    ),
                    callback=lambda _: self.refresh_task_detail_after_resume(),
                )

    def _find_context_choice(self, choice_id: str) -> _ContextChoice | None:
        return next(
            (
                item
                for item in (*self._context_choices(), *self._task_context_choices())
                if item.choice_id == choice_id
            ),
            None,
        )

    async def _run_context_choice(self, choice: _ContextChoice) -> None:
        if await self._run_special_context_choice(choice):
            return
        if choice.task_filter is not None:
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None:
                await cast("_TaskBrowserNavigation", queue).activate_filter(choice.task_filter)
            return
        if choice.command_history:
            await self.push_screen(
                _CommandHistoryScreen(self._views, self._on_change),
                callback=lambda _: self.refresh_task_detail_after_resume(),
            )
            return
        if choice.attention:
            panel = self._widget_instances.get(_WIDGET_ESCALATIONS)
            if panel is not None:
                cast("_AttentionPanelNavigation", panel).activate_attention()
            return
        if choice.reveal_task:
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None:
                await cast("_TaskBrowserNavigation", queue).reveal_selected_task()
            return
        await self._show_context_widget(choice)

    async def _open_task_detail(
        self,
        task_id: str,
        *,
        initial_view: str = TASK_VIEW_SUMMARY,
    ) -> None:
        def refresh_resumed_detail(_: None) -> None:
            self.refresh_task_detail_after_resume()

        await self.push_screen(
            TaskDetailScreen(
                self._widget_context(),
                task_id,
                initial_view=initial_view,
            ),
            callback=refresh_resumed_detail,
        )

    async def _run_special_context_choice(self, choice: _ContextChoice) -> bool:
        if choice.operator_actions:
            await self.action_open_palette()
            return True
        if choice.context_help:
            if self._home_detail_panel is not None and self._selected_task_id is not None:
                self._home_detail_panel.select_task(self._selected_task_id)
                await self._home_detail_panel.open_help()
            else:
                await self.action_show_help()
            return True
        return False

    async def _show_context_widget(self, choice: _ContextChoice) -> None:
        if choice.widget_name is None or choice.placement is None:
            message = f"contextual dashboard view {choice.choice_id!r} has no target"
            raise WidgetPluginError(message)
        widget = self._create_widget(choice.widget_name, placement=choice.placement)
        await self.push_screen(
            _ContextWidgetScreen(choice.title or choice.label, widget),
            callback=lambda _: self.refresh_task_detail_after_resume(),
        )

    def _context_view_selected(self, choice_id: str | None) -> None:
        self.refresh_task_detail_after_resume()
        if choice_id is not None and choice_id != EMPTY_CONTEXT_CHOICE:
            self.run_worker(
                self._open_contextual_view(choice_id),
                group=FOCUS_CONTEXT_VIEW_WORKER,
            )

    def _select_task_context(self, task_id: str | None) -> None:
        self._selected_task_id = task_id
        if self._home_detail_panel is not None:
            self._home_detail_panel.select_task(task_id)
        queue = self._widget_instances.get(_WIDGET_QUEUE)
        if queue is not None:
            cast("_TaskBrowserNavigation", queue).sync_context_selection(task_id)
        attention = self._widget_instances.get(_WIDGET_ESCALATIONS)
        if attention is not None:
            cast("_AttentionPanelNavigation", attention).follow_task(task_id)

    def _clear_home_selection(self) -> None:
        queue = self._widget_instances.get(_WIDGET_QUEUE)
        if queue is None:
            self._select_task_context(None)
            return
        browser = cast("_TaskBrowserNavigation", queue)
        browser.clear_selection()
        browser.focus_task_results()

    def get_default_screen(self) -> Screen[object]:
        return TabStopScreen()

    async def on_mount(self) -> None:
        """Focus the task browser or task view and start signal-driven refresh."""
        self._apply_layout(self.size.width, self.size.height)
        if self._focus_task is None:
            task_list = self.query("#task-browser-list")
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None:
                cast("_TaskBrowserNavigation", queue).focus_task_results()
            elif task_list:
                self.set_focus(task_list.first())
            else:
                self.set_focus(self.query_one(f"#{_HOME_FOOTER_ACTIONS}", Button))
        else:
            self.set_focus(self.query_one("#task-detail-body"))
        if self._motion_interval_seconds is not None:
            self.run_worker(self._motion_loop(), group="dashboard-motion", exclusive=True)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width, event.size.height)

    def refresh_layout(self) -> None:
        """Recalculate Home row allocations after a footer status becomes visible or hidden."""
        if not self.is_running or self._focus_task is not None:
            return
        if any(
            not self.query(selector)
            for selector in (
                "#focus-main",
                "#focus-readouts",
                "#focus-footer",
                "#workspace-command-status",
            )
        ):
            return
        self._apply_layout(self.size.width, self.size.height)

    def refresh_task_detail_after_resume(self) -> None:
        """Refresh task detail mounted in the screen that resumed after an overlay."""
        if not self.is_running:
            return
        if self._focus_task is None:
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None:
                cast("_TaskBrowserNavigation", queue).refresh_after_resume()
        panels = self.screen.query(TaskDetailPanel)
        if panels:
            panels.first().refresh_after_resume()

    async def on_key(self, event: events.Key) -> None:
        task_detail_screen = isinstance(self.screen, TaskDetailScreen)
        if event.key == TASK_DETAIL_ESCAPE and self._focused_select_is_expanded():
            return
        if event.key == _DASHBOARD_PALETTE_EVENT_KEY:
            if isinstance(self.screen, ModalScreen) and not task_detail_screen:
                return
            await self.action_open_palette()
            event.stop()
            event.prevent_default()
            return
        if (native_editor_has_focus(self.focused) and event.key != TASK_DETAIL_ESCAPE) or (
            isinstance(self.screen, ModalScreen) and not task_detail_screen
        ):
            return
        if await self._move_task_selection(event.key):
            event.stop()
            event.prevent_default()
            return
        if await self._dispatch_workspace_key(event):
            event.stop()
            if event.key in {_ACTIONS_KEY, TASK_DETAIL_ESCAPE}:
                event.prevent_default()

    async def _move_task_selection(self, key: str) -> bool:
        if (
            key not in _TASK_MOVE_STEPS
            or isinstance(self.screen, ModalScreen)
            or isinstance(self.focused, _ARROW_KEY_OWNERS)
        ):
            return False
        queue = self._widget_instances.get(_WIDGET_QUEUE)
        if queue is None:
            return False
        await cast("_TaskBrowserNavigation", queue).move_selection(_TASK_MOVE_STEPS[key])
        return True

    def _focused_select_is_expanded(self) -> bool:
        focused = self.focused
        while focused is not None:
            if isinstance(focused, Select):
                return focused.expanded
            focused = focused.parent
        return False

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == _HOME_FOOTER_CLEAR:
            self._clear_home_selection()
        elif event.button.id == _HOME_FOOTER_ACTIONS:
            await self.action_open_palette()
        elif event.button.id == _HOME_FOOTER_DETACH and self._detach_tmux_client is not None:
            if not await self._detach_tmux_client():
                self._show_navigation_feedback("No project tmux client is currently showing Home.")
        elif event.button.id == _HOME_FOOTER_HELP:
            await self.action_show_help()
        elif event.button.id == _HOME_FOOTER_COMMAND_STATUS:
            await self.push_screen(
                _CommandHistoryScreen(self._views, self._on_change),
                callback=lambda _: self.refresh_task_detail_after_resume(),
            )

    async def _dispatch_workspace_key(self, event: events.Key) -> bool:
        if event.key == _ACTIONS_KEY:
            await self.action_open_palette()
        elif event.key == TASK_DETAIL_ESCAPE:
            await self.action_return_from_section()
        elif event.key == TASK_DETAIL_HELP_KEY:
            await self.action_show_help()
        elif event.key == TASK_DETAIL_VIEWS_KEY:
            panel = self._context_task_detail_panel()
            if panel is not None:
                await panel.open_contextual_views()
            else:
                await self.action_open_views()
        else:
            return self._open_workspace_search(event.key)
        return True

    def _open_workspace_search(self, key: str) -> bool:
        if (
            key != _SEARCH_KEY
            or self._focus_task is not None
            or _WIDGET_QUEUE not in self._widget_names
        ):
            return False
        queue = self._widget_instances.get(_WIDGET_QUEUE)
        open_search = getattr(queue, "open_search", None)
        if not callable(open_search):
            return False
        open_search()
        return True

    async def _motion_loop(self) -> None:
        interval = self._motion_interval_seconds
        if interval is None:
            return
        while True:
            await self._clock.sleep(interval)
            tick = self._clock.now()
            for registration in tuple(self._motion_callbacks):
                if registration.in_flight:
                    continue
                with pinned_tick(tick):
                    registration.task = asyncio.ensure_future(registration.callback())
                registration.task.add_done_callback(self._surface_motion_callback_error)

    def _surface_motion_callback_error(self, task: asyncio.Future[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if isinstance(error, Exception):
            self._handle_exception(error)

    def register_motion_callback(self, callback: OnChange) -> Callable[[], None]:
        """Register a motion callback and return its cancellation subscription."""
        registration = _MotionCallback(callback)
        self._motion_callbacks.append(registration)

        def unsubscribe() -> None:
            self._motion_callbacks = [
                item for item in self._motion_callbacks if item is not registration
            ]
            registration.cancel()

        return unsubscribe

    def on_unmount(self) -> None:
        for registration in self._motion_callbacks:
            registration.cancel()
        self._motion_callbacks.clear()

    def _apply_layout(self, width: int, height: int) -> None:
        if self._focus_task is None:
            main = self.query_one("#focus-main", Horizontal)
            was_wide = main.has_class("wide")
            wide = width >= HOME_SPLIT_MIN_COLUMNS
            main.set_class(wide, "wide")
            main.set_class(not wide, "compact")
            if self._home_detail_panel is not None:
                self._home_detail_panel.set_class(not wide, COMPACT_INSPECTOR_CLASS)
            readouts = self.query_one("#focus-readouts", VerticalScroll)
            task_list_configured = bool(
                self._widget_names_by_placement.get(WidgetPlacement.TASK_LIST)
            )
            minimum_main_height = (
                FOCUS_MIN_TASK_LIST_ROWS
                if wide and task_list_configured
                else FOCUS_COMPACT_SHORT_MAIN_ROWS
                if not wide and FOCUS_FOOTER_MIN_HEIGHT <= height <= FOCUS_COMPACT_SHORT_HOME_HEIGHT
                else FOCUS_MAIN_MIN_ROWS
                if FOCUS_FOOTER_MIN_HEIGHT <= height <= FOCUS_SHORT_HOME_HEIGHT
                else FOCUS_COMPACT_MAIN_ROWS
                if not wide and task_list_configured
                else FOCUS_MAIN_MIN_ROWS
            )
            main.styles.min_height = minimum_main_height
            footer = self.query_one("#focus-footer", _HomeFooter)
            footer.display = height >= FOCUS_FOOTER_MIN_HEIGHT
            footer_columns = (
                FOCUS_FOOTER_STACKED_COLUMN_COUNT
                if width < FOCUS_FOOTER_GROUP_MIN_COLUMNS
                else FOCUS_FOOTER_ACTION_COUNT + 1
                if wide
                else FOCUS_FOOTER_GROUP_COUNT
            )
            footer.styles.grid_size_columns = footer_columns
            footer.set_class(footer_columns != FOCUS_FOOTER_STACKED_COLUMN_COUNT, "grouped")
            footer.styles.grid_columns = (
                "auto auto auto auto auto 1fr"
                if wide
                else f"{FOCUS_FOOTER_COMPACT_CONTROL_COLUMN_WIDTH} 1fr"
                if width < FOCUS_FOOTER_STATUS_WIDE_LABEL_MIN_COLUMNS
                and footer_columns == FOCUS_FOOTER_GROUP_COUNT
                else "auto 1fr"
                if footer_columns == FOCUS_FOOTER_GROUP_COUNT
                else "1fr"
            )
            status_visible = self.query_one(
                f"#{_HOME_FOOTER_COMMAND_STATUS}", _CommandStatusLine
            ).display
            footer_items = FOCUS_FOOTER_ACTION_COUNT + int(status_visible)
            footer_rows = (footer_items + footer_columns - 1) // footer_columns
            if (
                width < FOCUS_FOOTER_STATUS_WIDE_LABEL_MIN_COLUMNS
                and footer_columns == FOCUS_FOOTER_GROUP_COUNT
            ):
                footer_rows += FOCUS_FOOTER_COMPACT_WRAP_ROWS
            readout_main_height = (
                minimum_main_height - FOCUS_TASK_LIST_RECENT_RESERVE_ROWS
                if wide and task_list_configured
                else minimum_main_height
            )
            readouts.styles.max_height = (
                None
                if not wide and height < FOCUS_FOOTER_MIN_HEIGHT
                else max(
                    1,
                    min(
                        FOCUS_SUMMARY_MAX_ROWS,
                        height
                        - readout_main_height
                        - footer_rows
                        - FOCUS_ATTENTION_HEIGHT
                        - FOCUS_FRAME_BORDER_ROWS,
                    ),
                )
            )
            task_list = self.query("#task-browser-list")
            if task_list:
                task_list.first().styles.height = None if wide else FOCUS_COMPACT_TASK_LIST_ROWS
            if self._home_detail_panel is not None:
                self._home_detail_panel.display = True
                self._home_detail_panel.styles.height = "100%" if wide else "auto"
                if wide != was_wide:
                    self._home_detail_panel.refresh_after_resume()
            return
        return

    async def action_return_from_section(self) -> None:
        """Return from task-scoped details or the fixed task view."""
        if isinstance(self.screen, TaskDetailScreen):
            self.screen.dismiss()
            return
        if self._focus_task is None and not isinstance(self.screen, TaskDetailScreen):
            panel = self._context_task_detail_panel()
            if panel is not None and panel.active_view != TASK_VIEW_SUMMARY:
                panel.close_contextual_view()
                return
            self._clear_home_selection()
            return
        panel = self._context_task_detail_panel()
        if panel is None:
            return
        if panel.active_view != TASK_VIEW_SUMMARY:
            panel.close_contextual_view()
            return
        await panel.return_from_detail()

    async def action_show_help(self) -> None:
        """Show the bindings and current home or task workspace context."""
        panel = self._context_task_detail_panel()
        if panel is not None:
            await panel.open_help()
            return
        if self._focus_task is None:
            await self.push_screen(
                _WorkspaceHelpScreen(
                    None,
                    self._widget_names,
                    self._tmux_detach_hint,
                ),
                callback=lambda _: self.refresh_task_detail_after_resume(),
            )
            return
        await self.push_screen(
            _WorkspaceHelpScreen(
                self._focus_task,
                self._widget_names,
                self._tmux_detach_hint,
            ),
            callback=lambda _: self.refresh_task_detail_after_resume(),
        )

    async def open_task_detail_help(self, task_id: str) -> None:
        """Open help with the selected task and current detail context."""
        await self.push_screen(
            _WorkspaceHelpScreen(
                task_id,
                self._widget_names,
                self._tmux_detach_hint,
            ),
            callback=lambda _: self.refresh_task_detail_after_resume(),
        )

    async def action_open_views(self) -> None:
        """Open configured filters, history and secondary dashboard widgets."""
        if self._focus_task is not None:
            await self.query_one("#task-detail-panel", TaskDetailPanel).open_contextual_views()
            return
        self.push_screen(
            _ContextViewsScreen(self._context_choices()),
            callback=self._context_view_selected,
        )

    async def action_open_palette(self) -> None:
        """Open the operator palette through the shared dashboard callback."""
        panel = self._context_task_detail_panel()
        if panel is not None:
            await panel.open_palette()
            return
        if self._focus_task is not None:
            await self.query_one("#task-detail-panel", TaskDetailPanel).open_palette()
            return
        if self._home_detail_panel is not None and self._selected_task_id is not None:
            self._home_detail_panel.select_task(self._selected_task_id)
            await self._home_detail_panel.open_palette()
            return
        if self._open_palette_context is not None:
            await self._open_contextual_palette(PaletteRequest(task_id=self._selected_task_id))
            return
        await self._open_palette("")

    async def action_open_task_usage(self) -> None:
        """Open recorded usage details for the selected task or task view."""
        panel = self._context_task_detail_panel()
        task_id = panel.task_id if panel is not None else self._selected_task_id
        if task_id is None:
            return
        await self.open_task_detail_view(task_id, TASK_VIEW_USAGE)

    async def action_open_task_evidence(self) -> None:
        """Open retained evidence for the selected task or task view."""
        panel = self._context_task_detail_panel()
        task_id = panel.task_id if panel is not None else self._selected_task_id
        if task_id is None:
            return
        await self.open_task_detail_view(task_id, TASK_VIEW_EVIDENCE)

    async def open_task_detail_view(self, task_id: str, view_id: str) -> None:
        """Open a task detail view over Home or switch views in the active task popup."""
        panel = self._context_task_detail_panel()
        if (
            isinstance(self.screen, TaskDetailScreen)
            and panel is not None
            and panel.task_id == task_id
        ):
            panel.open_view(view_id)
            return
        await self._open_task_detail(task_id, initial_view=view_id)

    def _context_task_detail_panel(self) -> TaskDetailPanel | None:
        if isinstance(self.screen, TaskDetailScreen):
            return self.screen.query_one("#task-detail-panel", TaskDetailPanel)
        if self._focus_task is not None:
            return self.query_one("#task-detail-panel", TaskDetailPanel)
        panel = self._home_detail_panel
        focused = self.focused
        if (
            panel is not None
            and focused is not None
            and (focused is panel or panel in focused.ancestors)
        ):
            return panel
        return None
