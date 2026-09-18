"""The `dash --home | --task T-n` Textual app (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import App

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, WidgetContext
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from textual.app import ComposeResult

    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import DashboardWidget, WidgetServices
    from shikiru.store.reader import StateReader

_WIDGET_PLUGIN_KIND = "dashboard widget plugin"


class WidgetPluginError(ConfigError):
    """A configured dashboard widget name has no matching, loadable entry point."""


def _load_widget(name: str) -> DashboardWidget:
    loaded = load_unique_entry_point(
        WIDGET_ENTRY_POINT_GROUP, name, WidgetPluginError, _WIDGET_PLUGIN_KIND
    )
    widget_class = cast("type[DashboardWidget]", loaded)
    return widget_class()


class DashboardApp(App[None]):
    """Stacks `dashboard.widgets` panels in configured order for one system pane (06 §5.4)."""

    def __init__(
        self,
        *,
        state: StateReader,
        project_dir: Path,
        focus_task: str | None,
        services: WidgetServices,
        settings: Settings,
        widget_names: Sequence[str],
    ) -> None:
        """Build the system pane for `focus_task` (`None` means the home window)."""
        super().__init__()
        self._state = state
        self._project_dir = project_dir
        self._focus_task = focus_task
        self._services = services
        self._settings = settings
        self._widget_names = tuple(widget_names)

    def compose(self) -> ComposeResult:
        """Yield one mounted `Widget` per configured widget name, in order."""
        context = WidgetContext(
            state=self._state,
            project_dir=self._project_dir,
            focus_task=self._focus_task,
            services=self._services,
            settings=self._settings,
        )
        for name in self._widget_names:
            yield _load_widget(name).create(context)
