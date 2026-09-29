"""Dependency-cancellation escalation orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason
from shikiru.engine.escalations import escalate_stored

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
        transition = Transition.escalate_current(task, EscalationReason.DEPENDENCY_CANCELLED)
        await self._raiser.raise_escalation(
            task,
            step=transition.step,
            reason=EscalationReason.DEPENDENCY_CANCELLED,
            question="A dependency was cancelled; choose whether to drop it or cancel this task.",
            attempted="The scheduler found a cancelled dependency before dispatch.",
            reports=(),
            facts="",
            view_tail="",
            apply_state=lambda: escalate_stored(self._tasks, task, transition),
        )
