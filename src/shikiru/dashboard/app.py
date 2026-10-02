"""The `dash --home | --task T-n` Textual workspace shell (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import App
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, WidgetContext
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
    "queue": _SECTION_WORKSPACE,
    _WIDGET_DIALOGUE: _SECTION_ACTIVITY,
    "usage": _SECTION_USAGE,
    "resources": _SECTION_DIAGNOSTICS,
    "docker": _SECTION_DIAGNOSTICS,
}
_TAB_PREFIX = "section-tab-"
_EXPAND_BUTTON = "workspace-expand"
_RETURN_BUTTON = "workspace-return"
_HELP_BUTTON = "workspace-help"
_HELP_CLOSE_BUTTON = "workspace-help-close"


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
        with Vertical(id="workspace-help-frame"):
            yield Label(f"Help · {view} · {self._current_section}")
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
        height: auto;
        padding: 0 1;
        border-top: solid $neutral-border;
        background: $panel;
    }
    #workspace-footer-copy { width: 1fr; height: auto; color: $primary-text; }
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
        self._widget_names = tuple(widget_names)
        self._motion_callbacks: list[OnChange] = []
        self._widget_names_by_section: dict[str, list[str]] = {
            section_id: [] for section_id, _ in _SECTION_LABELS
        }
        for name in self._widget_names:
            self._widget_names_by_section[_section_for_widget(name)].append(name)
        self._section_ids = tuple(
            section_id
            for section_id, _ in _SECTION_LABELS
            if self._widget_names_by_section[section_id]
        ) or (_SECTION_WORKSPACE,)
        self._selected_section = self._section_ids[0]
        self._expanded_section: str | None = None
        self._return_focus: Widget | None = None
        self._return_scroll_position = (0, 0)

    def compose(self) -> ComposeResult:
        """Mount every configured widget within its documented workspace section."""
        context = WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            on_change=self._on_change,
            on_motion=self._motion_callbacks.append,
            open_palette=self._open_palette,
        )
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
                            classes="workspace-section-heading",
                        )
                        with Vertical(classes="workspace-section-content"):
                            names = self._widget_names_by_section[section_id]
                            if not names:
                                yield Static("No configured widgets.", markup=False)
                            for name in names:
                                yield _load_widget(name).create(context)
            with Horizontal(id="workspace-footer"):
                yield Static(self._footer_text(), id="workspace-footer-copy", markup=False)
                yield Button("Expand section", id=_EXPAND_BUTTON)
                yield Button("Return", id=_RETURN_BUTTON)
                yield Button("? Help", id=_HELP_BUTTON)

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

    def _select_section(self, section_id: str) -> None:
        if self._expanded_section is not None:
            return
        self._selected_section = section_id
        for candidate in self._section_ids:
            button = self.query_one(f"#{_TAB_PREFIX}{candidate}", Button)
            label = self._section_label(candidate)
            button.label = f"> {label}" if candidate == section_id else label
        self.query_one("#workspace-footer-copy", Static).update(self._footer_text())
        section = self.query_one(f"#section-{section_id}")
        self.query_one("#dashboard-panels", VerticalScroll).scroll_to_widget(
            section, animate=False, top=True
        )

    def _expand_selected_section(self) -> None:
        if self._expanded_section is not None:
            return
        self._return_focus = self.focused
        panels = self.query_one("#dashboard-panels", VerticalScroll)
        self._return_scroll_position = (panels.scroll_x, panels.scroll_y)
        self._expanded_section = self._selected_section
        self.query_one("#workspace-root").add_class("expanded")
        for section_id in self._section_ids:
            self.query_one(f"#section-{section_id}").display = section_id == self._expanded_section
        self.query_one("#workspace-footer-copy", Static).update(self._footer_text())
        self.set_focus(self.query_one(f"#{_RETURN_BUTTON}", Button))
        panels.scroll_to_widget(
            self.query_one(f"#section-{self._expanded_section}"), animate=False, top=True
        )

    def _return_to_sections(self) -> None:
        if self._expanded_section is None:
            return
        self.query_one("#workspace-root").remove_class("expanded")
        for section_id in self._section_ids:
            self.query_one(f"#section-{section_id}").display = True
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
            return f"Expanded section: {label} · Escape returns · ? opens help"
        return (
            f"> Focused section: {label} · Tab/Shift+Tab navigate · z expands"
            " · Ctrl+P actions · ? help"
        )

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
