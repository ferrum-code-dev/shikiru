"""Runs a step's turns and their recoveries until one completes or the task stops (03 §7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.recovery import Completed
from shikiru.domain.steps import ACTING_ROLE, Transition
from shikiru.engine.events import emit_nudge, emit_turn_started
from shikiru.engine.turn_context import ORDINARY_TURN, NextTurn
from shikiru.engine.turn_execution import TurnExecution
from shikiru.engine.turns import TurnHeld
from shikiru.errors import InvariantError

if TYPE_CHECKING:
    from shikiru.domain.recovery import TurnCounters
    from shikiru.domain.reports import Report, ReportKind
    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.recovery import TurnRecovery
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.turn_context import TurnContextPreparer
    from shikiru.engine.turns import TurnRunner
    from shikiru.prompts.renderer import PromptName
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore


@dataclass(frozen=True)
class TurnOverrides:
    """What an integration turn changes about a step's ordinary turn."""

    role: Role | None = None
    template: PromptName | None = None
    kind: ReportKind | None = None
    conflict_context: str | None = None


NO_OVERRIDES = TurnOverrides()


@dataclass(frozen=True)
class TurnsCompleted:
    """The loop ended with a turn that produced `report`."""

    task: Task
    counters: TurnCounters
    execution: TurnExecution
    report: Report


@dataclass(frozen=True)
class TurnsStopped:
    """The loop ended because the task was held or escalated."""

    task: Task
    counters: TurnCounters


TurnLoopResult = TurnsCompleted | TurnsStopped


def _acting_role(step: Step, role_override: Role | None) -> Role:
    return role_override if role_override is not None else ACTING_ROLE[step]


class TurnLoop:
    """Runs a step's turn, recovering failed turns until one completes or the task stops."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        events: EventLog,
        sessions: SessionManager,
        turns: TurnRunner,
        contexts: TurnContextPreparer,
        recovery: TurnRecovery,
        escalations: EscalationFiler,
        runs: RunStore,
    ) -> None:
        """Run turns through `turns`, recover through `recovery`, escalate through `escalations`."""
        self._tasks = tasks
        self._events = events
        self._sessions = sessions
        self._turns = turns
        self._contexts = contexts
        self._recovery = recovery
        self._escalations = escalations
        self._runs = runs
        self._resumed: set[str] = set()

    def expect_resume(self, task_id: str) -> None:
        """Let `task_id`'s next turn consume a report its interrupted turn already submitted."""
        self._resumed.add(task_id)

    def forget_resume(self, task_id: str) -> None:
        """Drop the pending resume of `task_id`."""
        self._resumed.discard(task_id)

    async def run(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        overrides: TurnOverrides = NO_OVERRIDES,
    ) -> TurnLoopResult:
        """Run `step`'s turn and its recoveries until a report, a hold or an escalation."""
        current = self._tasks.require(task.id)
        if current.held:
            return TurnsStopped(current, counters)
        current_step = step
        execution = await self._execute(current, current_step, overrides, ORDINARY_TURN)
        while execution is not None:
            result = execution.result
            if isinstance(result, Completed):
                return TurnsCompleted(current, counters, execution, result.report)
            role = _acting_role(current_step, overrides.role)
            recovery = await self._recovery.recover(
                current, current_step, role, execution, counters
            )
            current, counters = recovery.task, recovery.counters
            if recovery.escalation is not None:
                transition = Transition.escalate(current, recovery.escalation, step=current_step)
                escalated = await self._escalations.escalate(
                    current, transition, None, result=result, role=role
                )
                return TurnsStopped(escalated, counters)
            if recovery.execution is not None:
                execution = recovery.execution
                continue
            if current.step is None:
                message = f"{current.id} recovery left the task with no step"
                raise InvariantError(message)
            current_step = current.step
            current = self._tasks.require(current.id)
            if current.held:
                return TurnsStopped(current, counters)
            execution = await self._execute(current, current_step, overrides, recovery.next_turn)
        return TurnsStopped(self._tasks.require(current.id), counters)

    async def _execute(
        self, task: Task, step: Step, overrides: TurnOverrides, next_turn: NextTurn
    ) -> TurnExecution | None:
        role = _acting_role(step, overrides.role)
        selection = self._sessions.session_for(task, role)
        turn_request = await self._contexts.prepare(
            task,
            step,
            session_handoff=selection.handoff,
            next_turn=next_turn,
            template_override=overrides.template,
            kind_override=overrides.kind,
            conflict_context=overrides.conflict_context,
        )
        usage_before = selection.run.usage
        resumed = task.id in self._resumed
        self._resumed.discard(task.id)
        completed = self._turns.completed_turn(task.id, role, turn_request) if resumed else None
        if completed is not None:
            result, turn_dir = completed
            return TurnExecution(
                result=result,
                task_id=task.id,
                role=role,
                turn_dir=turn_dir,
                rung=selection.rung,
                usage_before=usage_before,
                usage_after=usage_before,
            )
        emit_turn_started(self._events, task, step, role, selection.rung)

        def on_nudge() -> None:
            emit_nudge(self._events, task, step, role)

        try:
            result, turn_dir, selection = await self._turns.run(
                task, role, selection, turn_request, on_nudge=on_nudge
            )
        except TurnHeld:
            return None
        live_run = self._runs.get_live(task.id, role)
        if live_run is None:
            message = f"{task.id} {role.value} run disappeared after its turn"
            raise InvariantError(message)
        return TurnExecution(
            result=result,
            task_id=task.id,
            role=role,
            turn_dir=turn_dir,
            rung=selection.rung,
            usage_before=usage_before,
            usage_after=live_run.usage,
        )
