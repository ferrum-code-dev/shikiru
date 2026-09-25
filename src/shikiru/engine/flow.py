"""`TaskFlow`: runs a task's steps to `integrate` or an escalation (03-workflow.md §2-3).

Every rule of the review loop lives in `domain.steps.next_step`; this module only orchestrates
around it: resolving each step's turn, applying the resulting `Transition`, inserting any new
tasks a plan/supersede carries, raising an escalation when one is reached, and delegating
the integrating state to the injected coordinator.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import FailureKind, role_dir
from shikiru.domain.events import EventKind
from shikiru.domain.ladder import move_up, pick_rung
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    Action,
    Backoff,
    Completed,
    Failed,
    Handoff,
    NextRung,
    ProtocolError,
    RecoveryPolicy,
    ResumeTurn,
    RetryTurn,
    Stalled,
    TurnCounters,
    TurnResult,
    WaitForQuota,
    decide,
    quota_block_until,
)
from shikiru.domain.reports import (
    ConsultAnswer,
    ImplementationReport,
    Issue,
    PlanReport,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.steps import ACTING_ROLE, Transition, first_step, next_step
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status, Step, TaskOrigin
from shikiru.engine.events import emit_step_started
from shikiru.engine.history import ENGINE_VERDICT_FILENAME, ReportHistory
from shikiru.engine.integration_turns import IntegrationTurns, IntegrationTurnStopped
from shikiru.engine.turns import TurnRequest
from shikiru.errors import ShikiruError, UsageError
from shikiru.paths import run_dir, task_root
from shikiru.prompts.renderer import PromptName
from shikiru.workspace.facts import render_facts

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.contracts import Rung, Usage
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.reports import Report
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import RepoState, Task
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.titles import TaskTitles
    from shikiru.engine.turns import TurnRunner
    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.facts import GitFacts


ESCALATION_PANE_TAIL_LINES = 60
"""Lines of pane scrollback captured as escalation evidence (03 §8)."""

FACTS_UNAVAILABLE = "(facts unavailable: {error})"
UNCOMMITTED_SUMMARY = "The workspace has uncommitted changes, so it cannot be integrated."
UNCOMMITTED_ISSUE = (
    "Uncommitted changes in the workspace: {paths}. "
    "Commit what belongs to this task and discard the rest."
)
_QUOTA_WAIT_EXHAUSTED_MESSAGE = "quota wait exceeded"


def _template_for_step(step: Step, iteration: int) -> tuple[PromptName, ReportKind]:
    if step is Step.TRIAGE:
        return PromptName.TRIAGE, ReportKind.TRIAGE
    if step is Step.PLAN:
        return PromptName.PLAN, ReportKind.PLAN
    if step is Step.IMPLEMENT:
        template = PromptName.IMPLEMENT if iteration <= 1 else PromptName.REWORK
        return template, ReportKind.IMPLEMENTATION
    if step is Step.CONSULT:
        return PromptName.CONSULT, ReportKind.CONSULT
    if step is Step.VERIFY:
        return PromptName.VERIFY, ReportKind.VERDICT
    if step is Step.INTEGRATE:
        return PromptName.CONFLICTS, ReportKind.IMPLEMENTATION
    message = f"TaskFlow does not run a turn for step {step!r}; that is Task 16's job (03 §6)"
    raise ShikiruError(message)


def _turn_summary(outcome: Outcome) -> str:
    if isinstance(outcome, TriageReport):
        return outcome.title
    if isinstance(outcome, ConsultAnswer):
        return outcome.answer if outcome.decision == "answer" else "escalate"
    return outcome.summary


def _turn_question(outcome: Outcome) -> str:
    return outcome.question if isinstance(outcome, ImplementationReport) else ""


def _turn_outcome(outcome: Outcome) -> str:
    if isinstance(outcome, TriageReport):
        return outcome.mode.value
    if isinstance(outcome, PlanReport):
        return "completed"
    if isinstance(outcome, ConsultAnswer):
        return outcome.decision
    if isinstance(outcome, ImplementationReport):
        return outcome.status
    return outcome.decision


def _usage_delta(before: Usage, after: Usage) -> dict[str, int | float | None]:
    if after.cost_usd is None:
        cost_delta = None
    elif before.cost_usd is None:
        cost_delta = after.cost_usd
    else:
        cost_delta = after.cost_usd - before.cost_usd
    return {
        "input_tokens": after.input_tokens - before.input_tokens,
        "output_tokens": after.output_tokens - before.output_tokens,
        "cache_read_tokens": after.cache_read_tokens - before.cache_read_tokens,
        "cache_write_tokens": after.cache_write_tokens - before.cache_write_tokens,
        "cost_usd": cost_delta,
    }


def _escalation_narrative(
    reason: EscalationReason,
    outcome: Outcome | None,
    result: TurnResult | None = None,
) -> tuple[str, str]:
    question = "An Operator decision is needed to continue."
    attempted = "Automatic recovery has no option left"
    if reason is EscalationReason.ITERATIONS_EXHAUSTED:
        question = "The final round still requested changes; choose accept, supersede or cancel."
        attempted = "Implement and verify cycled to the configured iteration limit."
    elif reason is EscalationReason.ENGINE_ERROR:
        attempted = "The engine raised an error no automatic recovery covers."
    elif isinstance(outcome, ConsultAnswer) and outcome.escalation is not None:
        question = outcome.escalation.question
        attempted = "The Leader escalated during a consult turn."
    elif isinstance(outcome, Verdict) and outcome.escalation is not None:
        question = outcome.escalation.question
        attempted = "The Leader escalated during verification."
    elif isinstance(outcome, ImplementationReport):
        question = outcome.question
        attempted = "The Implementer's question exceeded the consult limit."
    elif isinstance(result, Failed):
        question = result.message
        attempted = f"The agent reported a {result.kind.value} failure."
    elif isinstance(result, Stalled):
        question = "The agent's turn stream stopped progressing."
        attempted = "Automatic stall recovery was exhausted."
    elif isinstance(result, ProtocolError):
        question = "The agent did not produce a valid report."
        attempted = "The Helper could not rebuild it."
    elif result is not None:
        question = "The agent session could not continue."
        attempted = f"Recovery ended with {type(result).__name__}."
    return question, attempted


@dataclass(frozen=True)
class _TurnPlan:
    template: PromptName
    kind: ReportKind
    context: dict[str, object]
    facts: str


@dataclass
class _TurnExecution:
    result: TurnResult
    task_id: str
    role: Role
    turn_dir: Path
    rung: Rung
    usage_before: Usage
    usage_after: Usage
    quota_wait_exhausted: bool = False


class TaskFlow:
    """Runs one task's steps, applying `domain.steps.next_step` at every turn boundary."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        events: EventLog,
        sessions: SessionManager,
        turns: TurnRunner,
        git_facts: GitFacts,
        escalations: EscalationRaiser,
        escalation_store: EscalationStore,
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
        self._git_facts = git_facts
        self._escalations = escalations
        self._escalation_store = escalation_store
        self._titles = titles
        self._settings = settings
        self._project_root = project_root
        self._quota = quota
        self._clock = clock
        self._integration_turns = integration_turns
        self._history = history
        self._runs = runs
        self._force_handoff: set[str] = set()
        self._continue_next: set[str] = set()

    async def run(self, task_id: str) -> None:
        """Run `task_id` from its current step (or its first) until `integrate` or an escalation.

        An unexpected error escalates the task as `engine_error` instead of propagating, so one
        task's failure never stops the engine (03 §8).
        """
        try:
            await self._run(task_id)
        except Exception as error:
            _LOGGER.exception("task flow %s failed", task_id)
            task = self._tasks.get(task_id)
            if task is None or task.status in (Status.CANCELLED, Status.DONE, Status.ESCALATED):
                return
            await self._escalate_engine_error(task, error)

    async def _run(self, task_id: str) -> None:
        task = self.require_task(task_id)
        if task.step is None:
            task = await self.apply(task, first_step(task), None)
        if task.status is Status.RUNNING and task.step is Step.INTEGRATE:
            task = await self.apply(
                task,
                Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING),
                None,
            )
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
            self._emit_turn_completed(current, report, execution)
            return current, report
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
        task = self.require_task(task.id)
        if self._step_blocked(task):
            return task, counters, True
        return await self._run_step_turn(task, step, counters, conflict_context=conflict_context)

    @staticmethod
    def _step_blocked(task: Task) -> bool:
        return Hold.PAUSED in task.holds or Hold.INTERVENTION in task.holds

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
    ) -> tuple[Task, TurnCounters, _TurnExecution | None, bool]:
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
            current = self.require_task(current.id)
            if self._step_blocked(current):
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
        self, task: Task, step: Step, execution: _TurnExecution
    ) -> tuple[Task, TurnCounters, bool]:
        result = execution.result
        if not isinstance(result, Completed):
            message = f"{task.id} completed-step handler received {type(result).__name__}"
            raise ShikiruError(message)
        outcome = result.report
        self._emit_turn_completed(task, outcome, execution)
        if step is Step.VERIFY and isinstance(outcome, Verdict) and outcome.decision == "accept":
            outcome = await self._reject_uncommitted(task, outcome, execution.turn_dir)
        transition = next_step(task, outcome, self._settings)
        escalated = transition.status is Status.ESCALATED
        try:
            if escalated:
                task = await self._escalate(task, transition, outcome)
            else:
                task = await self.apply(task, transition, outcome)
        except UsageError as error:
            task = await self._escalate_scope_change_protocol(task, step, outcome, error)
            return task, TurnCounters(), True
        if escalated:
            return task, TurnCounters(), True
        return task, TurnCounters(), False

    async def _recover(
        self,
        task: Task,
        step: Step,
        execution: _TurnExecution,
        counters: TurnCounters,
        *,
        role_override: Role | None = None,
    ) -> tuple[Task, TurnCounters, bool, _TurnExecution | None]:
        result = execution.result
        self._emit_turn_failed(task, step, result)
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
        continued = updated if isinstance(updated, _TurnExecution) else None
        next_task = self.require_task(task.id) if isinstance(updated, _TurnExecution) else updated
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
        execution: _TurnExecution,
        result: TurnResult,
        counters: TurnCounters,
        action: Action,
    ) -> tuple[Task | _TurnExecution, bool, TurnCounters]:
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
                self._continue_next.add(task.id)
                updated = self.require_task(task.id)
        elif isinstance(action, Backoff):
            await self._clock.sleep(action.delay.total_seconds())
            updated = self.require_task(task.id)
            next_counters = dataclasses.replace(
                counters, unavailable_retries=counters.unavailable_retries + 1
            )
        elif isinstance(action, RetryTurn):
            next_counters = dataclasses.replace(counters, other_retries=counters.other_retries + 1)
        elif isinstance(action, ResumeTurn):
            self._continue_next.add(task.id)
            updated = self.require_task(task.id)
            next_counters = dataclasses.replace(
                counters,
                stall_recoveries=counters.stall_recoveries + 1,
            )
        elif isinstance(action, Handoff):
            self._force_handoff.add(task.id)
            updated = self.require_task(task.id)
            next_counters = dataclasses.replace(
                counters, context_handoffs=counters.context_handoffs + 1
            )
        elif isinstance(action, NextRung):
            return await self._apply_next_rung(task, step, role, result, action)
        else:
            return await self._raise_recovery_escalation(task, step, role, action.reason, result)
        return updated, False, next_counters

    async def _apply_next_rung(
        self, task: Task, step: Step, role: Role, result: TurnResult, action: NextRung
    ) -> tuple[Task | _TurnExecution, bool, TurnCounters]:
        if action.different_agent:
            self._force_handoff.add(task.id)
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
        updated = self.require_task(task.id)
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

    def _emit_turn_failed(self, task: Task, step: Step, result: TurnResult) -> None:
        if isinstance(result, Failed):
            message = result.message
            data = {"kind": result.kind.value, "resets_at": result.resets_at}
        elif isinstance(result, Stalled):
            message = "agent stalled"
            data = {}
        elif isinstance(result, ProtocolError):
            message = "agent protocol error"
            data = {}
        else:
            message = type(result).__name__
            data = {}
        data.update({"step": step.value, "result": type(result).__name__})
        self._events.append(EventKind.TURN_FAILED, task_id=task.id, message=message, data=data)

    async def raise_escalation(
        self,
        task: Task,
        step: Step | None,
        reason: EscalationReason,
        outcome: Outcome | None,
        *,
        question: str | None = None,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> None:
        """Build and persist the escalation packet for a task flow."""
        narrative_question, attempted = _escalation_narrative(reason, outcome, result)
        if question is None:
            question = narrative_question
        try:
            history = self._history.load(task.id)
        except Exception:
            _LOGGER.exception("could not load report history for escalation of %s", task.id)
            history = []
        await self._escalations.raise_escalation(
            task,
            step=step,
            reason=reason,
            question=question,
            attempted=attempted,
            reports=self._history.context(history),
            facts=await self._escalation_facts(task),
            view_tail=await self._view_tail(task, step, role_override=role),
        )

    async def _escalation_facts(self, task: Task) -> str:
        if task.repo is None:
            return ""
        try:
            return await self._facts_text(task)
        except ShikiruError as error:
            return FACTS_UNAVAILABLE.format(error=error)

    async def _view_tail(
        self, task: Task, step: Step | None, *, role_override: Role | None = None
    ) -> str:
        role = role_override
        if role is None and step is not None:
            role = ACTING_ROLE[step]
        if role is None:
            return ""
        path = role_dir(run_dir(self._project_root, task.id), role.value) / "view.jsonl"
        if not path.is_file():
            return ""
        return "\n".join(path.read_text().splitlines()[-ESCALATION_PANE_TAIL_LINES:])

    async def _run_turn(
        self,
        task: Task,
        step: Step,
        *,
        role_override: Role | None = None,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> _TurnExecution:
        role = role_override if role_override is not None else ACTING_ROLE[step]
        force_new = task.id in self._force_handoff
        selection = self._sessions.session_for(task, role, force_new=force_new)

        history = self._history.load(task.id)
        continue_turn = task.id in self._continue_next
        if continue_turn:
            template_override = PromptName.CONTINUE
        plan = await self._plan_turn(
            task,
            step,
            history,
            fresh=selection.handoff and not continue_turn,
            template_override=template_override,
            kind_override=kind_override,
            conflict_context=conflict_context,
        )
        if continue_turn:
            self._continue_next.discard(task.id)
        turn_request = TurnRequest(
            step=step,
            iteration=task.iteration,
            kind=plan.kind,
            template=plan.template,
            context=plan.context,
            facts=plan.facts,
        )
        usage_before = selection.run.usage
        self._events.append(
            EventKind.TURN_STARTED,
            task_id=task.id,
            message=f"{task.id} {role.value} turn started",
            data={
                "step": step.value,
                "role": role.value,
                "iteration": task.iteration,
                "agent": selection.rung.agent,
                "model": selection.rung.model,
            },
        )

        def on_nudge() -> None:
            self._events.append(
                EventKind.NUDGE,
                task_id=task.id,
                message=f"{task.id} {role.value} nudge",
                data={"step": step.value, "role": role.value},
            )

        result, turn_dir, selection = await self._turns.run(
            task, role, selection, turn_request, on_nudge=on_nudge
        )
        live_run = self._runs.get_live(task.id, role)
        if live_run is None:
            message = f"{task.id} {role.value} run disappeared after its turn"
            raise ShikiruError(message)
        return _TurnExecution(
            result=result,
            task_id=task.id,
            role=role,
            turn_dir=turn_dir,
            rung=selection.rung,
            usage_before=usage_before,
            usage_after=live_run.usage,
        )

    async def _plan_turn(
        self,
        task: Task,
        step: Step,
        history: list[tuple[str, Report]],
        *,
        fresh: bool,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
        conflict_context: str | None = None,
    ) -> _TurnPlan:
        template, kind = _template_for_step(step, task.iteration)
        if template_override is not None:
            template = template_override
        if kind_override is not None:
            kind = kind_override
        force_handoff = task.id in self._force_handoff
        context: dict[str, object] = {
            "request": task.request,
            "workspace": str(task_root(self._project_root, task.id)),
            "trackers": list(self._settings.project.trackers),
            "reports": self._history.context(history),
        }
        if template is PromptName.CONSULT:
            answer = (
                self._escalation_store.latest_answer(task.id)
                if task.counters.consults == 0
                else None
            )
            context["question"] = (
                answer if answer is not None else self._history.latest_question(history)
            )
        handoff = (fresh and bool(history)) or force_handoff
        needs_facts = template in (PromptName.VERIFY, PromptName.CONFLICTS) or handoff
        facts_text = await self._facts_text(task) if needs_facts else ""
        if handoff:
            template = PromptName.HANDOFF
        self._force_handoff.discard(task.id)
        if needs_facts:
            context["facts"] = facts_text
        if template is PromptName.VERIFY:
            context["base"] = _require_repo(task).base
            context["final_round"] = task.iteration >= self._settings.orchestration.max_iterations
        if template is PromptName.CONFLICTS:
            context["conflict"] = conflict_context or ""
        return _TurnPlan(template=template, kind=kind, context=context, facts=facts_text)

    async def _facts_text(self, task: Task) -> str:
        facts = await self._git_facts.collect(
            task_root(self._project_root, task.id),
            _require_repo(task),
            self._settings.project.trackers,
        )
        return render_facts(facts)

    async def apply(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
        """Apply a task transition and refresh its visible title."""
        if transition.enqueue_new_tasks:
            if outcome is None:
                message = f"{task.id}'s transition enqueues new tasks but has no outcome"
                raise ShikiruError(message)
            self._apply_new_tasks(task, transition, outcome)
        else:
            self._tasks.apply_transition(task.id, transition)
            if transition.status in (Status.DONE, Status.CANCELLED):
                self._tasks.set_slot(task.id, None)
            if isinstance(outcome, TriageReport):
                self._tasks.set_mode(task.id, outcome.mode)
        updated = self.require_task(task.id)
        if (
            task.step != updated.step
            and updated.step is not None
            and updated.status
            in (
                Status.RUNNING,
                Status.INTEGRATING,
            )
        ):
            self._emit_step_started(updated)
        await self._titles.refresh(updated)
        return updated

    def _apply_new_tasks(self, task: Task, transition: Transition, outcome: Outcome) -> None:
        if not isinstance(outcome, PlanReport | Verdict):
            message = f"{task.id}'s transition enqueues new tasks from a {type(outcome).__name__}"
            raise ShikiruError(message)
        children = self._tasks.apply_scope_change(
            task.id, transition, outcome.new_tasks, origin=TaskOrigin.LEADER
        )
        self._emit_scope_change(task, outcome, [child.id for child in children])

    async def _escalate_engine_error(self, task: Task, error: Exception) -> None:
        transition = Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
        await self._escalate(task, transition, None, question=str(error))

    async def _escalate_scope_change_protocol(
        self, task: Task, step: Step, outcome: Outcome, error: UsageError
    ) -> Task:
        self._emit_turn_failed(task, step, ProtocolError())
        transition = Transition.escalate(task, EscalationReason.AGENT_PROTOCOL, step=step)
        return await self._escalate(task, transition, outcome, question=str(error))

    def _emit_scope_change(
        self, task: Task, outcome: PlanReport | Verdict, new_ids: list[str]
    ) -> None:
        if not outcome.new_tasks and not outcome.scope_changes:
            return
        self._events.append(
            EventKind.SCOPE_CHANGE,
            task_id=task.id,
            message=f"{task.id} scope changed: {len(new_ids)} new task(s)",
            data={"new_task_ids": new_ids, "scope_changes": list(outcome.scope_changes)},
        )

    def _emit_step_started(self, task: Task) -> None:
        emit_step_started(self._events, task, self._settings)

    def _emit_turn_completed(self, task: Task, outcome: Outcome, execution: _TurnExecution) -> None:
        summary = _turn_summary(outcome)
        usage_delta = _usage_delta(execution.usage_before, execution.usage_after)
        self._events.append(
            EventKind.TURN_COMPLETED,
            task_id=task.id,
            message=summary,
            data={
                "step": task.step.value if task.step is not None else None,
                "iteration": task.iteration,
                "role": execution.role.value,
                "agent": execution.rung.agent,
                "model": execution.rung.model,
                "outcome": _turn_outcome(outcome),
                "summary": summary,
                "question": _turn_question(outcome),
                "usage_delta": usage_delta,
            },
        )
        if isinstance(outcome, Verdict):
            self._emit_verdict(task, outcome, summary)

    def _emit_verdict(self, task: Task, verdict: Verdict, summary: str) -> None:
        self._events.append(
            EventKind.VERDICT,
            task_id=task.id,
            message=summary,
            data={
                "decision": verdict.decision,
                "summary": summary,
                "issues": [issue.model_dump() for issue in verdict.issues],
                "residual_risks": list(verdict.residual_risks),
            },
        )

    async def _reject_uncommitted(self, task: Task, verdict: Verdict, turn_dir: Path) -> Verdict:
        facts = await self._git_facts.collect(
            task_root(self._project_root, task.id),
            _require_repo(task),
            self._settings.project.trackers,
        )
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
        self._emit_verdict(task, rejection, rejection.summary)
        return rejection

    def require_task(self, task_id: str) -> Task:
        """Reload a task or raise when it disappeared during its flow."""
        task = self._tasks.get(task_id)
        if task is None:
            message = f"{task_id} vanished while its flow was running"
            raise ShikiruError(message)
        return task

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
        updated = await self.apply(task, transition, outcome)
        await self.raise_escalation(
            updated,
            transition.step,
            transition.escalation_reason,
            outcome,
            question=question,
            result=result,
            role=role,
        )
        return updated


def _require_repo(task: Task) -> RepoState:
    if task.repo is None:
        message = f"{task.id} has no task root yet"
        raise ShikiruError(message)
    return task.repo
