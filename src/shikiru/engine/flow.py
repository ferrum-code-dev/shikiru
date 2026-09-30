"""`TaskFlow`: runs a task's steps to `integrate` or an escalation (03-workflow.md §2-3).

Every rule of the review loop lives in `domain.steps.next_step`; this module only sequences
around it: running each step's turn, delegating recovery, context, evidence and persistence to
their collaborators, escalating when the loop says so, and delegating the integrating state to
the injected coordinator.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.domain.recovery import (
    Completed,
    ProtocolError,
    TurnCounters,
    TurnResult,
)
from shikiru.domain.reports import (
    ImplementationReport,
    Issue,
    ReportKind,
    Verdict,
)
from shikiru.domain.steps import ACTING_ROLE, Transition, first_step, next_step
from shikiru.domain.tasks import EscalationReason, Role, Status, Step
from shikiru.engine.events import (
    emit_nudge,
    emit_turn_completed,
    emit_turn_failed,
    emit_turn_started,
    emit_verdict,
)
from shikiru.engine.history import ENGINE_VERDICT_FILENAME
from shikiru.engine.integration_turns import IntegrationTurns, IntegrationTurnStopped
from shikiru.engine.turn_execution import TurnExecution
from shikiru.engine.turns import TurnHeld
from shikiru.errors import ShikiruError, UsageError
from shikiru.private import write_private_text

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Task
    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.recovery import TurnRecovery
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.task_facts import TaskFacts
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.engine.turn_context import TurnContextPreparer
    from shikiru.engine.turns import TurnRunner
    from shikiru.prompts.renderer import PromptName
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore


_WORK_ENDING_DECISIONS = frozenset({"accept", "supersede"})
UNCOMMITTED_SUMMARY = "The workspace has uncommitted changes, so it cannot be integrated."
UNCOMMITTED_ISSUE = (
    "Uncommitted changes in the workspace: {paths}. "
    "Commit what belongs to this task and discard the rest."
)


def _acting_role(step: Step, role_override: Role | None) -> Role:
    return role_override if role_override is not None else ACTING_ROLE[step]


class TaskFlow:
    """Runs one task's steps, applying `domain.steps.next_step` at every turn boundary."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        events: EventLog,
        sessions: SessionManager,
        turns: TurnRunner,
        facts: TaskFacts,
        contexts: TurnContextPreparer,
        escalations: EscalationFiler,
        writer: TransitionWriter,
        recovery: TurnRecovery,
        settings: Settings,
        integration_turns: IntegrationTurns,
        runs: RunStore,
    ) -> None:
        """Wire the step loop, recovery, report history and integration turns."""
        self._tasks = tasks
        self._events = events
        self._sessions = sessions
        self._turns = turns
        self._facts = facts
        self._contexts = contexts
        self._escalations = escalations
        self._writer = writer
        self._recovery = recovery
        self._settings = settings
        self._integration_turns = integration_turns
        self._runs = runs
        self._resumed: set[str] = set()

    async def run(self, task_id: str, *, resumed: bool = False) -> None:
        """Run `task_id` from its current step (or its first) until `integrate` or an escalation.

        A `resumed` run re-runs the step an engine restart interrupted: its first turn consumes a
        report that turn already submitted instead of starting another (03 §7).
        An unexpected error escalates the task as `engine_error` instead of propagating, so one
        task's failure never stops the engine (03 §8).
        """
        if resumed:
            self._resumed.add(task_id)
        try:
            await self._run(task_id)
        except Exception as error:
            _LOGGER.exception("task flow %s failed", task_id)
            task = self._tasks.get(task_id)
            if task is None or task.status in (Status.CANCELLED, Status.DONE, Status.ESCALATED):
                return
            await self._escalate_engine_error(task, error)
        finally:
            self._resumed.discard(task_id)

    async def _run(self, task_id: str) -> None:
        task = self._tasks.require(task_id)
        if task.step is None:
            task = await self._writer.apply(task, first_step(task), None)
        recovery_counters = TurnCounters()
        while task.status is Status.RUNNING:
            step = task.step
            if step is None:
                message = f"{task.id} is running with no step set"
                raise ShikiruError(message)
            task, recovery_counters, should_return = await self.run_step(
                task, step, recovery_counters
            )
            if should_return:
                return
        if task.status is Status.INTEGRATING:
            task, held = self._reload(task.id)
            if not held:
                await self._integration_turns.run(task, self)

    async def run_report_turn(
        self,
        task: Task,
        *,
        role: Role,
        template: PromptName,
        kind: ReportKind,
    ) -> tuple[Task, ImplementationReport | Verdict]:
        """Run one integration report through the shared turn and recovery loop."""
        task, held = self._reload(task.id)
        if held:
            raise IntegrationTurnStopped
        current, _, execution, stopped = await self._run_until_completed(
            task,
            Step.INTEGRATE,
            TurnCounters(),
            role_override=role,
            template_override=template,
            kind_override=kind,
        )
        if stopped or execution is None:
            raise IntegrationTurnStopped
        if not isinstance(execution.result, Completed):
            message = f"{current.id} integration turn completed without a report"
            raise ShikiruError(message)
        report = execution.result.report
        if isinstance(report, ImplementationReport | Verdict):
            return current, await self._settle(current, report, execution)
        message = f"{current.id} integration turn returned an unexpected report"
        raise ShikiruError(message)

    async def run_step(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        *,
        conflict_context: str | None = None,
    ) -> tuple[Task, TurnCounters, bool]:
        """Run one domain step through the task's turn and recovery loop."""
        task, held = self._reload(task.id)
        if held:
            return task, counters, True
        return await self._run_step_turn(task, step, counters, conflict_context=conflict_context)

    async def _run_step_turn(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        *,
        conflict_context: str | None = None,
    ) -> tuple[Task, TurnCounters, bool]:
        task, counters, execution, stopped = await self._run_until_completed(
            task, step, counters, conflict_context=conflict_context
        )
        if stopped:
            return task, counters, True
        if execution is None:
            message = f"{task.id} turn loop completed without an execution"
            raise ShikiruError(message)
        return await self._complete_step_turn(task, step, execution)

    async def _run_until_completed(
        self,
        task: Task,
        step: Step,
        counters: TurnCounters,
        *,
        role_override: Role | None = None,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> tuple[Task, TurnCounters, TurnExecution | None, bool]:
        current = task
        current_step = step
        execution = await self._run_turn(
            current,
            current_step,
            role_override=role_override,
            template_override=template_override,
            kind_override=kind_override,
            conflict_context=conflict_context,
        )
        if execution is None:
            return self._tasks.require(current.id), counters, None, True
        while not isinstance(execution.result, Completed):
            role = _acting_role(current_step, role_override)
            recovery = await self._recovery.recover(
                current, current_step, role, execution, counters
            )
            current, counters = recovery.task, recovery.counters
            if recovery.escalation is not None:
                transition = Transition.escalate(current, recovery.escalation, step=current_step)
                escalated = await self._escalate(
                    current, transition, None, result=execution.result, role=role
                )
                return escalated, counters, None, True
            if recovery.execution is not None:
                execution = recovery.execution
                continue
            next_step = current.step
            if next_step is None:
                message = f"{current.id} recovery left the task with no step"
                raise ShikiruError(message)
            current, held = self._reload(current.id)
            if held:
                return current, counters, None, True
            current_step = next_step
            execution = await self._run_turn(
                current,
                current_step,
                role_override=role_override,
                template_override=template_override,
                kind_override=kind_override,
                conflict_context=conflict_context,
            )
            if execution is None:
                return self._tasks.require(current.id), counters, None, True
        return current, counters, execution, False

    async def _complete_step_turn(
        self, task: Task, step: Step, execution: TurnExecution
    ) -> tuple[Task, TurnCounters, bool]:
        result = execution.result
        if not isinstance(result, Completed):
            message = f"{task.id} completed-step handler received {type(result).__name__}"
            raise ShikiruError(message)
        outcome = await self._settle(task, result.report, execution)
        try:
            task, escalated = await self.advance(task, outcome)
        except UsageError as error:
            task = await self._escalate_scope_change_protocol(task, step, outcome, error)
            return task, TurnCounters(), True
        return task, TurnCounters(), escalated

    async def advance(
        self, task: Task, outcome: Outcome, *, role: Role | None = None
    ) -> tuple[Task, bool]:
        """Apply the transition `outcome` earns `task`; report whether it escalated."""
        transition = next_step(task, outcome, self._settings)
        if transition.status is Status.ESCALATED:
            return await self._escalate(task, transition, outcome, role=role), True
        return await self._writer.apply(task, transition, outcome), False

    def _reload(self, task_id: str) -> tuple[Task, bool]:
        task = self._tasks.require(task_id)
        return task, task.held

    async def _settle[R: Outcome](
        self, task: Task, report: R, execution: TurnExecution
    ) -> R | Verdict:
        emit_turn_completed(self._events, task, report, execution)
        if isinstance(report, Verdict):
            return await self._reject_uncommitted(task, report, execution.turn_dir)
        return report

    async def _run_turn(
        self,
        task: Task,
        step: Step,
        *,
        role_override: Role | None = None,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> TurnExecution | None:
        role = _acting_role(step, role_override)
        selection = self._sessions.session_for(task, role)

        turn_request = await self._contexts.prepare(
            task,
            step,
            session_handoff=selection.handoff,
            template_override=template_override,
            kind_override=kind_override,
            conflict_context=conflict_context,
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
            raise ShikiruError(message)
        return TurnExecution(
            result=result,
            task_id=task.id,
            role=role,
            turn_dir=turn_dir,
            rung=selection.rung,
            usage_before=usage_before,
            usage_after=live_run.usage,
        )

    async def _escalate_engine_error(self, task: Task, error: Exception) -> None:
        transition = Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
        await self._escalate(task, transition, None, question=str(error))

    async def _escalate_scope_change_protocol(
        self, task: Task, step: Step, outcome: Outcome, error: UsageError
    ) -> Task:
        emit_turn_failed(self._events, task, step, ProtocolError())
        transition = Transition.escalate(task, EscalationReason.AGENT_PROTOCOL, step=step)
        return await self._escalate(task, transition, outcome, question=str(error))

    async def _reject_uncommitted(self, task: Task, verdict: Verdict, turn_dir: Path) -> Verdict:
        if verdict.decision not in _WORK_ENDING_DECISIONS:
            return verdict
        facts = await self._facts.collect(task)
        if not facts.status:
            return verdict
        issue = Issue(
            severity="blocker",
            description=UNCOMMITTED_ISSUE.format(
                paths=", ".join(line.strip() for line in facts.status)
            ),
        )
        rejection = Verdict(
            decision="changes_requested", summary=UNCOMMITTED_SUMMARY, issues=[issue]
        )
        write_private_text(turn_dir / ENGINE_VERDICT_FILENAME, rejection.model_dump_json(indent=2))
        emit_verdict(self._events, task, rejection, rejection.summary)
        return rejection

    async def escalate(
        self,
        task: Task,
        transition: Transition,
        outcome: Outcome | None,
        *,
        question: str | None = None,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> Task:
        """Apply and packet an escalation requested by an integration turn."""
        return await self._escalate(
            task, transition, outcome, question=question, result=result, role=role
        )

    async def _escalate(
        self,
        task: Task,
        transition: Transition,
        outcome: Outcome | None,
        *,
        question: str | None = None,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> Task:
        if transition.status is not Status.ESCALATED or transition.escalation_reason is None:
            message = f"{task.id} escalation has no escalated status or reason"
            raise ShikiruError(message)
        await self._escalations.raise_escalation(
            task,
            transition.step,
            transition.escalation_reason,
            outcome,
            question=question,
            result=result,
            role=role,
            apply_state=lambda: self._writer.persist(task, transition, outcome),
        )
        return self._tasks.require(task.id)
