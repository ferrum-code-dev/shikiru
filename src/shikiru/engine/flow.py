"""`TaskFlow`: runs a task's steps to `integrate` or an escalation (03-workflow.md §2-3).

Every rule of the review loop lives in `domain.steps.next_step`; this module only orchestrates
around it: resolving each step's turn, applying the resulting `Transition`, inserting any new
tasks a plan/supersede carries, raising an escalation when one is reached, and delegating
the integrating state to the injected coordinator.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import FailureKind
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    Action,
    Backoff,
    Completed,
    Failed,
    NextRung,
    ProtocolError,
    RecoveryPolicy,
    ResumeTurn,
    RetryTurn,
    TurnCounters,
    TurnResult,
    WaitForQuota,
    decide,
    quota_block_until,
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
from shikiru.engine.history import ENGINE_VERDICT_FILENAME, ReportHistory
from shikiru.engine.integration_turns import IntegrationTurns, IntegrationTurnStopped
from shikiru.engine.turn_execution import TurnExecution
from shikiru.errors import ShikiruError, UsageError

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Task
    from shikiru.engine.escalation_filing import EscalationFiler
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.task_facts import TaskFacts
    from shikiru.engine.titles import TaskTitles
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.engine.turn_context import TurnContextPreparer
    from shikiru.engine.turns import TurnRunner
    from shikiru.prompts.renderer import PromptName
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore


UNCOMMITTED_SUMMARY = "The workspace has uncommitted changes, so it cannot be integrated."
UNCOMMITTED_ISSUE = (
    "Uncommitted changes in the workspace: {paths}. "
    "Commit what belongs to this task and discard the rest."
)
_QUOTA_WAIT_EXHAUSTED_MESSAGE = "quota wait exceeded"


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
        titles: TaskTitles,
        settings: Settings,
        project_root: Path,
        quota: QuotaStore,
        clock: Clock,
        integration_turns: IntegrationTurns,
        history: ReportHistory,
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
        self._titles = titles
        self._settings = settings
        self._project_root = project_root
        self._quota = quota
        self._clock = clock
        self._integration_turns = integration_turns
        self._history = history
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
        while not isinstance(execution.result, Completed):
            current, counters, stopped, continued = await self._recover(
                current,
                current_step,
                execution,
                counters,
                role_override=role_override,
            )
            if stopped:
                return current, counters, None, True
            if continued is not None:
                execution = continued
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

    async def _recover(
        self,
        task: Task,
        step: Step,
        execution: TurnExecution,
        counters: TurnCounters,
        *,
        role_override: Role | None = None,
    ) -> tuple[Task, TurnCounters, bool, TurnExecution | None]:
        result = execution.result
        emit_turn_failed(self._events, task, step, result)
        role = role_override if role_override is not None else ACTING_ROLE[step]
        policy = self._recovery_policy(
            task, role, quota_wait_exhausted=execution.quota_wait_exhausted
        )
        action = decide(result, counters, policy)
        if isinstance(result, Failed) and result.kind is FailureKind.QUOTA:
            self._block_quota(task, role, result.resets_at)
        updated, should_return, counters = await self._apply_recovery(
            task,
            step,
            role,
            execution=execution,
            result=result,
            counters=counters,
            action=action,
        )
        continued = updated if isinstance(updated, TurnExecution) else None
        next_task = self._tasks.require(task.id) if isinstance(updated, TurnExecution) else updated
        return next_task, counters, should_return, continued

    def _recovery_policy(
        self, task: Task, role: Role, *, quota_wait_exhausted: bool = False
    ) -> RecoveryPolicy:
        current = move_up(task, role, self._settings)
        different = move_up(task, role, self._settings, different_agent=True)
        return RecoveryPolicy(
            now=self._clock.now(),
            max_quota_wait=timedelta(seconds=self._settings.orchestration.timeouts.max_quota_wait),
            has_next_rung=current is not None,
            has_next_rung_different_agent=different is not None,
            quota_wait_exhausted=quota_wait_exhausted,
        )

    async def _apply_recovery(
        self,
        task: Task,
        step: Step,
        role: Role,
        *,
        execution: TurnExecution,
        result: TurnResult,
        counters: TurnCounters,
        action: Action,
    ) -> tuple[Task | TurnExecution, bool, TurnCounters]:
        updated = task
        next_counters = counters
        if isinstance(action, WaitForQuota):
            terminal = await self._wait_for_quota(task, role, action.until)
            if terminal is not None:
                updated = dataclasses.replace(
                    execution,
                    result=terminal,
                    quota_wait_exhausted=True,
                )
            else:
                self._contexts.request_continue(task.id)
                updated = self._tasks.require(task.id)
        elif isinstance(action, Backoff):
            await self._clock.sleep(action.delay.total_seconds())
            updated = self._tasks.require(task.id)
            next_counters = dataclasses.replace(
                counters, unavailable_retries=counters.unavailable_retries + 1
            )
        elif isinstance(action, RetryTurn):
            next_counters = dataclasses.replace(counters, other_retries=counters.other_retries + 1)
        elif isinstance(action, ResumeTurn):
            self._contexts.request_continue(task.id)
            updated = self._tasks.require(task.id)
            next_counters = dataclasses.replace(
                counters,
                stall_recoveries=counters.stall_recoveries + 1,
            )
        elif isinstance(action, NextRung):
            return await self._apply_next_rung(task, step, role, result, action)
        else:
            return await self._raise_recovery_escalation(task, step, role, action.reason, result)
        return updated, False, next_counters

    async def _apply_next_rung(
        self, task: Task, step: Step, role: Role, result: TurnResult, action: NextRung
    ) -> tuple[Task | TurnExecution, bool, TurnCounters]:
        if action.different_agent:
            self._contexts.request_handoff(task.id)
        updated = await self._move_rung(task, step, role, action.different_agent, result)
        if updated is not None:
            return updated, False, TurnCounters()
        reason = (
            EscalationReason.QUOTA_EXHAUSTED
            if action.different_agent
            else EscalationReason.LADDER_EXHAUSTED
        )
        return await self._raise_recovery_escalation(task, step, role, reason, result)

    async def _raise_recovery_escalation(
        self, task: Task, step: Step, role: Role, reason: EscalationReason, result: TurnResult
    ) -> tuple[Task, bool, TurnCounters]:
        transition = Transition.escalate(task, reason, step=step)
        updated = await self._escalate(task, transition, None, result=result, role=role)
        return updated, True, TurnCounters()

    async def _move_rung(
        self,
        task: Task,
        step: Step,
        role: Role,
        different_agent: bool,
        result: TurnResult,
    ) -> Task | None:
        move = move_up(task, role, self._settings, different_agent=different_agent)
        if move is None:
            return None
        old_rung = pick_rung(task, role, self._settings)
        transition = dataclasses.replace(
            Transition.stay(task, step=step, status=task.status),
            counters=move.counters,
            rung_index=move.rung_index,
            overrides=move.overrides,
        )
        self._tasks.apply_transition(task.id, transition)
        updated = self._tasks.require(task.id)
        new_rung = pick_rung(updated, role, self._settings)
        self._events.append(
            EventKind.RUNG_CHANGED,
            task_id=task.id,
            message=f"{task.id} {role.value} moved from {old_rung.model} to {new_rung.model}",
            data={
                "role": role.value,
                "from": old_rung.model,
                "to": new_rung.model,
                "different_agent": different_agent,
                "result": type(result).__name__,
            },
        )
        await self._titles.refresh(updated)
        return updated

    def _block_quota(self, task: Task, role: Role, resets_at: datetime | None) -> None:
        agent = pick_rung(task, role, self._settings).agent
        until = quota_block_until(resets_at, self._clock.now())
        self._quota.block(agent, until, "quota")
        self._events.append(
            EventKind.QUOTA_BLOCKED,
            task_id=task.id,
            message=f"{agent} quota blocked until {until.isoformat()}",
            data={"agent": agent, "until": until.isoformat(), "reason": "quota"},
        )

    async def _wait_for_quota(self, task: Task, role: Role, until: datetime) -> Failed | None:
        agent = pick_rung(task, role, self._settings).agent
        deadline = self._clock.now() + timedelta(
            seconds=self._settings.orchestration.timeouts.max_quota_wait
        )
        while True:
            current = self._quota.block_for(agent)
            now = self._clock.now()
            current_until = current.until if current is not None else until
            if current_until > now:
                if now >= deadline:
                    return Failed(
                        kind=FailureKind.QUOTA,
                        resets_at=current_until - QUOTA_MARGIN,
                        message=_QUOTA_WAIT_EXHAUSTED_MESSAGE,
                    )
                remaining = min(current_until, deadline) - now
                await self._clock.sleep(remaining.total_seconds())
                continue
            self._quota.clear_block(agent)
            self._events.append(
                EventKind.QUOTA_CLEARED,
                task_id=task.id,
                message=f"{agent} quota block cleared",
                data={"agent": agent},
            )
            return None

    async def _run_turn(
        self,
        task: Task,
        step: Step,
        *,
        role_override: Role | None = None,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> TurnExecution:
        role = role_override if role_override is not None else ACTING_ROLE[step]
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

        result, turn_dir, selection = await self._turns.run(
            task, role, selection, turn_request, on_nudge=on_nudge
        )
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
        if verdict.decision != "accept":
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
        (turn_dir / ENGINE_VERDICT_FILENAME).write_text(rejection.model_dump_json(indent=2))
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
