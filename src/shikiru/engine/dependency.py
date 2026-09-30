"""Dependency-cancellation escalation orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason

if TYPE_CHECKING:
    from shikiru.domain.tasks import Task
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.transitions import TransitionWriter


class DependencyEscalator:
    """Apply and packet a dependency-cancelled escalation through one boundary."""

    def __init__(self, writer: TransitionWriter, raiser: EscalationRaiser) -> None:
        """Use `writer` for the state transition and `raiser` for the operator packet."""
        self._writer = writer
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
            apply_state=lambda: self._writer.persist(task, transition, None),
        )
