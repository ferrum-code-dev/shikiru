"""The `dash --home | --task T-n` Textual workspace shell (06 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from textual.app import App
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import (
    DEFAULT_TASK_TIMELINE_REFRESH_SECONDS,
    WIDGET_ENTRY_POINT_GROUP,
    CommandFeedbackEntryView,
    CommandFeedbackStatus,
    CommandHistoryView,
    PaletteRequest,
    TaskBrowserFilter,
    WidgetContext,
    WidgetPlacement,
)
from shikiru.dashboard.detail import TaskDetailPanel, TaskDetailScreen
from shikiru.errors import ConfigError
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from shikiru.clock import Clock
    from shikiru.dashboard.contract import (
        DashboardViews,
        DashboardWidget,
        NavigateHome,
        NavigateRolePane,
        NavigateTaskWindow,
        OnChange,
        OnChangeRegistration,
        OpenPalette,
        OpenPaletteContext,
    )

_WIDGET_PLUGIN_KIND = "dashboard widget plugin"
_SECTION_WORKSPACE = "workspace"
_SECTION_ACTIVITY = "activity"
_SECTION_USAGE = "usage"
_SECTION_DIAGNOSTICS = "diagnostics"
_SECTION_EXTENSIONS = "extensions"
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
_SECTION_LABELS = (
    (_SECTION_WORKSPACE, "Workspace"),
    (_SECTION_ACTIVITY, "Activity"),
    (_SECTION_USAGE, "Usage"),
    (_SECTION_DIAGNOSTICS, "Diagnostics"),
    (_SECTION_EXTENSIONS, "Extensions"),
)
_SECTION_BY_ID = dict(_SECTION_LABELS)
_TAB_PREFIX = "section-tab-"
_SECTION_PREFIX = "section-"
_SECTION_HEADING_PREFIX = "section-heading-"
_EXPAND_BUTTON = "workspace-expand"
_RETURN_BUTTON = "workspace-return"
_HELP_BUTTON = "workspace-help"
_COMMAND_HISTORY_BUTTON = "workspace-command-history"
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
_COMMAND_HISTORY_PAGE_SIZE = 20
_COMMAND_HISTORY_ALL = "all"
_COMMAND_STATUS_LINE_MAX_CHARS = 96
_COMMAND_HISTORY_PREVIOUS_KEY = "left"
_COMMAND_HISTORY_NEXT_KEY = "right"
COMMAND_ACTION_SAY = "Say"
_SEARCH_KEY = "slash"
_SUMMARY_ATTENTION = "attention"
_SUMMARY_RUNNING = "running"
_SUMMARY_WAITING = "waiting"
HOME_SPLIT_MIN_COLUMNS = 100
SHORT_PANE_MAX_ROWS = 18
VERY_SMALL_MAX_COLUMNS = 23
VERY_SMALL_MAX_ROWS = 9
FOOTER_COMPACT_MAX_COLUMNS = 40
COMPACT_FOOTER_HEIGHT = 4
COMPACT_FOOTER_CONTROLS_HEIGHT = 2
WORKSPACE_NAVIGATION_HEIGHT = 3
VERY_SMALL_NAVIGATION_HEIGHT = 1
FOCUS_SUMMARY_MAX_ROWS = 12
FOCUS_SHORT_SUMMARY_MAX_ROWS = 3
FOCUS_MAIN_MIN_ROWS = 3
FOCUS_MIN_TASK_LIST_ROWS = 9
FOCUS_COMMAND_STATUS_ROWS = 1
FOCUS_READOUT_BORDER_ROWS = 1
FOCUS_FOOTER_HEIGHT = 2
FOCUS_VIEWS_BUTTON = "focus-views"
FOCUS_HELP_BUTTON = "focus-help"
FOCUS_HINTS = "focus-hints"
FOCUS_VIEWS_LIST = "focus-views-list"
FOCUS_VIEW_PREFIX = "focus-view-"
FOCUS_COMMAND_HISTORY = "commands"
FOCUS_ATTENTION = "attention"
FOCUS_REVEAL_TASK = "reveal-task"
FOCUS_USAGE_DETAILS = "usage"
FOCUS_TASK_FILTER_PREFIX = "filter:"
FOCUS_CONTEXT_VIEW_WORKER = "focus-context-view"
EMPTY_CONTEXT_CHOICE = ""


class _TaskBrowserNavigation(Protocol):
    async def activate_filter(self, task_filter: TaskBrowserFilter) -> None: ...

    async def reveal_selected_task(self) -> None: ...


class _AttentionPanelNavigation(Protocol):
    def activate_attention(self) -> None: ...


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


def _legacy_section_for_placement(placement: WidgetPlacement) -> str:
    if placement in {
        WidgetPlacement.IDENTITY,
        WidgetPlacement.TASK_LIST,
        WidgetPlacement.ATTENTION,
        WidgetPlacement.RESOURCES,
        WidgetPlacement.WORKFLOW,
    }:
        return _SECTION_WORKSPACE
    if placement is WidgetPlacement.ACTIVITY:
        return _SECTION_ACTIVITY
    if placement in {WidgetPlacement.PROJECT_USAGE, WidgetPlacement.DETAILED_USAGE}:
        return _SECTION_USAGE
    if placement is WidgetPlacement.DIAGNOSTICS:
        return _SECTION_DIAGNOSTICS
    return _SECTION_EXTENSIONS


def _contextual_widget_label(name: str, placement: WidgetPlacement) -> str:
    category = {
        WidgetPlacement.ACTIVITY: "Activity",
        WidgetPlacement.DETAILED_USAGE: "Usage",
        WidgetPlacement.DIAGNOSTICS: "Diagnostics",
        WidgetPlacement.WORKFLOW: "Workflow",
        WidgetPlacement.EXTENSIONS: "Extensions",
    }.get(placement)
    label = name.replace("_", " ").title()
    return f"{category} · {label}" if category is not None else label


@dataclass(frozen=True)
class _ContextChoice:
    choice_id: str
    label: str
    task_filter: TaskBrowserFilter | None = None
    widget_name: str | None = None
    placement: WidgetPlacement | None = None
    command_history: bool = False
    attention: bool = False
    reveal_task: bool = False


class _ContextViewsScreen(ModalScreen[str]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Back")]
    DEFAULT_CSS = """
    _ContextViewsScreen { align: center middle; background: $background; }
    #context-views-frame {
        width: 60%; max-width: 76; height: 70%; max-height: 24;
        padding: 1; border: solid $neutral-border; background: $surface;
    }
    #context-views-title { height: 1; color: $primary-text; text-style: bold; }
    #focus-views-list { height: 1fr; border: none; background: $surface; }
    #context-views-hint { height: 1; color: $secondary-text; }
    """

    def __init__(self, choices: tuple[_ContextChoice, ...]) -> None:
        super().__init__()
        self._choices = {
            f"{FOCUS_VIEW_PREFIX}{index}": choice for index, choice in enumerate(choices)
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="context-views-frame"):
            yield Label("Views and filters", id="context-views-title")
            items = [
                ListItem(Label(choice.label), id=choice_id)
                for choice_id, choice in self._choices.items()
            ]
            yield ListView(*items, id=FOCUS_VIEWS_LIST)
            yield Static("↑↓ select · Enter open · Esc back", id="context-views-hint")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        choice_id = event.item.id
        if choice_id is not None and choice_id in self._choices:
            self.dismiss(self._choices[choice_id].choice_id)

    async def action_close(self) -> None:
        self.dismiss("")


class _ContextWidgetScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Back")]
    DEFAULT_CSS = """
    _ContextWidgetScreen { background: $background; }
    #context-widget-frame { height: 100%; padding: 0 1; }
    #context-widget-title {
        height: 1; padding: 0 1; color: $primary-text;
        border-bottom: solid $neutral-border;
    }
    #context-widget-content { height: 1fr; padding: 1 0; }
    #context-widget-hint { height: 1; color: $secondary-text; }
    """

    def __init__(self, label: str, widget: Widget) -> None:
        super().__init__()
        self._label = label
        self._widget = widget

    def compose(self) -> ComposeResult:
        with Vertical(id="context-widget-frame"):
            yield Label(self._label, id="context-widget-title")
            with VerticalScroll(id="context-widget-content"):
                yield self._widget
            yield Static("Esc back to Focus", id="context-widget-hint")

    async def action_close(self) -> None:
        self.dismiss()


def _command_history_text(history: CommandHistoryView) -> str:
    if not history.entries:
        return "No commands recorded."
    return "\n".join(
        _command_entry_text(entry, engine_up=history.engine_up) for entry in history.entries
    )


def _command_entry_text(entry: CommandFeedbackEntryView, *, engine_up: bool) -> str:
    if entry.status is CommandFeedbackStatus.PENDING:
        engine_state = "running" if engine_up else "stopped"
        detail = f"engine currently {engine_state}; original acknowledgement wait context unknown"
    elif entry.status is CommandFeedbackStatus.APPLIED:
        detail = (
            "guidance queued for the selected role's next turn"
            if entry.action == COMMAND_ACTION_SAY
            else "engine applied the command effect"
        )
        if entry.result is not None:
            detail = f"{detail}; returned {entry.result}"
    else:
        detail = (
            f"returned {entry.result}"
            if entry.result is not None
            else "engine rejected the command without a returned result"
        )
    return (
        f"#{entry.command_id} · {entry.status.value} · {entry.action} · {entry.target} · {detail}"
    )


def _command_summary_text(history: CommandHistoryView) -> str:
    entry = history.entries[0]
    engine = (
        f" · engine {'running' if history.engine_up else 'stopped'}"
        if entry.status is CommandFeedbackStatus.PENDING
        else ""
    )
    summary = (
        f"#{entry.command_id} · {entry.status.value} · {entry.action} · {entry.target}{engine}"
    )
    return summary[:_COMMAND_STATUS_LINE_MAX_CHARS]


class _WorkspaceHelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    _WorkspaceHelpScreen { align: center middle; }
    #workspace-help-frame {
        width: 90%;
        height: 90%;
        max-width: 76;
        border: round $neutral-border;
        padding: 0 1;
        background: $surface;
    }
    #workspace-help-context { height: 1; }
    #workspace-help-scroll { height: 1fr; }
    #workspace-help-copy { height: auto; color: $primary-text; }
    #workspace-help-close { height: 3; }
    """

    def __init__(
        self,
        current_section: str,
        task_id: str | None,
        expanded: bool,
        widget_names: Sequence[str],
        *,
        compact_focus: bool = False,
    ) -> None:
        super().__init__()
        self._current_section = current_section
        self._task_id = task_id
        self._expanded = expanded
        self._widget_names = tuple(widget_names)
        self._compact_focus = compact_focus

    def compose(self) -> ComposeResult:
        """Render help for the current workspace section and its available keys."""
        view = f"Task workspace {self._task_id}" if self._task_id else "Home workspace"
        if self._compact_focus:
            lines = [
                "Up/Down: select a task",
                "Enter: inspect selected task",
                "Escape: return from detail or close this view",
                "Ctrl+P: open contextual operator actions",
                "/: search task ID or title",
                "v: open filters, history and configured secondary views",
                "Mouse: select or open a task; Views and Help are clickable",
                "?: show this help",
            ]
        else:
            lines = [
                "Tab / Shift+Tab: move between sections",
                "Mouse: choose a section",
                "z: expand the focused section",
                "Escape returns to the section list."
                if self._expanded
                else "Escape closes this help.",
                "Ctrl+P: open operator actions",
                "?: show this help",
            ]
        if _WIDGET_ESCALATIONS in self._widget_names:
            lines.append("Escalations: Up/Down select, o opens evidence, r opens resolution")
        if _WIDGET_DIALOGUE in self._widget_names:
            lines.append("Dialogue: Enter opens full text")
        if _WIDGET_QUEUE in self._widget_names:
            lines.append(
                "Task browser: / search, Up/Down select, v opens filters, Escape clears search"
                if self._compact_focus
                else "Task browser: / search, Up/Down select, filters and Clear search"
            )
        with Vertical(id="workspace-help-frame"):
            yield Label(f"Help · {view} · {self._current_section}", id=_HELP_CONTEXT)
            with VerticalScroll(id="workspace-help-scroll"):
                yield Static("\n".join(lines), id="workspace-help-copy", markup=False)
            yield Button("Close help", id=_HELP_CLOSE_BUTTON)

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
        (_COMMAND_HISTORY_PREVIOUS_KEY, "previous_page", "Newer history"),
        (_COMMAND_HISTORY_NEXT_KEY, "next_page", "Older history"),
    ]
    DEFAULT_CSS = """
    _CommandHistoryScreen { align: center middle; }
    #command-history-frame {
        width: 90%; height: 85%; max-width: 100;
        border: round $neutral-border; padding: 1 2;
        background: $surface; color: $primary-text;
    }
    #command-history-filter { width: 100%; height: 3; }
    #command-history-scroll { height: 1fr; border: round $neutral-border; }
    #command-history-copy { height: auto; padding: 1; }
    #command-history-page { height: 1; }
    #command-history-controls { height: 3; width: 100%; }
    """

    def __init__(self, views: DashboardViews, on_change: OnChangeRegistration) -> None:
        super().__init__()
        self._views = views
        self._on_change = on_change
        self._offset = 0
        self._status: CommandFeedbackStatus | None = None
        self._has_more = False
        self._change_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="command-history-frame"):
            yield Label("Command history · left/right or page controls")
            yield Select(
                (
                    ("All statuses", _COMMAND_HISTORY_ALL),
                    *((status.value, status.value) for status in CommandFeedbackStatus),
                ),
                value=_COMMAND_HISTORY_ALL,
                id=_COMMAND_HISTORY_FILTER,
            )
            with VerticalScroll(id=_COMMAND_HISTORY_SCROLL):
                yield Static("Loading command history…", id=_COMMAND_HISTORY_COPY, markup=False)
            yield Label("", id=_COMMAND_HISTORY_PAGE)
            with HorizontalScroll(id="command-history-controls"):
                yield Button("Newer", id=_COMMAND_HISTORY_PREVIOUS, disabled=True)
                yield Button("Older", id=_COMMAND_HISTORY_NEXT, disabled=True)
                yield Button("Back", id=_COMMAND_HISTORY_CLOSE)

    async def on_mount(self) -> None:
        self._display_history()
        self._change_worker = self.run_worker(
            self._on_change(self._refresh), group=_COMMAND_HISTORY_WORKER
        )

    def on_unmount(self) -> None:
        if self._change_worker is not None:
            self._change_worker.cancel()

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
        self._offset = max(0, self._offset - _COMMAND_HISTORY_PAGE_SIZE)
        self._display_history()

    def action_next_page(self) -> None:
        if self._has_more:
            self._offset += _COMMAND_HISTORY_PAGE_SIZE
            self._display_history()

    async def _refresh(self) -> None:
        if self.is_mounted:
            self._display_history(preserve_scroll=True)

    def _display_history(self, *, preserve_scroll: bool = False) -> None:
        scroll = self.query_one(f"#{_COMMAND_HISTORY_SCROLL}", VerticalScroll)
        scroll_y = scroll.scroll_y if preserve_scroll and self.is_mounted else 0
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
        self.query_one(f"#{_COMMAND_HISTORY_PAGE}", Label).update(
            f"{filter_name} · page {page_number} · {history.limit} per page · "
            f"rows {start}-{end} of {history.total_count}"
        )
        self.query_one(f"#{_COMMAND_HISTORY_PREVIOUS}", Button).disabled = history.offset == 0
        self.query_one(f"#{_COMMAND_HISTORY_NEXT}", Button).disabled = not history.has_more
        if preserve_scroll:
            self.call_after_refresh(lambda: scroll.scroll_to(y=scroll_y, animate=False))


