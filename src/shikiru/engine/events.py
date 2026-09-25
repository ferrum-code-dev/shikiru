"""Shared event construction for engine lifecycle boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.steps import ACTING_ROLE

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.store.events import EventLog


def emit_step_started(events: EventLog, task: Task, settings: Settings) -> None:
    """Record the selected role and rung when a task step starts."""
    if task.step is None:
        return
    role = ACTING_ROLE[task.step]
    rung = pick_rung(task, role, settings)
    events.append(
        EventKind.STEP_STARTED,
        task_id=task.id,
        message=f"{task.id} {task.step.value} started",
        data={
            "step": task.step.value,
            "role": role.value,
            "iteration": task.iteration,
            "agent": rung.agent,
            "model": rung.model,
        },
    )
