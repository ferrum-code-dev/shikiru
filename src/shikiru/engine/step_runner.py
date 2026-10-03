"""Runs a step's turn to its transition, the boundary every turn shares (03 §2-3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.recovery import ProtocolError, TurnCounters
from shikiru.domain.reports import ImplementationReport, Verdict
from shikiru.domain.steps import Transition, next_step
from shikiru.domain.tasks import EscalationReason, Status, Step
from shikiru.engine.events import emit_turn_failed
from shikiru.engine.turn_loop import TurnOverrides, TurnsStopped
from shikiru.errors import InvariantError, UsageError

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.reports import Report, ReportKind
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.acceptance import AcceptanceGuard
    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.engine.turn_loop import TurnLoop
    from shikiru.prompts.renderer import PromptName
    from shikiru.store.events import EventLog


class TurnStopped(Exception):
    """Stop a caller's sequence after its task escalates or pauses."""


class StepRunner:
    """Runs a step's turn, settles its report and applies the transition the report earns."""

    def __init__(
        self,
        *,
        loop: TurnLoop,
        acceptance: AcceptanceGuard,
        escalations: EscalationFiler,
        writer: TransitionWriter,
        events: EventLog,
        settings: Settings,
    ) -> None:
        """Run turns through `loop`, guard acceptance, then transition or escalate."""
        self._loop = loop
        self._acceptance = acceptance
        self._escalations = escalations
        self._writer = writer
        self._events = events
        self._settings = settings

    async def run_step(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        *,
        conflict_context: str | None = None,
    ) -> tuple[Task, TurnCounters, bool]:
        """Run one domain step through its turn and recovery loop; report whether it stopped."""
        result = await self._loop.run(
            task, step, counters, TurnOverrides(conflict_context=conflict_context)
        )
        if isinstance(result, TurnsStopped):
            return result.task, result.counters, True
        outcome = await self._acceptance.settle(result.task, result.report, result.execution)
        try:
            current, escalated = await self.advance(result.task, outcome)
        except UsageError as error:
            escalated_task = await self._escalate_scope_change_protocol(
                result.task,
                step,
                outcome,
                error,
                turn=result.execution.turn_dir.name,
                role=result.execution.role,
            )
            return escalated_task, TurnCounters(), True
        return current, TurnCounters(), escalated

    async def run_report_turn(
        self,
        task: Task,
        *,
        role: Role,
        template: PromptName,
        kind: ReportKind,
    ) -> tuple[Task, ImplementationReport | Verdict]:
        """Run one integration report through the shared turn and recovery loop."""
        result = await self._loop.run(
            task,
            Step.INTEGRATE,
            TurnCounters(),
            TurnOverrides(role=role, template=template, kind=kind),
        )
        if isinstance(result, TurnsStopped):
            raise TurnStopped
        report: Report = result.report
        if not isinstance(report, ImplementationReport | Verdict):
            message = f"{result.task.id} integration turn returned an unexpected report"
            raise InvariantError(message)
        return result.task, await self._acceptance.settle(result.task, report, result.execution)

    async def advance(
        self, task: Task, outcome: Outcome, *, role: Role | None = None
    ) -> tuple[Task, bool]:
        """Apply the transition `outcome` earns `task`; report whether it escalated."""
        transition = next_step(task, outcome, self._settings)
        if transition.status is Status.ESCALATED:
            escalated = await self._escalations.escalate(task, transition, outcome, role=role)
            return escalated, True
        return await self._writer.apply(task, transition, outcome), False

    async def _escalate_scope_change_protocol(
        self,
        task: Task,
        step: Step,
        outcome: Outcome,
        error: UsageError,
        *,
        turn: str,
        role: Role,
    ) -> Task:
        emit_turn_failed(self._events, task, step, ProtocolError(), turn=turn, role=role)
        transition = Transition.escalate(task, EscalationReason.AGENT_PROTOCOL, step=step)
        return await self._escalations.escalate(task, transition, outcome, question=str(error))