class _CommandStatusLine(Static):
    def __init__(self, views: DashboardViews, on_change: OnChangeRegistration) -> None:
        super().__init__("", id="workspace-command-status", markup=False)
        self._views = views
        self._on_change = on_change

    async def on_mount(self) -> None:
        self._display_history()
        self.run_worker(self._on_change(self._refresh), group=_COMMAND_HISTORY_WORKER)

    async def _refresh(self) -> None:
        if self.is_mounted:
            self._display_history()

    def _display_history(self) -> None:
        history = self._views.command_history()
        self.display = bool(history.entries)
        if history.entries:
            self.update(_command_summary_text(history))


class DashboardApp(App[None]):
    """Group configured widgets into navigable sections for one system pane."""

    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+p", "open_palette", "Palette"),
        ("v", "open_views", "Views"),
        ("z", "expand_section", "Expand section"),
        ("escape", "return_from_section", "Back"),
        ("question_mark", "show_help", "Help"),
    ]
    CSS = """
    Screen { background: $background; color: $primary-text; }
    #focus-root { height: 100%; }
    #focus-readouts {
        height: auto; padding: 0 1;
        border-bottom: solid $neutral-border; background: $background;
    }
    #header { height: auto; padding: 1 1 0 1; }
    #usage { height: auto; padding: 0 1; }
    #resources { height: auto; padding: 0 1; }
    #escalations { height: auto; padding: 0 1; }
    #focus-main { height: 1fr; min-height: 3; padding: 0 1; }
    #focus-main.wide { layout: horizontal; }
    #focus-main.compact { layout: vertical; overflow-y: auto; }
    #focus-main.wide #queue {
        width: 42; min-width: 30; height: 100%; padding-right: 1;
        border-right: solid $neutral-border;
    }
    #focus-main.wide #task-browser-list { height: 1fr; min-height: 3; }
    #focus-main.wide #task-detail-panel {
        width: 1fr; min-width: 45; height: 100%; padding-left: 1;
        border-left: solid $neutral-border;
    }
    #focus-main.wide #task-detail-body { height: 1fr; max-height: 1fr; }
    #focus-main.compact #queue { width: 100%; height: auto; }
    #focus-main.compact #task-browser-list { height: auto; min-height: 3; }
    #focus-main.compact #task-detail-panel { width: 100%; height: auto; }
    #focus-main.compact #task-detail-body { max-height: 12; }
    #focus-empty { height: 1fr; padding: 1; color: $secondary-text; }
    #focus-footer {
        height: 1; padding: 0 1; border-top: solid $neutral-border;
        background: $panel;
    }
    #focus-hints { width: 1fr; height: 1; color: $secondary-text; }
    #focus-footer Button {
        width: auto; min-width: 1; height: 1; margin: 0 1 0 0; padding: 0;
        border: none; background: $panel; color: $steel-blue;
    }
    #focus-footer Button:focus { text-style: bold reverse; }
    #workspace-command-status { height: 1; padding: 0 1; color: $secondary-text; }
    #workspace-root { height: 100%; }
    #workspace-header {
        height: auto;
        padding: 0 1;
        border-bottom: solid $neutral-border;
        background: $surface;
    }
    #workspace-title { text-style: bold; color: $primary-text; }
    #workspace-command-status { height: 1; padding: 0 1; overflow: hidden; color: $secondary-text; }
    #workspace-navigation { height: 3; padding: 0 1; background: $surface; }
    #workspace-navigation Button, #workspace-footer Button {
        width: auto;
        min-width: 1;
        margin-right: 1;
        border: round $neutral-border;
        background: $surface;
        color: $muted-text;
    }
    #workspace-navigation Button:focus, #workspace-footer Button:focus {
        border: round $focus;
        color: $primary-text;
        text-style: bold reverse;
    }
    #dashboard-panels { height: 1fr; padding: 0 1; background: $background; }
    .workspace-section {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border: round $neutral-border;
        background: $surface;
    }
    .workspace-section-heading {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $primary-text;
        text-style: bold;
    }
    .workspace-section-content { height: auto; padding: 0 1; }
    #workspace-footer {
        height: 4;
        padding: 0 1;
        border-top: solid $neutral-border;
        background: $panel;
    }
    #workspace-footer-copy { width: 100%; height: 1; color: $primary-text; }
    #workspace-footer-controls { width: 100%; height: 2; }
    #workspace-footer-controls Button { height: 2; border: none; padding: 0 1; }
    #workspace-footer-controls Button:focus { border: none; text-style: bold reverse; }
    #workspace-return { display: none; }
    #workspace-root.expanded #workspace-navigation { display: none; }
    #workspace-root.expanded #workspace-expand { display: none; }
    #workspace-root.expanded #workspace-return { display: block; }
    #workspace-root.pane-tiny #workspace-command-status { display: none; }
    #workspace-root.pane-tiny #workspace-header { height: 1; padding: 0; }
    #workspace-root.pane-tiny #workspace-navigation {
        height: 1; padding: 0;
    }
    #workspace-root.pane-tiny #workspace-navigation Button {
        height: 1; border: none; padding: 0 1;
    }
    #workspace-root.pane-tiny #dashboard-panels { padding: 0; }
    #home-browser-detail { width: 100%; height: auto; }
    #home-browser-detail.wide { layout: horizontal; }
    #home-browser-detail.compact { layout: vertical; }
    #home-browser-detail.wide #queue { width: 40; min-width: 30; height: 100%; }
    #home-browser-detail.wide #task-browser-list { height: 1fr; min-height: 3; }
    #home-browser-detail.wide #task-browser-preview,
    #home-browser-detail.wide #task-browser-reveal { display: none; }
    #home-browser-detail.wide #task-detail-panel {
        width: 1fr; height: 100%; min-width: 45;
        border-left: solid $neutral-border;
    }
    #home-browser-detail.wide #task-detail-body { height: 1fr; max-height: 1fr; }
    #home-browser-detail.compact #queue { width: 100%; height: auto; }
    #home-browser-detail.compact #task-detail-panel { width: 100%; }
    #home-browser-detail.compact #task-detail-body { max-height: 12; }
    #workspace-root.pane-short #home-browser-detail.compact #task-browser-list {
        height: 6; min-height: 3;
    }
    #workspace-root.pane-tiny #home-browser-detail.compact #task-browser-list {
        height: 3; min-height: 2;
    }
    #workspace-root.pane-tiny #workspace-navigation Button { min-width: 1; }
    #workspace-root.pane-short #workspace-footer { height: 4; }
    #workspace-root.pane-short #workspace-footer-controls { height: 2; }
    """

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
        open_palette: OpenPalette,
        widget_names: Sequence[str],
        open_palette_context: OpenPaletteContext | None = None,
        navigate_task_window: NavigateTaskWindow | None = None,
        navigate_role_pane: NavigateRolePane | None = None,
        navigate_home: NavigateHome | None = None,
        timeline_refresh_seconds: float = DEFAULT_TASK_TIMELINE_REFRESH_SECONDS,
    ) -> None:
        """Build the home or task workspace with the configured widgets."""
        super().__init__()
        install_theme(self)
        self._focus_task = focus_task
        self._views = views
        self._on_change = on_change
        self._clock = clock
        self._motion_interval_seconds = motion_interval_seconds
        self._resources_refresh_seconds = resources_refresh_seconds
        self._docker_refresh_seconds = docker_refresh_seconds
        self._timeline_refresh_seconds = timeline_refresh_seconds
        self._open_palette = open_palette
        self._open_palette_context = open_palette_context
        self._selected_task_id = focus_task
        self._navigate_task_window = navigate_task_window
        self._navigate_role_pane = navigate_role_pane
        self._navigate_home = navigate_home
        self._configured_widget_names = tuple(widget_names)
        self._widget_names = self._configured_widget_names
        self._motion_callbacks: list[OnChange] = []
        self._home_detail_panel: TaskDetailPanel | None = None
        self._widget_plugins: dict[str, DashboardWidget] = {}
        self._widget_placements: dict[str, WidgetPlacement] = {}
        self._widget_names_by_placement: dict[WidgetPlacement, list[str]] = {}
        self._widgets_configured = False
        self._widget_names_by_section: dict[str, list[str]] = {
            section_id: [] for section_id, _ in _SECTION_LABELS
        }
        self._widget_instances: dict[str, Widget] = {}
        self._section_ids: tuple[str, ...] = ()
        self._selected_section = _SECTION_WORKSPACE
        self._expanded_section: str | None = None
        self._return_focus: Widget | None = None
        self._return_scroll_position = (0, 0)

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
        if self._focus_task is None:
            self._section_ids = ()
            self._widget_names = self._configured_widget_names
        else:
            for name in self._configured_widget_names:
                placement = self._widget_placements[name]
                if placement not in _TASK_VIEW_OMITTED_PLACEMENTS:
                    self._widget_names_by_section[_legacy_section_for_placement(placement)].append(
                        name
                    )
            self._widget_names = tuple(
                name
                for section_widgets in self._widget_names_by_section.values()
                for name in section_widgets
            )
            self._section_ids = tuple(
                section_id
                for section_id, _ in _SECTION_LABELS
                if self._widget_names_by_section[section_id] or section_id == _SECTION_WORKSPACE
            )
        self._selected_section = self._section_ids[0] if self._section_ids else _SECTION_WORKSPACE
        self._widgets_configured = True

    def _compose_focus(self) -> ComposeResult:
        context = self._widget_context()
        with Vertical(id="focus-root"):
            with VerticalScroll(id="focus-readouts"):
                for placement in (
                    WidgetPlacement.IDENTITY,
                    WidgetPlacement.PROJECT_USAGE,
                    WidgetPlacement.RESOURCES,
                    WidgetPlacement.ATTENTION,
                ):
                    for name in self._widget_names_by_placement.get(placement, ()):
                        widget = self._create_widget(name)
                        self._widget_instances[name] = widget
                        yield widget
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
                        embedded=True,
                    )
                    yield self._home_detail_panel
                else:
                    yield Static("Task list is not configured.", id="focus-empty", markup=False)
            yield _CommandStatusLine(self._views, self._on_change)
            with Horizontal(id="focus-footer"):
                yield Static(
                    "↑↓ select · Enter inspect · Ctrl+P actions · / search · v views · ? help",
                    id=FOCUS_HINTS,
                    markup=False,
                )
                yield Button("Views", id=FOCUS_VIEWS_BUTTON)
                yield Button("Help", id=FOCUS_HELP_BUTTON)

    def _compose_task_workspace(self) -> ComposeResult:
        context = self._widget_context()
        view_label = f"Task workspace · {self._focus_task}"
        with Vertical(id="workspace-root"):
            with Horizontal(id="workspace-header"):
                yield Label(f"Shikiru · {view_label}", id="workspace-title")
            yield _CommandStatusLine(self._views, self._on_change)
            with HorizontalScroll(id="workspace-navigation"):
                for section_id in self._section_ids:
                    yield Button(self._section_label(section_id), id=f"{_TAB_PREFIX}{section_id}")
            with VerticalScroll(id="dashboard-panels"):
                for section_id in self._section_ids:
                    with Vertical(id=f"section-{section_id}", classes="workspace-section"):
                        yield Label(
                            self._section_label(section_id),
                            id=f"{_SECTION_HEADING_PREFIX}{section_id}",
                            classes="workspace-section-heading",
                        )
                        with Vertical(classes="workspace-section-content"):
                            if section_id == _SECTION_WORKSPACE and self._focus_task is not None:
                                yield TaskDetailPanel(
                                    context,
                                    self._focus_task,
                                    fixed=True,
                                )
                            names = self._widget_names_by_section[section_id]
                            if not names and not (
                                section_id == _SECTION_WORKSPACE and self._focus_task is not None
                            ):
                                yield Static("No configured widgets.", markup=False)
                            for name in names:
                                placement = self._task_widget_placement(name)
                                widget = self._create_widget(name, placement=placement)
                                self._widget_instances[name] = widget
                                yield widget
            with Vertical(id="workspace-footer"):
                yield Static(self._footer_text(), id="workspace-footer-copy", markup=False)
                with HorizontalScroll(id="workspace-footer-controls"):
                    yield Button("Expand", id=_EXPAND_BUTTON)
                    yield Button("Back", id=_RETURN_BUTTON)
                    yield Button("Help", id=_HELP_BUTTON)
                    yield Button("Commands", id=_COMMAND_HISTORY_BUTTON)

    def _task_widget_placement(self, name: str) -> WidgetPlacement:
        placement = self._widget_placements[name]
        if placement is WidgetPlacement.PROJECT_USAGE:
            return WidgetPlacement.DETAILED_USAGE
        return placement

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
            def refresh_resumed_detail(_: None) -> None:
                self.refresh_task_detail_after_resume()

            await self.push_screen(
                TaskDetailScreen(self._widget_context(), task_id),
                callback=refresh_resumed_detail,
            )

        return WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            timeline_refresh_seconds=self._timeline_refresh_seconds,
            clock=self._clock,
            on_change=self._on_change,
            on_motion=self._register_motion_callback,
            open_palette=self._open_palette,
            open_palette_context=self._open_palette_context,
            open_task_detail=open_task_detail,
            navigate_task_window=self._navigate_task_window,
            navigate_role_pane=self._navigate_role_pane,
            navigate_home=self._navigate_home,
            on_task_selected=self._select_task_context,
            activate_home_summary=self._activate_home_summary,
            placement=placement,
            home_attention_present=(
                self._focus_task is None
                and bool(self._widget_names_by_placement.get(WidgetPlacement.ATTENTION))
            ),
            open_contextual_view=self._open_contextual_view,
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

    def _show_navigation_feedback(self, message: str) -> None:
        if self._focus_task is None:
            status = self.query_one("#workspace-command-status", Static)
        else:
            status = self.query_one("#workspace-footer-copy", Static)
        status.update(message)
        status.display = True

    def _context_choices(self) -> tuple[_ContextChoice, ...]:
        choices: list[_ContextChoice] = []
        if _WIDGET_QUEUE in self._widget_plugins:
            choices.extend(
                _ContextChoice(
                    f"{FOCUS_TASK_FILTER_PREFIX}{task_filter.value}",
                    f"Task filter · {task_filter.value.title()}",
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
                "Command history · Pending, Applied and Rejected",
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
                        "Usage details · subscriptions, costs, coverage and quota",
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

    async def _open_contextual_view(self, choice_id: str) -> None:
        if choice_id == EMPTY_CONTEXT_CHOICE:
            return
        choice = self._find_context_choice(choice_id)
        if choice is None:
            message = f"unknown contextual dashboard view {choice_id!r}"
            raise ValueError(message)
        await self._run_context_choice(choice)

    def _find_context_choice(self, choice_id: str) -> _ContextChoice | None:
        return next(
            (item for item in self._context_choices() if item.choice_id == choice_id),
            None,
        )

    async def _run_context_choice(self, choice: _ContextChoice) -> None:
        if choice.task_filter is not None:
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            if queue is not None:
                await cast("_TaskBrowserNavigation", queue).activate_filter(choice.task_filter)
            return
        if choice.command_history:
            await self.push_screen(_CommandHistoryScreen(self._views, self._on_change))
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

    async def _show_context_widget(self, choice: _ContextChoice) -> None:
        if choice.widget_name is None or choice.placement is None:
            message = f"contextual dashboard view {choice.choice_id!r} has no target"
            raise WidgetPluginError(message)
        widget = self._create_widget(choice.widget_name, placement=choice.placement)
        await self.push_screen(
            _ContextWidgetScreen(
                _contextual_widget_label(choice.widget_name, choice.placement),
                widget,
            )
        )

    def _context_view_selected(self, choice_id: str | None) -> None:
        if choice_id is not None and choice_id != EMPTY_CONTEXT_CHOICE:
            self.run_worker(
                self._open_contextual_view(choice_id),
                group=FOCUS_CONTEXT_VIEW_WORKER,
            )

    def _select_task_context(self, task_id: str | None) -> None:
        self._selected_task_id = task_id
        if self._home_detail_panel is not None:
            self._home_detail_panel.select_task(task_id)

    async def on_mount(self) -> None:
        """Focus the task browser or task view and start signal-driven refresh."""
        self._apply_layout(self.size.width, self.size.height)
        if self._focus_task is None:
            task_list = self.query("#task-browser-list")
            target = (
                task_list.first() if task_list else self.query_one(f"#{FOCUS_VIEWS_BUTTON}", Button)
            )
            self.set_focus(target)
        else:
            self.set_focus(self.query_one(f"#{_TAB_PREFIX}{self._selected_section}", Button))
        if self._motion_interval_seconds is not None:
            self.run_worker(self._motion_loop(), group="dashboard-motion", exclusive=True)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width, event.size.height)

    def refresh_task_detail_after_resume(self) -> None:
        """Refresh task detail mounted in the screen that resumed after an overlay."""
        if not self.is_running:
            return
        panels = self.screen.query(TaskDetailPanel)
        if panels:
            panels.first().refresh_after_resume()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Keep the visible section marker synchronized with keyboard focus."""
        if self._focus_task is None:
            return
        section_id = self._section_for_tab(event.widget.id)
        if section_id is not None:
            self._select_section(section_id)
            return
        section_id = self._section_for_widget(event.widget)
        if section_id is not None:
            self._select_section(section_id, scroll=False)

    def on_key(self, event: events.Key) -> None:
        if (
            event.key != _SEARCH_KEY
            or isinstance(self.focused, Input)
            or isinstance(self.screen, ModalScreen)
            or _WIDGET_QUEUE not in self._widget_names
        ):
            return
        if self._focus_task is None:
            queue = self._widget_instances.get(_WIDGET_QUEUE)
            open_search = getattr(queue, "open_search", None)
            if callable(open_search):
                open_search()
                event.stop()
            return
        if self._expanded_section is not None:
            self._return_to_sections()
        self._select_section(_SECTION_WORKSPACE)
        self.set_focus(self.query_one("#task-browser-search", Input))
        event.stop()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Apply navigation, expansion, return and help button activations."""
        button_id = event.button.id or ""
        if self._focus_task is None:
            if button_id == FOCUS_VIEWS_BUTTON:
                await self.action_open_views()
            elif button_id == FOCUS_HELP_BUTTON:
                await self.action_show_help()
            return
        section_id = self._section_for_tab(button_id)
        if section_id is not None:
            self._select_section(section_id)
        elif button_id == _EXPAND_BUTTON:
            self._expand_selected_section()
        elif button_id == _RETURN_BUTTON:
            self._return_to_sections()
        elif button_id == _HELP_BUTTON:
            await self.action_show_help()
        elif button_id == _COMMAND_HISTORY_BUTTON:
            await self.push_screen(_CommandHistoryScreen(self._views, self._on_change))

    async def _motion_loop(self) -> None:
        interval = self._motion_interval_seconds
        if interval is None:
            return
        while True:
            await self._clock.sleep(interval)
            for callback in tuple(self._motion_callbacks):
                await callback()

    def _register_motion_callback(self, callback: OnChange) -> Callable[[], None]:
        self._motion_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._motion_callbacks:
                self._motion_callbacks.remove(callback)

        return unsubscribe

    def _apply_layout(self, width: int, height: int) -> None:
        if self._focus_task is None:
            main = self.query_one("#focus-main", Horizontal)
            was_wide = main.has_class("wide")
            wide = width >= HOME_SPLIT_MIN_COLUMNS and height > SHORT_PANE_MAX_ROWS
            main.set_class(wide, "wide")
            main.set_class(not wide, "compact")
            readouts = self.query_one("#focus-readouts", VerticalScroll)
            self.query_one("#focus-footer").styles.height = FOCUS_FOOTER_HEIGHT
            if height <= SHORT_PANE_MAX_ROWS:
                readouts.styles.max_height = FOCUS_SHORT_SUMMARY_MAX_ROWS
            else:
                minimum_main_height = (
                    FOCUS_MIN_TASK_LIST_ROWS
                    if self._widget_names_by_placement.get(WidgetPlacement.TASK_LIST)
                    else FOCUS_MAIN_MIN_ROWS
                )
                readouts.styles.max_height = min(
                    FOCUS_SUMMARY_MAX_ROWS,
                    height
                    - minimum_main_height
                    - FOCUS_FOOTER_HEIGHT
                    - FOCUS_COMMAND_STATUS_ROWS
                    - FOCUS_READOUT_BORDER_ROWS,
                )
            task_list = self.query("#task-browser-list")
            if task_list:
                task_list.first().styles.height = "1fr" if wide else FOCUS_MIN_TASK_LIST_ROWS
            if self._home_detail_panel is not None:
                self._home_detail_panel.display = wide
                self._home_detail_panel.styles.height = "100%" if wide else "auto"
                if wide != was_wide:
                    self._home_detail_panel.refresh_after_resume()
            return
        root = self.query_one("#workspace-root")
        short = height <= SHORT_PANE_MAX_ROWS
        tiny = width <= VERY_SMALL_MAX_COLUMNS or height <= VERY_SMALL_MAX_ROWS
        wide = width >= HOME_SPLIT_MIN_COLUMNS and not short
        self.query_one("#workspace-footer").styles.height = COMPACT_FOOTER_HEIGHT
        self.query_one("#workspace-footer-controls").styles.height = COMPACT_FOOTER_CONTROLS_HEIGHT
        self.query_one("#workspace-navigation").styles.height = (
            VERY_SMALL_NAVIGATION_HEIGHT if tiny else WORKSPACE_NAVIGATION_HEIGHT
        )
        root.set_class(short, "pane-short")
        root.set_class(tiny, "pane-tiny")
        root.set_class(wide, "layout-wide")
        root.set_class(not wide, "layout-compact")
        commands = self.query_one(f"#{_COMMAND_HISTORY_BUTTON}", Button)
        commands.label = "Cmds" if width <= FOOTER_COMPACT_MAX_COLUMNS else "Commands"

    def _section_for_tab(self, widget_id: str | None) -> str | None:
        if widget_id is None or not widget_id.startswith(_TAB_PREFIX):
            return None
        section_id = widget_id.removeprefix(_TAB_PREFIX)
        return section_id if section_id in self._section_ids else None

    def _section_for_widget(self, widget: Widget | None) -> str | None:
        while widget is not None:
            widget_id = widget.id
            if widget_id is not None and widget_id.startswith(_SECTION_PREFIX):
                section_id = widget_id.removeprefix(_SECTION_PREFIX)
                if section_id in self._section_ids:
                    return section_id
            widget = cast("Widget | None", widget.parent)
        return None

    def _select_section(self, section_id: str, *, scroll: bool = True) -> None:
        if self._expanded_section is not None:
            return
        self._selected_section = section_id
        for candidate in self._section_ids:
            button = self.query_one(f"#{_TAB_PREFIX}{candidate}", Button)
            label = self._section_label(candidate)
            button.label = f"> {label}" if candidate == section_id else label
            heading = self.query_one(f"#{_SECTION_HEADING_PREFIX}{candidate}", Label)
            heading.update(f"> {label}" if candidate == section_id else label)
        self.query_one("#workspace-footer-copy", Static).update(self._footer_text())
        if scroll:
            section = self.query_one(f"#{_SECTION_PREFIX}{section_id}")
            self.query_one("#dashboard-panels", VerticalScroll).scroll_to_widget(
                section, animate=False, top=True
            )

    def _expand_selected_section(self) -> None:
        if self._expanded_section is not None:
            return
        self._return_focus = self.focused
        focused_section = self._section_for_widget(self._return_focus)
        if focused_section is not None:
            self._select_section(focused_section, scroll=False)
        panels = self.query_one("#dashboard-panels", VerticalScroll)
        self._return_scroll_position = (panels.scroll_x, panels.scroll_y)
        self._expanded_section = self._selected_section
        self.query_one("#workspace-root").add_class("expanded")
        for section_id in self._section_ids:
            self.query_one(f"#{_SECTION_PREFIX}{section_id}").display = (
                section_id == self._expanded_section
            )
        self.query_one("#workspace-footer-copy", Static).update(self._footer_text())
        if focused_section is not None and self._return_focus is not None:
            panels.scroll_to_widget(self._return_focus, animate=False)
        self.set_focus(self.query_one(f"#{_RETURN_BUTTON}", Button))

    def _return_to_sections(self) -> None:
        if self._expanded_section is None:
            return
        self.query_one("#workspace-root").remove_class("expanded")
        for section_id in self._section_ids:
            self.query_one(f"#{_SECTION_PREFIX}{section_id}").display = True
        self._expanded_section = None
        self.query_one("#workspace-footer-copy", Static).update(self._footer_text())
        panels = self.query_one("#dashboard-panels", VerticalScroll)
        panels.scroll_to(
            x=self._return_scroll_position[0],
            y=self._return_scroll_position[1],
            animate=False,
        )
        self.set_focus(self._return_focus)
        self._return_focus = None

    def _footer_text(self) -> str:
        label = self._section_label(self._selected_section)
        if self._expanded_section is not None:
            return f"{label} expanded"
        return f"> {label}"

    def _section_label(self, section_id: str) -> str:
        return _SECTION_BY_ID[section_id]

    async def action_expand_section(self) -> None:
        """Expand the focused section to the available body area."""
        if self._focus_task is None:
            return
        self._expand_selected_section()

    async def action_return_from_section(self) -> None:
        """Return from an expanded section and restore navigation focus."""
        self._return_to_sections()

    async def action_show_help(self) -> None:
        """Show the bindings and current home or task workspace context."""
        if self._focus_task is None:
            await self.push_screen(
                _WorkspaceHelpScreen(
                    "Focus",
                    None,
                    False,
                    self._widget_names,
                    compact_focus=True,
                )
            )
            return
        await self.push_screen(
            _WorkspaceHelpScreen(
                self._section_label(self._selected_section),
                self._focus_task,
                self._expanded_section is not None,
                self._widget_names_by_section[self._selected_section],
            )
        )

    async def action_open_views(self) -> None:
        """Open configured filters, history and secondary dashboard widgets."""
        if self._focus_task is not None:
            return
        self.push_screen(
            _ContextViewsScreen(self._context_choices()),
            callback=self._context_view_selected,
        )

    async def action_open_palette(self) -> None:
        """Open the operator palette through the shared dashboard callback."""
        if self._focus_task is not None:
            await self.query_one("#task-detail-panel", TaskDetailPanel).open_palette()
            return
        if self._open_palette_context is not None:
            task_id = self._focus_task or self._selected_task_id
            await self._open_palette_context(PaletteRequest(task_id=task_id))
            return
        await self._open_palette("")
