"""The `dash --home | --task T-n` Textual app (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import App
from textual.containers import VerticalScroll

from shikiru.agents.registry import load_unique_entry_point
from shikiru.dashboard.contract import WIDGET_ENTRY_POINT_GROUP, WidgetContext
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult
    from textual.binding import BindingType

    from shikiru.clock import Clock
    from shikiru.dashboard.contract import (
        DashboardViews,
        DashboardWidget,
        OnChange,
        OnChangeRegistration,
        OpenPalette,
    )

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

    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [("ctrl+p", "open_palette", "Palette")]

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
        """Build the system pane for `focus_task` (`None` means the home window)."""
        super().__init__()
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

    def compose(self) -> ComposeResult:
        """Yield one mounted `Widget` per configured widget name, in order."""
        context = WidgetContext(
            views=self._views,
            focus_task=self._focus_task,
            resources_refresh_seconds=self._resources_refresh_seconds,
            docker_refresh_seconds=self._docker_refresh_seconds,
            on_change=self._on_change,
            on_motion=self._motion_callbacks.append,
            open_palette=self._open_palette,
        )
        with VerticalScroll(id="dashboard-panels"):
            for name in self._widget_names:
                yield _load_widget(name).create(context)

    async def on_mount(self) -> None:
        """Start the single clock-driven refresh source for mounted motion-aware widgets."""
        if self._motion_interval_seconds is not None:
            self.run_worker(self._motion_loop(), group="dashboard-motion", exclusive=True)

    async def _motion_loop(self) -> None:
        interval = self._motion_interval_seconds
        if interval is None:
            return
        while True:
            await self._clock.sleep(interval)
            for callback in tuple(self._motion_callbacks):
                await callback()

    async def action_open_palette(self) -> None:
        """Open the operator palette through the shared dashboard callback."""
        await self._open_palette("")
