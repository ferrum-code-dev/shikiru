"""`TaskFlow`: runs a task's steps to `integrate` or an escalation.

Every rule of the review loop lives in `domain.steps.next_step`; this module only sequences
around it: starting a task at its first step, running step after step through the `StepRunner`,
escalating unexpected errors and handing the integrating state to `IntegrationTurns`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.domain.recovery import TurnCounters
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import EscalationReason, Status
from shikiru.errors import InvariantError

if TYPE_CHECKING:
    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.integration_turns import IntegrationTurns
    from shikiru.engine.step_runner import StepRunner
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.engine.turn_loop import TurnLoop
    from shikiru.store.tasks import TaskStore

_LOGGER = logging.getLogger(__name__)


class TaskFlow:
    """Runs one task's steps, applying `domain.steps.next_step` at every turn boundary."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        writer: TransitionWriter,
        steps: StepRunner,
        turn_loop: TurnLoop,
        escalations: EscalationFiler,
        integration_turns: IntegrationTurns,
    ) -> None:
        """Sequence `steps` for a task, then its integration turns."""
        self._tasks = tasks
        self._writer = writer
        self._steps = steps
        self._turn_loop = turn_loop
        self._escalations = escalations
        self._integration_turns = integration_turns

    async def run(self, task_id: str, *, resumed: bool = False) -> None:
        """Run `task_id` from its current step (or its first) until `integrate` or an escalation.

        A `resumed` run re-runs the step an engine restart interrupted: its first turn consumes a
        report that turn already submitted instead of starting another.
        An unexpected error escalates the task as `engine_error` instead of propagating, so one
        task's failure never stops the engine.
        """
        if resumed:
            self._turn_loop.expect_resume(task_id)
        try:
            await self._run(task_id)
        except Exception as error:
            _LOGGER.exception("task flow %s failed", task_id)
            task = self._tasks.get(task_id)
            if task is None or task.status in (Status.CANCELLED, Status.DONE, Status.ESCALATED):
                return
            transition = Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
            await self._escalations.escalate(task, transition, None, question=str(error))
        finally:
            self._turn_loop.forget_resume(task_id)

    async def _run(self, task_id: str) -> None:
        task = self._tasks.require(task_id)
        if task.step is None:
            task = await self._writer.apply(task, first_step(task), None)
        counters = TurnCounters()
        while task.status is Status.RUNNING:
            if task.step is None:
                message = f"{task.id} is running with no step set"
                raise InvariantError(message)
            task, counters, stopped = await self._steps.run_step(task, task.step, counters)
            if stopped:
                return
        if task.status is Status.INTEGRATING:
            task = self._tasks.require(task.id)
            if not task.held:
                await self._integration_turns.run(task)
