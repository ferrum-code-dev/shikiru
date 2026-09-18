"""The `dash --home | --task T-n` Textual app (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from textual.app import App

from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, WidgetContext
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from textual.app import ComposeResult

    from shikiru.dashboard.contract import DashboardWidget, WidgetServices
    from shikiru.store.reader import StateReader


class WidgetPluginError(ConfigError):
    """A configured dashboard widget name has no matching, loadable entry point."""


def _load_widget(name: str) -> DashboardWidget:
    matches = [ep for ep in entry_points(group=WIDGET_ENTRY_POINT_GROUP) if ep.name == name]
    if not matches:
        message = f"no dashboard widget plugin named {name!r} is registered"
        raise WidgetPluginError(message)
    try:
        widget_class = matches[0].load()
        return widget_class()
    except Exception as error:
        message = f"dashboard widget plugin {name!r} failed to load: {error}"
        raise WidgetPluginError(message) from error


class DashboardApp(App[None]):
    """Stacks `dashboard.widgets` panels in configured order for one system pane (06 §5.4)."""

    def __init__(
        self,
        *,
        state: StateReader,
        project_dir: Path,
        focus_task: str | None,
        services: WidgetServices,
        widget_names: Sequence[str],
    ) -> None:
        """Build the system pane for `focus_task` (`None` means the home window)."""
        super().__init__()
        self._state = state
        self._project_dir = project_dir
        self._focus_task = focus_task
        self._services = services
        self._widget_names = tuple(widget_names)

    def compose(self) -> ComposeResult:
        """Yield one mounted `Widget` per configured widget name, in order."""
        context = WidgetContext(
            state=self._state,
            project_dir=self._project_dir,
            focus_task=self._focus_task,
            services=self._services,
        )
        for name in self._widget_names:
            yield _load_widget(name).create(context)
