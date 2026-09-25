"""Compose ready-to-render dashboard data from state, settings and services (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from shikiru.dashboard.contract import HeaderView, QueueRow, RepoStatus
from shikiru.domain.scheduling import dispatch_sort_key, occupied_slot_ids
from shikiru.domain.tasks import ACTIVE_STATUSES, Status
from shikiru.operator.strip_views import task_strip_data

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.store.reader import StateReader
    from shikiru.ui.strip import TaskStripData


class DashboardSources(Protocol):
    """External information composed into dashboard view models."""

    async def repo_status(self) -> RepoStatus:
        """Return the project checkout's git status."""
        ...

    def engine_up(self) -> bool:
        """Return whether the engine holds its liveness lock."""
        ...


class StateDashboardViews:
    """Build header, task-strip and queue view models from the current state snapshot."""

    def __init__(
        self,
        state: StateReader,
        *,
        focus_task: str | None,
        settings: Settings,
        sources: DashboardSources,
    ) -> None:
        """Use `state` plus the configured values and sources to build widget views."""
        self._state = state
        self._focus_task = focus_task
        self._settings = settings
        self._sources = sources

    async def header(self) -> HeaderView:
        """Build the project, engine, slot and repository summary."""
        tasks = self._state.list_tasks()
        return HeaderView(
            project=self._settings.project.name,
            engine_up=self._sources.engine_up(),
            occupied_slots=len(occupied_slot_ids(tasks)),
            maximum_slots=self._settings.orchestration.max_parallel,
            repo=await self._sources.repo_status(),
        )

    def strips(self) -> list[TaskStripData]:
        """Build the active task strips, placing the focused task first."""
        tasks = self._state.list_tasks(statuses=ACTIVE_STATUSES)
        tasks.sort(key=lambda task: task.id != self._focus_task)
        return [task_strip_data(task, self._settings) for task in tasks]

    def queue(self) -> list[QueueRow]:
        """Build queued-task rows in dispatch order."""
        tasks = self._state.list_tasks(statuses=(Status.QUEUED,))
        tasks.sort(key=dispatch_sort_key)
        return [
            QueueRow(
                task_id=task.id,
                title=task.title,
                priority=task.priority,
                dependencies=tuple(task.depends_on),
                wait_reason=task.wait_reason,
            )
            for task in tasks
        ]
