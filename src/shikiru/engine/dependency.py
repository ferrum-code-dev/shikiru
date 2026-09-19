"""Dependency-cancellation escalation orchestration."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Status
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from shikiru.domain.tasks import Task
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.store.tasks import TaskStore


class DependencyEscalator:
    """Apply and packet a dependency-cancelled escalation through one boundary."""

    def __init__(self, tasks: TaskStore, raiser: EscalationRaiser) -> None:
        """Use `tasks` for the state transition and `raiser` for the operator packet."""
        self._tasks = tasks
        self._raiser = raiser

    async def __call__(self, task: Task) -> None:
        """Escalate `task` after the scheduler finds a cancelled dependency."""
        transition = replace(
            Transition.stay(task, step=task.step, status=Status.ESCALATED),
            escalation_reason=EscalationReason.DEPENDENCY_CANCELLED,
        )
        self._tasks.apply_transition(task.id, transition)
        self._tasks.set_wait_reason(task.id, None)
        current = self._tasks.get(task.id)
        if current is None:
            message = f"{task.id} vanished before dependency escalation"
            raise ShikiruError(message)
        await self._raiser.raise_escalation(
            current,
            step=current.step,
            reason=EscalationReason.DEPENDENCY_CANCELLED,
            question="A dependency was cancelled; choose whether to drop it or cancel this task.",
            attempted="The scheduler found a cancelled dependency before dispatch.",
            reports=(),
            facts="",
            pane_tail="",
        )
