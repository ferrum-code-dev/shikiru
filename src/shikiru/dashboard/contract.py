"""Stable view models and plugin protocols for dashboard widgets (06 §5.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.ui.strip import TaskStripData

WIDGET_ENTRY_POINT_GROUP = "shikiru.widgets"
OnChange = Callable[[], Awaitable[None]]
OnChangeRegistration = Callable[[OnChange], Awaitable[None]]


@dataclass(frozen=True)
class RepoStatus:
    """The project checkout's state, as shown by the `header` widget (06 §5.4)."""

    branch: str
    ahead: int
    behind: int
    dirty: int


@dataclass(frozen=True)
class HeaderView:
    """Project and engine summary rendered by the header widget."""

    project: str
    engine_up: bool
    occupied_slots: int
    maximum_slots: int
    repo: RepoStatus


@dataclass(frozen=True)
class QueueRow:
    """One queued task row, already ordered by the scheduler's dispatch rules."""

    task_id: str
    title: str
    priority: int
    dependencies: tuple[str, ...]
    wait_reason: str | None


class DashboardViews(Protocol):
    """Ready-to-render data; widget plugins never receive state or configuration objects."""

    async def header(self) -> HeaderView:
        """Return the project, engine, slots and repository view."""
        ...

    def strips(self) -> list[TaskStripData]:
        """Return active task strips, with the focused task first."""
        ...

    def queue(self) -> list[QueueRow]:
        """Return queued tasks in dispatch order."""
        ...


class DashboardWidget(Protocol):
    """A system-pane panel plugin, registered under `WIDGET_ENTRY_POINT_GROUP` (06 §5.4)."""

    name: str

    def create(self, context: WidgetContext) -> Widget:
        """Build this widget from its views and update subscription."""
        ...


@dataclass(frozen=True)
class WidgetContext:
    """Data and update subscription supplied to a `DashboardWidget`."""

    views: DashboardViews
    focus_task: str | None
    on_change: OnChangeRegistration
