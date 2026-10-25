"""Persists domain transitions of a task and projects them into events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.reports import PlanReport, Verdict
from shikiru.domain.tasks import Status, TaskOrigin
from shikiru.engine.events import emit_step_started
from shikiru.errors import InvariantError

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.steps import Outcome, Transition
    from shikiru.domain.tasks import Task
    from shikiru.engine.titles import TaskTitles
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore


class TransitionWriter:
    """The single place a `Transition` becomes stored task state, events and a refreshed title."""

    def __init__(
        self, *, tasks: TaskStore, events: EventLog, titles: TaskTitles, settings: Settings
    ) -> None:
        """Write transitions to `tasks`, record their events and refresh titles."""
        self._tasks = tasks
        self._events = events
        self._titles = titles
        self._settings = settings

    async def apply(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
        """Apply a task transition and refresh its visible title."""
        updated = self.persist(task, transition, outcome)
        await self._titles.refresh(updated)
        return updated

    def persist(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
        """Write a task transition, emitting its step event, and return the stored task."""
        if transition.enqueue_new_tasks:
            if outcome is None:
                message = f"{task.id}'s transition enqueues new tasks but has no outcome"
                raise InvariantError(message)
            self._apply_new_tasks(task, transition, outcome)
        else:
            self._tasks.apply_transition(task.id, transition)
            if transition.status in (Status.DONE, Status.CANCELLED):
                self._tasks.set_slot(task.id, None)
            if transition.status is Status.ESCALATED:
                self._tasks.set_wait_reason(task.id, None)
        updated = self._tasks.require(task.id)
        if (
            task.step != updated.step
            and updated.step is not None
            and updated.status in (Status.RUNNING, Status.INTEGRATING)
        ):
            emit_step_started(self._events, updated, self._settings)
        return updated

    def _apply_new_tasks(self, task: Task, transition: Transition, outcome: Outcome) -> None:
        if not isinstance(outcome, PlanReport | Verdict):
            message = f"{task.id}'s transition enqueues new tasks from a {type(outcome).__name__}"
            raise InvariantError(message)
        children = self._tasks.apply_scope_change(
            task.id, transition, outcome.new_tasks, origin=TaskOrigin.LEADER
        )
        if not outcome.new_tasks and not outcome.scope_changes:
            return
        self._events.append(
            EventKind.SCOPE_CHANGE,
            task_id=task.id,
            message=f"{task.id} scope changed: {len(children)} new task(s)",
            data={
                "new_task_ids": [child.id for child in children],
                "scope_changes": list(outcome.scope_changes),
            },
        )
