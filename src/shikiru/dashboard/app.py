"""The `dash --home | --task T-n` Textual workspace shell (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import App
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, WidgetContext
from shikiru.dashboard.detail import TaskDetailPanel, TaskDetailScreen
from shikiru.errors import ConfigError
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.widget import Widget

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
_HELP_CLOSE_BUTTON = "workspace-help-close"
_HELP_CONTEXT = "workspace-help-context"
_SEARCH_KEY = "slash"


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
                                yield _load_widget(name).create(context)
            with Vertical(id="workspace-footer"):
                yield Static(self._footer_text(), id="workspace-footer-copy", markup=False)
                with Horizontal(id="workspace-footer-controls"):
                    yield Button("Expand", id=_EXPAND_BUTTON)
                    yield Button("Back", id=_RETURN_BUTTON)
                    yield Button("Help", id=_HELP_BUTTON)

    def _widget_context(self) -> WidgetContext:
        async def open_task_detail(task_id: str) -> None:
            await self.push_screen(TaskDetailScreen(self._widget_context(), task_id))

        return WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            on_change=self._on_change,
            on_motion=self._motion_callbacks.append,
            open_palette=self._open_palette,
            open_task_detail=open_task_detail,
            navigate_task_window=self._navigate_task_window,
            navigate_role_pane=self._navigate_role_pane,
            navigate_home=self._navigate_home,
        )

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
        await self._open_palette("")
