"""The `docker` widget: live container CPU and memory (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from shikiru.dashboard.contract import DOCKER_REFRESH_SECONDS

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import DockerView, WidgetContext


def _docker_text(snapshot: DockerView) -> str | Text:
    if not snapshot.containers:
        return "no running containers"
    return Text("\n").join(
        Text(f"{container.name} · CPU {container.cpu_pct} · memory {container.memory}")
        for container in snapshot.containers
    )


class DockerPanel(Static):
    """Show Docker container usage and remain hidden when the daemon is unavailable."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; Docker sampling starts on mount."""
        super().__init__(id="docker")
        self._widget_context = context
        self.display = False

    async def on_mount(self) -> None:
        """Render and refresh Docker state at the five-second protocol cadence."""
        await self._refresh()
        self.set_interval(DOCKER_REFRESH_SECONDS, self._refresh)

    async def _refresh(self) -> None:
        snapshot = await self._widget_context.views.containers()
        self.display = snapshot.available
        if snapshot.available:
            self.update(_docker_text(snapshot))


class DockerWidget:
    """The `docker` dashboard widget plugin (06 §5.4)."""

    name = "docker"

    def create(self, context: WidgetContext) -> Widget:
        """Build the Docker panel for `context`."""
        return DockerPanel(context)
