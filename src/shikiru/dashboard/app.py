"""The `dash --home | --task T-n` Textual workspace shell (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from textual.app import App
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import (
    WIDGET_ENTRY_POINT_GROUP,
    CommandFeedbackEntryView,
    CommandFeedbackStatus,
    CommandHistoryView,
    PaletteRequest,
    TaskBrowserFilter,
    WidgetContext,
)
from shikiru.dashboard.detail import TaskDetailPanel, TaskDetailScreen
from shikiru.errors import ConfigError
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.widget import Widget
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
_TASK_VIEW_OMITTED_WIDGETS = frozenset(
    {"header", _WIDGET_ESCALATIONS, "strips", _WIDGET_QUEUE, "resources", "docker"}
)
_SECTION_LABELS = (
    (_SECTION_WORKSPACE, "Workspace"),
    (_SECTION_ACTIVITY, "Activity"),
    (_SECTION_USAGE, "Usage"),
    (_SECTION_DIAGNOSTICS, "Diagnostics"),
    (_SECTION_EXTENSIONS, "Extensions"),
)
_SECTION_BY_ID = dict(_SECTION_LABELS)
_BUILTIN_SECTIONS = {
    "header": _SECTION_WORKSPACE,
    _WIDGET_ESCALATIONS: _SECTION_WORKSPACE,
    "strips": _SECTION_WORKSPACE,
    _WIDGET_QUEUE: _SECTION_WORKSPACE,
    _WIDGET_DIALOGUE: _SECTION_ACTIVITY,
    "usage": _SECTION_USAGE,
    "resources": _SECTION_DIAGNOSTICS,
    "docker": _SECTION_DIAGNOSTICS,
}
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
_SUMMARY_BAR_HEIGHT = 3
_SUMMARY_BUTTONS = (
    (_SUMMARY_ATTENTION, "Attention", "workspace-summary-attention"),
    (_SUMMARY_RUNNING, "Running", "workspace-summary-running"),
    (_SUMMARY_WAITING, "Waiting", "workspace-summary-waiting"),
)
_SUMMARY_BY_BUTTON = {button_id: name for name, _, button_id in _SUMMARY_BUTTONS}


class _TaskBrowserNavigation(Protocol):
    async def activate_filter(self, task_filter: TaskBrowserFilter) -> None: ...


class _AttentionPanelNavigation(Protocol):
    def activate_attention(self) -> None: ...


class WorkspaceSummaryBar(Horizontal):
    """Show bounded home counts with direct navigation to each content list."""

    DEFAULT_CSS = f"""
    WorkspaceSummaryBar {{ height: {_SUMMARY_BAR_HEIGHT}; width: 100%; padding: 0 1; }}
    WorkspaceSummaryBar Button {{
        width: 1fr; min-width: 1; height: {_SUMMARY_BAR_HEIGHT}; padding: 0; margin: 0;
        border: round $neutral-border; background: $surface; color: $muted-text;
    }}
    WorkspaceSummaryBar Button:focus {{
        border: round $focus; color: $primary-text; text-style: bold reverse;
    }}
    """

    def __init__(self, context: WidgetContext) -> None:
        super().__init__(id="workspace-summary")
        self._widget_context = context

    def compose(self) -> ComposeResult:
        for _, label, button_id in _SUMMARY_BUTTONS:
            yield Button(label, id=button_id)

    async def on_mount(self) -> None:
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh_while_mounted), exclusive=True)
        self._widget_context.on_motion(self._refresh_while_mounted)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        name = _SUMMARY_BY_BUTTON.get(event.button.id or "")
        callback = self._widget_context.activate_home_summary
        if name is not None and callback is not None:
            await callback(name)

    async def _refresh(self) -> None:
        view = self._widget_context.views.workspace_summary()
        counts = {
            _SUMMARY_ATTENTION: view.attention,
            _SUMMARY_RUNNING: view.running,
            _SUMMARY_WAITING: view.waiting,
        }
        for name, label, button_id in _SUMMARY_BUTTONS:
            self.query_one(f"#{button_id}", Button).label = f"{label} {counts[name]}"

    async def _refresh_while_mounted(self) -> None:
        app = cast("App[None]", self.app)
        if app.is_running and self.is_mounted and self.is_on_screen and self.screen.is_active:
            await self._refresh()


class WidgetPluginError(ConfigError):
    """A configured dashboard widget name has no matching, loadable entry point."""


def _load_widget(name: str) -> DashboardWidget:
    loaded = load_unique_entry_point(
        WIDGET_ENTRY_POINT_GROUP, name, WidgetPluginError, _WIDGET_PLUGIN_KIND
    )
    widget_class = cast("type[DashboardWidget]", loaded)
    return widget_class()


def _section_for_widget(name: str) -> str:
    return _BUILTIN_SECTIONS.get(name, _SECTION_EXTENSIONS)


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
        height: auto;
        max-height: 90%;
        max-width: 76;
        border: round $neutral-border;
        padding: 1 2;
        background: $surface;
    }
    #workspace-help-copy { height: auto; color: $primary-text; }
    #workspace-help-close { margin-top: 1; }
    """

    def __init__(
        self,
        current_section: str,
        task_id: str | None,
        expanded: bool,
        widget_names: Sequence[str],
    ) -> None:
        super().__init__()
        self._current_section = current_section
        self._task_id = task_id
        self._expanded = expanded
        self._widget_names = tuple(widget_names)

    def compose(self) -> ComposeResult:
        """Render help for the current workspace section and its available keys."""
        view = f"Task workspace {self._task_id}" if self._task_id else "Home workspace"
        lines = [
            "Tab / Shift+Tab: move between sections",
            "Mouse: choose a section",
            "z: expand the focused section",
            "Escape returns to the section list." if self._expanded else "Escape closes this help.",
            "Ctrl+P: open operator actions",
            "?: show this help",
        ]
        if _WIDGET_ESCALATIONS in self._widget_names:
            lines.append("Escalations: Up/Down select, o opens evidence, r opens resolution")
        if _WIDGET_DIALOGUE in self._widget_names:
            lines.append("Dialogue: Enter opens full text")
        if _WIDGET_QUEUE in self._widget_names:
            lines.append("Task browser: / search, Up/Down select, filters and Clear search")
        with Vertical(id="workspace-help-frame"):
            yield Label(f"Help · {view} · {self._current_section}", id=_HELP_CONTEXT)
            yield Static(
                "\n".join(lines),
                id="workspace-help-copy",
                markup=False,
            )
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
        ("z", "expand_section", "Expand section"),
        ("escape", "return_from_section", "Back"),
        ("question_mark", "show_help", "Help"),
    ]
    CSS = """
    Screen { background: $background; color: $primary-text; }
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
        height: 5;
        padding: 0 1;
        border-top: solid $neutral-border;
        background: $panel;
    }
    #workspace-footer-copy { width: 100%; height: 1; color: $primary-text; }
    #workspace-footer-controls { width: 100%; height: 3; }
    #workspace-footer-controls Button { padding: 0 1; }
    #workspace-return { display: none; }
    #workspace-root.expanded #workspace-navigation { display: none; }
    #workspace-root.expanded #workspace-expand { display: none; }
    #workspace-root.expanded #workspace-return { display: block; }
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
        self._open_palette = open_palette
        self._open_palette_context = open_palette_context
        self._selected_task_id = focus_task
        self._navigate_task_window = navigate_task_window
        self._navigate_role_pane = navigate_role_pane
        self._navigate_home = navigate_home
        self._widget_names = tuple(widget_names)
        self._motion_callbacks: list[OnChange] = []
        self._widget_names_by_section: dict[str, list[str]] = {
            section_id: [] for section_id, _ in _SECTION_LABELS
        }
        for name in self._widget_names:
            self._widget_names_by_section[_section_for_widget(name)].append(name)
        if self._focus_task is not None:
            for section_widgets in self._widget_names_by_section.values():
                section_widgets[:] = [
                    name for name in section_widgets if name not in _TASK_VIEW_OMITTED_WIDGETS
                ]
        self._widget_names = tuple(
            name
            for section_widgets in self._widget_names_by_section.values()
            for name in section_widgets
        )
        self._widget_instances: dict[str, object] = {}
        self._section_ids = tuple(
            section_id
            for section_id, _ in _SECTION_LABELS
            if self._widget_names_by_section[section_id]
            or (self._focus_task is not None and section_id == _SECTION_WORKSPACE)
        ) or (_SECTION_WORKSPACE,)
        self._selected_section = self._section_ids[0]
        self._expanded_section: str | None = None
        self._return_focus: Widget | None = None
        self._return_scroll_position = (0, 0)

    def compose(self) -> ComposeResult:
        """Mount every configured widget within its documented workspace section."""
        context = self._widget_context()
        view_label = (
            f"Task workspace · {self._focus_task}"
            if self._focus_task is not None
            else "Home workspace"
        )
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
                            elif section_id == _SECTION_WORKSPACE:
                                yield WorkspaceSummaryBar(context)
                            names = self._widget_names_by_section[section_id]
                            if not names and not (
                                section_id == _SECTION_WORKSPACE and self._focus_task is not None
                            ):
                                yield Static("No configured widgets.", markup=False)
                            for name in names:
                                widget = _load_widget(name).create(context)
                                self._widget_instances[name] = widget
                                yield widget
            with Vertical(id="workspace-footer"):
                yield Static(self._footer_text(), id="workspace-footer-copy", markup=False)
                with HorizontalScroll(id="workspace-footer-controls"):
                    yield Button("Expand", id=_EXPAND_BUTTON)
                    yield Button("Back", id=_RETURN_BUTTON)
                    yield Button("Help", id=_HELP_BUTTON)
                    yield Button("Commands", id=_COMMAND_HISTORY_BUTTON)

    def _widget_context(self) -> WidgetContext:
        async def open_task_detail(task_id: str) -> None:
            self._select_task_context(task_id)
            await self.push_screen(TaskDetailScreen(self._widget_context(), task_id))

        return WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            on_change=self._on_change,
            on_motion=self._motion_callbacks.append,
            open_palette=self._open_palette,
            open_palette_context=self._open_palette_context,
            open_task_detail=open_task_detail,
            navigate_task_window=self._navigate_task_window,
            navigate_role_pane=self._navigate_role_pane,
            navigate_home=self._navigate_home,
            on_task_selected=self._select_task_context,
            activate_home_summary=self._activate_home_summary,
        )

    async def _activate_home_summary(self, summary: str) -> None:
        self._select_section(_SECTION_WORKSPACE)
        if summary == _SUMMARY_ATTENTION:
            panel = self._widget_instances.get(_WIDGET_ESCALATIONS)
            if panel is None:
                self.query_one("#workspace-footer-copy", Static).update(
                    "Attention content is not configured."
                )
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
            self.query_one("#workspace-footer-copy", Static).update(
                "Task browser content is not configured."
            )
            return
        await cast("_TaskBrowserNavigation", browser).activate_filter(task_filter)

    def _select_task_context(self, task_id: str | None) -> None:
        self._selected_task_id = task_id

    async def on_mount(self) -> None:
        """Focus section navigation and start the signal-driven motion refresh."""
        self.set_focus(self.query_one(f"#{_TAB_PREFIX}{self._selected_section}", Button))
        if self._motion_interval_seconds is not None:
            self.run_worker(self._motion_loop(), group="dashboard-motion", exclusive=True)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Keep the visible section marker synchronized with keyboard focus."""
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
        if self._expanded_section is not None:
            self._return_to_sections()
        self._select_section(_SECTION_WORKSPACE)
        self.set_focus(self.query_one("#task-browser-search", Input))
        event.stop()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Apply navigation, expansion, return and help button activations."""
        button_id = event.button.id or ""
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
        self._expand_selected_section()

    async def action_return_from_section(self) -> None:
        """Return from an expanded section and restore navigation focus."""
        self._return_to_sections()

    async def action_show_help(self) -> None:
        """Show the bindings and current home or task workspace context."""
        await self.push_screen(
            _WorkspaceHelpScreen(
                self._section_label(self._selected_section),
                self._focus_task,
                self._expanded_section is not None,
                self._widget_names_by_section[self._selected_section],
            )
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
