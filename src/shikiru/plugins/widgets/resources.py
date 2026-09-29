"""The `resources` widget: host and Shikiru process-tree usage (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from shikiru.ui import Gauge

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import ResourceView, WidgetContext

GIB_FORMAT = ".1f"
INSTANCE_RSS_FORMAT = ".2f"
UNKNOWN_VALUE = "\N{EN DASH}"


def _resource_lines(snapshot: ResourceView) -> Group:
    host = Text.assemble(
        Gauge("host CPU", snapshot.host_cpu_pct).render(),
        " · ",
        Gauge("host RAM", snapshot.host_ram_used_pct).render(),
        f" · free {snapshot.free_ram_gb:{GIB_FORMAT}} GiB",
    )
    if snapshot.instance_cpu_pct is None or snapshot.instance_rss_gb is None:
        instance = Text(f"Shikiru CPU {UNKNOWN_VALUE} · RSS {UNKNOWN_VALUE}")
    else:
        instance = Text.assemble(
            Gauge("Shikiru CPU", snapshot.instance_cpu_pct).render(),
            f" · RSS {snapshot.instance_rss_gb:{INSTANCE_RSS_FORMAT}} GiB",
        )
    return Group(host, instance)


class ResourcesPanel(Static):
    """Show host use and the engine plus every pane process tree."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; resource sampling starts on mount."""
        super().__init__(id="resources")
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render and resample resources at the configured cadence."""
        await self._refresh()
        self.set_interval(self._widget_context.resources_refresh_seconds, self._refresh)

    async def _refresh(self) -> None:
        self.update(_resource_lines(await self._widget_context.views.resources()))


class ResourcesWidget:
    """The `resources` dashboard widget plugin (06 §5.4)."""

    name = "resources"

    def create(self, context: WidgetContext) -> Widget:
        """Build the resource panel for `context`."""
        return ResourcesPanel(context)
