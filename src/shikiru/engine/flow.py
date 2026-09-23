"""`TaskFlow`: runs a task's steps to `integrate` or an escalation (03-workflow.md §2-3).

Every rule of the review loop lives in `domain.steps.next_step`; this module only orchestrates
around it: resolving each step's turn, applying the resulting `Transition`, inserting any new
tasks a split/plan/supersede carries, raising an escalation when one is reached, and delegating
the integrating state to the injected coordinator.
"""

from __future__ import annotations

import asyncio
import dataclasses
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
    ClassifyScreen,
    Completed,
    Consult,
    Escalate,
    Failed,
    Handoff,
    KeepWaiting,
    NextRung,
    PaneDied,
    ProtocolError,
    RecoveryPolicy,
    Relaunch,
    RetryTurn,
    ScreenClassification,
    ScreenState,
    SendKeys,
    Stalled,
    TurnCounters,
    TurnResult,
    WaitForQuota,
    decide,
    decide_stall,
    quota_block_until,
)
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    PROMPT_FILENAME,
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.steps import ACTING_ROLE, Transition, first_step, next_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Step, TaskOrigin
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationConflict,
    IntegrationCoordinator,
    IntegrationMetadata,
)
from shikiru.engine.turns import TURNS_DIR_NAME, TurnSpec, read_turn_report
from shikiru.errors import CommandFailed, ShikiruError, UsageError
from shikiru.helper.helper import Helper
from shikiru.helper.models import Unrecoverable
from shikiru.prompts.renderer import PromptName
from shikiru.workspace.facts import render_facts
from shikiru.workspace.roots import run_dir, task_root

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, Rung, Usage
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.reports import NewTask, Report
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import RepoState, Task
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.monitor import InjectedLines, MonitorSignal
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.titles import TaskTitles
    from shikiru.engine.turns import TurnRunner
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.pane import TmuxPane
    from shikiru.workspace.facts import GitFacts


ESCALATION_PANE_TAIL_LINES = 60
"""Lines of pane scrollback captured as escalation evidence (03 §8)."""

CONTINUE_INPUT = "continue"
_QUOTA_WAIT_EXHAUSTED_MESSAGE = "quota wait exceeded"


class _IntegrationTurnStopped(Exception):
    pass


def clear_intervention(tasks: TaskStore, events: EventLog, task_id: str) -> None:
    """Clear `task_id`'s intervention hold: on a valid report, or an operator resume (03 §9)."""
    task = tasks.get(task_id)
    if task is None:
        message = f"{task_id} has no task row; clear_intervention needs a running task"
        raise ShikiruError(message)
    if Hold.INTERVENTION not in task.holds:
        return
    tasks.set_holds(task_id, task.holds - {Hold.INTERVENTION})
    events.append(
        EventKind.HOLD_CLEARED,
        task_id=task_id,
        message=f"{task_id} intervention hold cleared",
        data={"hold": Hold.INTERVENTION.value},
    )


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


def _turn_dirs(task_run_dir: Path) -> list[Path]:
    turns_root = task_run_dir / TURNS_DIR_NAME
    if not turns_root.is_dir():
        return []
    return sorted((entry for entry in turns_root.iterdir() if entry.is_dir()), key=lambda p: p.name)


def load_history(task_run_dir: Path) -> list[tuple[str, Report]]:
    """Load every completed turn's report under `task_run_dir`, oldest first (03 §2's "reports").

    Shares `engine.turns.read_turn_report`'s "invalid means missing" reading: a turn whose
    `report.json`/`helper.json` doesn't parse or validate is dropped from the history the same
    way a live turn would treat it as "not done yet".
    """
    history: list[tuple[str, Report]] = []
    for turn_dir in _turn_dirs(task_run_dir):
        report = read_turn_report(turn_dir)
        if report is not None:
            history.append((turn_dir.name, report))
    return history


def _history_context(history: Sequence[tuple[str, Report]]) -> list[dict[str, str]]:
    return [{"label": label, "json": report.model_dump_json()} for label, report in history]


def _latest_question(history: Sequence[tuple[str, Report]]) -> str:
    if not history:
        return ""
    _, last = history[-1]
    return last.question if isinstance(last, ImplementationReport) else ""


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


def _new_tasks_for(outcome: Outcome) -> list[NewTask]:
    if isinstance(outcome, TriageReport):
        return list(outcome.subtasks)
    if isinstance(outcome, PlanReport | Verdict):
        return list(outcome.new_tasks)
    return []


def _split_mode_mismatch(task: Task, step: Step, outcome: Outcome) -> bool:
    return (
        step is Step.TRIAGE
        and task.mode is Mode.SPLIT
        and isinstance(outcome, TriageReport)
        and outcome.mode is not Mode.SPLIT
    )


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
        question = "The agent's screen stopped progressing."
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
    pane: TmuxPane
    signals: asyncio.Queue[MonitorSignal]
    turn_dir: Path
    spec: TurnSpec
    helper: Helper
    session: AgentSession
    prompt_path: Path
    injected: InjectedLines
    intervention_active: Callable[[], bool]
    rung: Rung
    usage_before: Usage
    usage_after: Usage
    on_nudge: Callable[[], None]
    closed: bool = False
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
        titles: TaskTitles,
        settings: Settings,
        agent_pool: AgentPool,
        pane_factory: Callable[[str], TmuxPane],
        project_root: Path,
        quota: QuotaStore,
        clock: Clock,
        integrator: IntegrationCoordinator,
        runs: RunStore,
    ) -> None:
        """Wire every collaborator a step turn and its recovery actions need."""
        self._tasks = tasks
        self._events = events
        self._sessions = sessions
        self._turns = turns
        self._git_facts = git_facts
        self._escalations = escalations
        self._titles = titles
        self._settings = settings
        self._agent_pool = agent_pool
        self._pane_factory = pane_factory
        self._project_root = project_root
        self._quota = quota
        self._clock = clock
        self._integrator = integrator
        self._runs = runs
        self._recovery_questions: dict[str, str] = {}
        self._force_handoff: set[str] = set()
        self._integration_context: dict[str, str] = {}

    async def run(self, task_id: str) -> None:
        """Run `task_id` from its current step (or its first) until `integrate` or an escalation."""
        task = self._require_task(task_id)
        if task.step is None:
            task = await self._apply(task, first_step(task), None)
        if task.status is Status.RUNNING and task.step is Step.INTEGRATE:
            task = await self._apply(
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
            task, recovery_counters, should_return = await self._run_step(
                task, step, recovery_counters
            )
            if should_return:
                return
        if task.status is Status.INTEGRATING:
            await self._run_integration(task)

    async def _run_integration(self, task: Task) -> None:
        metadata = self._integration_metadata(task)

        async def resolve_conflicts(
            current: Task, repo: RepoState, continue_rebase: Callable[[], Awaitable[None]]
        ) -> Task:
            resolved = await self._resolve_conflicts(current, repo, continue_rebase)
            refreshed = self._integration_metadata(resolved)
            metadata.summary = refreshed.summary
            metadata.residual_risks = refreshed.residual_risks
            return resolved

        try:
            await self._integrator.run(task, resolve_conflicts, metadata)
        except _IntegrationTurnStopped:
            return
        except (IntegrationBlocked, IntegrationConflict) as error:
            current = self._require_task(task.id)
            transition = dataclasses.replace(
                Transition.stay(current, step=Step.INTEGRATE, status=Status.ESCALATED),
                escalation_reason=error.reason,
            )
            self._tasks.apply_transition(current.id, transition)
            updated = self._require_task(current.id)
            await self._titles.refresh(updated)
            self._recovery_questions[current.id] = str(error)
            await self._raise_escalation(
                updated,
                Step.INTEGRATE,
                error.reason,
                None,
            )
        finally:
            self._integration_context.pop(task.id, None)

    async def _resolve_conflicts(
        self,
        task: Task,
        repo: RepoState,
        continue_rebase: Callable[[], Awaitable[None]],
    ) -> Task:
        del repo
        current = task
        context = self._integration_context.pop(task.id, "")
        try:
            while True:
                self._integration_context[current.id] = context
                current, _, stopped = await self._run_step(current, Step.INTEGRATE, TurnCounters())
                if stopped:
                    raise _IntegrationTurnStopped
                latest = self._latest_report(current)
                if not isinstance(latest, ImplementationReport):
                    message = f"{current.id} conflicts turn produced no implementation report"
                    raise ShikiruError(message)
                if latest.status != "completed":
                    if current.step is not Step.CONSULT:
                        message = f"{current.id} conflict report did not route to consult"
                        raise ShikiruError(message)
                    current, _, stopped = await self._run_step(
                        current, Step.CONSULT, TurnCounters()
                    )
                    if stopped:
                        raise _IntegrationTurnStopped
                    answer = self._latest_report(current)
                    if not isinstance(answer, ConsultAnswer):
                        message = f"{current.id} integration consult produced no answer"
                        raise ShikiruError(message)
                    context = answer.answer
                    continue
                await continue_rebase()
                current, verdict = await self._verify_conflicts(current, context)
                if verdict.decision == "accept":
                    return current
                context = self._integration_context.get(current.id, context)
        finally:
            self._integration_context.pop(task.id, None)

    async def _verify_conflicts(self, task: Task, context: str) -> tuple[Task, Verdict]:
        self._integration_context[task.id] = context
        current, outcome = await self._run_report_turn(
            task,
            role=Role.LEADER,
            template=PromptName.VERIFY,
            kind=ReportKind.VERDICT,
        )
        if not isinstance(outcome, Verdict):
            message = f"{task.id} post-rebase turn produced no verdict"
            raise ShikiruError(message)
        transition = next_step(current, outcome, self._settings)
        escalated = transition.status is Status.ESCALATED
        updated = await self._apply(current, transition, outcome)
        if escalated:
            reason = transition.escalation_reason
            if reason is None:
                message = f"{task.id} post-rebase verdict escalated without a reason"
                raise ShikiruError(message)
            await self._raise_escalation(
                updated, transition.step, reason, outcome, role=Role.LEADER
            )
            raise _IntegrationTurnStopped
        if outcome.decision == "changes_requested":
            self._integration_context[updated.id] = "; ".join(
                issue.description for issue in outcome.issues
            )
        return updated, outcome

    async def _run_report_turn(
        self,
        task: Task,
        *,
        role: Role,
        template: PromptName,
        kind: ReportKind,
    ) -> tuple[Task, ImplementationReport | Verdict]:
        execution: _TurnExecution | None = None
        current = task
        counters = TurnCounters()
        try:
            execution = await self._run_turn(
                current,
                Step.INTEGRATE,
                role_override=role,
                template_override=template,
                kind_override=kind,
            )
            while True:
                if isinstance(execution.result, Completed):
                    await self._close_execution(execution)
                    await self._clear_intervention(current.id, role)
                    report = execution.result.report
                    if isinstance(report, ImplementationReport | Verdict):
                        self._emit_turn_completed(current, report, execution)
                        return current, report
                    message = f"{current.id} integration turn returned an unexpected report"
                    raise ShikiruError(message)
                current, counters, stopped, continued = await self._recover(
                    current,
                    Step.INTEGRATE,
                    execution,
                    counters,
                    role_override=role,
                )
                if stopped:
                    raise _IntegrationTurnStopped
                if continued is not None:
                    execution = continued
                    continue
                execution = await self._run_turn(
                    current,
                    Step.INTEGRATE,
                    role_override=role,
                    template_override=template,
                    kind_override=kind,
                )
        finally:
            if execution is not None:
                await self._close_execution(execution)

    def _integration_metadata(self, task: Task) -> IntegrationMetadata:
        history = load_history(run_dir(self._project_root, task.id))
        for _, report in reversed(history):
            if isinstance(report, Verdict) and report.decision == "accept":
                return IntegrationMetadata(
                    summary=report.summary,
                    residual_risks=tuple(report.residual_risks),
                )
        for _, report in reversed(history):
            if isinstance(report, PlanReport):
                return IntegrationMetadata(summary=report.summary)
        return IntegrationMetadata(summary=task.title)

    def _latest_report(self, task: Task) -> Report | None:
        history = load_history(run_dir(self._project_root, task.id))
        return history[-1][1] if history else None

    async def _clear_intervention(self, task_id: str, role: Role) -> None:
        clear_intervention(self._tasks, self._events, task_id)
        self._sessions.clear_intervention(task_id, role)
        await self._titles.refresh(self._require_task(task_id))

    def _intervention_active(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return task is not None and Hold.INTERVENTION in task.holds

    async def _run_step(
        self, task: Task, step: Step, counters: TurnCounters
    ) -> tuple[Task, TurnCounters, bool]:
        task = self._require_task(task.id)
        if self._step_blocked(task):
            return task, counters, True
        return await self._run_step_turn(task, step, counters)

    @staticmethod
    def _step_blocked(task: Task) -> bool:
        return Hold.PAUSED in task.holds or Hold.INTERVENTION in task.holds

    async def _run_step_turn(
        self, task: Task, step: Step, counters: TurnCounters
    ) -> tuple[Task, TurnCounters, bool]:
        execution: _TurnExecution | None = None
        try:
            execution = await self._run_turn(task, step)
            while True:
                result = execution.result
                if not isinstance(result, Completed):
                    task, counters, should_return, continued = await self._recover(
                        task, step, execution, counters
                    )
                    if should_return:
                        return task, counters, True
                    if continued is not None:
                        execution = continued
                    else:
                        recovery_step = task.step
                        if recovery_step is None:
                            message = f"{task.id} recovery left the task with no step"
                            raise ShikiruError(message)
                        task = self._require_task(task.id)
                        if self._step_blocked(task):
                            return task, counters, True
                        step = recovery_step
                        execution = await self._run_turn(task, step)
                    continue
                await self._close_execution(execution)
                return await self._complete_step_turn(task, step, execution)
        finally:
            if execution is not None:
                await self._close_execution(execution)

    async def _complete_step_turn(
        self, task: Task, step: Step, execution: _TurnExecution
    ) -> tuple[Task, TurnCounters, bool]:
        result = execution.result
        if not isinstance(result, Completed):
            message = f"{task.id} completed-step handler received {type(result).__name__}"
            raise ShikiruError(message)
        outcome = result.report
        if _split_mode_mismatch(task, step, outcome):
            return await self._reject_split_mode(task, step, execution, TurnCounters())
        await self._clear_intervention(task.id, ACTING_ROLE[step])
        self._emit_turn_completed(task, outcome, execution)
        transition = next_step(task, outcome, self._settings)
        escalated = transition.status is Status.ESCALATED
        escalation_step, reason = transition.step, transition.escalation_reason
        try:
            task = await self._apply(task, transition, outcome)
        except UsageError as error:
            task = await self._escalate_scope_change_protocol(task, step, outcome, error)
            return task, TurnCounters(), True
        if escalated:
            if reason is None:
                message = f"{task.id} escalated with no escalation_reason on its transition"
                raise ShikiruError(message)
            await self._raise_escalation(task, escalation_step, reason, outcome)
            return task, TurnCounters(), True
        return task, TurnCounters(), False

    async def _reject_split_mode(
        self,
        task: Task,
        step: Step,
        execution: _TurnExecution,
        counters: TurnCounters,
    ) -> tuple[Task, TurnCounters, bool]:
        rejected = dataclasses.replace(execution, result=ProtocolError())
        updated, next_counters, should_return, _continued = await self._recover(
            task, step, rejected, counters
        )
        if not should_return:
            message = f"{task.id} split triage protocol recovery did not stop"
            raise ShikiruError(message)
        return updated, next_counters, True

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
        if isinstance(result, Stalled) and self._intervention_active(task.id):
            return self._require_task(task.id), counters, True, None
        self._emit_turn_failed(task, step, result)
        role = role_override if role_override is not None else ACTING_ROLE[step]
        policy = self._recovery_policy(
            task, role, quota_wait_exhausted=execution.quota_wait_exhausted
        )
        action = decide(result, counters, policy)
        if isinstance(action, ClassifyScreen):
            classification = await self._classify_screen(task, role, result)
            counters = dataclasses.replace(counters, stall_recoveries=counters.stall_recoveries + 1)
            if isinstance(classification, Unrecoverable):
                action = Escalate(EscalationReason.AGENT_STUCK)
            else:
                self._emit_helper_action(task, classification)
                policy = self._recovery_policy(task, role)
                action = decide_stall(
                    classification,
                    role,
                    frozenset(
                        self._agent_pool.get(pick_rung(task, role, self._settings).agent).keys.safe
                    ),
                    counters,
                    policy,
                )
                if isinstance(action, Consult):
                    self._recovery_questions[task.id] = classification.text
                if classification.state is ScreenState.QUOTA:
                    self._block_quota(task, role, classification.resets_at)
        elif isinstance(result, Failed) and result.kind is FailureKind.QUOTA:
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
        next_task = self._require_task(task.id) if isinstance(updated, _TurnExecution) else updated
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
                    quota_wait_exhausted=(
                        isinstance(terminal, Failed) and terminal.kind is FailureKind.QUOTA
                    ),
                )
            else:
                updated = await self._resume_execution(execution)
        elif isinstance(action, Backoff):
            await self._close_execution(execution)
            await self._clock.sleep(action.delay.total_seconds())
            updated = self._require_task(task.id)
            next_counters = dataclasses.replace(
                counters, unavailable_retries=counters.unavailable_retries + 1
            )
        elif isinstance(action, RetryTurn):
            await self._close_execution(execution)
            next_counters = dataclasses.replace(counters, other_retries=counters.other_retries + 1)
        elif isinstance(action, Relaunch):
            await self._close_execution(execution)
            await self._sessions.ensure(task, role)
            updated = self._require_task(task.id)
            next_counters = dataclasses.replace(counters, relaunches=counters.relaunches + 1)
        elif isinstance(action, Handoff):
            await self._close_execution(execution)
            self._force_handoff.add(task.id)
            await self._sessions.handoff(task, role)
            updated = self._require_task(task.id)
            next_counters = dataclasses.replace(
                counters, context_handoffs=counters.context_handoffs + 1
            )
        elif isinstance(action, NextRung):
            await self._close_execution(execution)
            return await self._apply_next_rung(task, step, role, result, action)
        else:
            if not isinstance(action, KeepWaiting | SendKeys):
                await self._close_execution(execution)
            return await self._apply_recovery_secondary(
                task,
                step,
                role,
                execution=execution,
                result=result,
                counters=counters,
                action=action,
            )
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

    async def _apply_recovery_secondary(
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
        if isinstance(action, ClassifyScreen):
            message = f"{task.id} recovery classification was not resolved"
            raise ShikiruError(message)
        if isinstance(action, KeepWaiting):
            self._sessions.rearm_stall_detection(task.id, role)
            await self._clock.sleep(self._settings.orchestration.poll_seconds)
            if self._sessions.launch_pending(task.id, role):
                await self._close_execution(execution)
                return self._require_task(task.id), False, counters
            return await self._resume_execution(execution), False, counters
        if isinstance(action, SendKeys):
            return await self._apply_recovery_keys(task, role, execution, counters, action.keys)
        if isinstance(action, Consult):
            self._recovery_questions.setdefault(task.id, "")
            base = Transition.stay(task, step=Step.CONSULT, status=task.status)
            self._tasks.apply_transition(task.id, base)
            updated = self._require_task(task.id)
            await self._titles.refresh(updated)
            return updated, False, counters
        if isinstance(action, Escalate):
            return await self._raise_recovery_escalation(task, step, role, action.reason, result)
        message = f"unsupported recovery action {action!r}"
        raise ShikiruError(message)

    async def _apply_recovery_keys(
        self,
        task: Task,
        role: Role,
        execution: _TurnExecution,
        counters: TurnCounters,
        keys: tuple[str, ...],
    ) -> tuple[Task | _TurnExecution, bool, TurnCounters]:
        pane_id = task.panes.get(role)
        if pane_id is None:
            message = f"{task.id} has no pane assigned for {role.value} recovery"
            raise ShikiruError(message)
        try:
            await self._pane_factory(pane_id).send_keys(*keys)
        except CommandFailed:
            return dataclasses.replace(execution, result=PaneDied()), False, counters
        if self._sessions.launch_pending(task.id, role):
            await self._close_execution(execution)
            return self._require_task(task.id), False, counters
        return await self._resume_execution(execution), False, counters

    async def _raise_recovery_escalation(
        self, task: Task, step: Step, role: Role, reason: EscalationReason, result: TurnResult
    ) -> tuple[Task, bool, TurnCounters]:
        transition = dataclasses.replace(
            Transition.stay(task, step=step, status=Status.ESCALATED),
            escalation_reason=reason,
        )
        self._tasks.apply_transition(task.id, transition)
        updated = self._require_task(task.id)
        await self._titles.refresh(updated)
        await self._raise_escalation(updated, step, reason, None, result=result, role=role)
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
        updated = self._require_task(task.id)
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

    async def _wait_for_quota(
        self, task: Task, role: Role, until: datetime
    ) -> Failed | PaneDied | Stalled | None:
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
            return await self._send_injected_line(task, role, CONTINUE_INPUT)

    async def _send_injected_line(
        self, task: Task, role: Role, line: str
    ) -> PaneDied | Stalled | None:
        try:
            return await self._sessions.inject_line(task, role, line)
        except ShikiruError:
            return PaneDied()

    async def _classify_screen(
        self, task: Task, role: Role, result: TurnResult
    ) -> ScreenClassification | Unrecoverable:
        if not isinstance(result, Stalled):
            message = f"{task.id} received a non-stalled result for screen classification"
            raise ShikiruError(message)
        helper_rung = pick_rung(task, Role.HELPER, self._settings)
        helper = Helper(self._agent_pool.get(helper_rung.agent))
        keys = self._agent_pool.get(pick_rung(task, role, self._settings).agent).keys.safe
        try:
            return await helper.classify_screen(
                result.screen,
                keys,
                model=helper_rung.model,
                effort=helper_rung.effort,
                cwd=task_root(self._project_root, task.id),
                timeout=timedelta(seconds=self._settings.orchestration.timeouts.helper),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return Unrecoverable(unrecoverable=str(error))

    def _emit_helper_action(self, task: Task, classification: ScreenClassification) -> None:
        self._events.append(
            EventKind.HELPER_ACTION,
            task_id=task.id,
            message=f"screen classified as {classification.state.value}",
            data={
                "state": classification.state.value,
                "keys": classification.keys,
                "text": classification.text,
                "resets_at": (
                    classification.resets_at.isoformat()
                    if classification.resets_at is not None
                    else None
                ),
            },
        )

    def _emit_turn_failed(self, task: Task, step: Step, result: TurnResult) -> None:
        if isinstance(result, Failed):
            message = result.message
            data = {"kind": result.kind.value, "resets_at": result.resets_at}
        elif isinstance(result, Stalled):
            message = "agent stalled"
            data = {"screen": result.screen}
        elif isinstance(result, ProtocolError):
            message = "agent protocol error"
            data = {}
        else:
            message = type(result).__name__
            data = {}
        data.update({"step": step.value, "result": type(result).__name__})
        self._events.append(EventKind.TURN_FAILED, task_id=task.id, message=message, data=data)

    async def _raise_escalation(
        self,
        task: Task,
        step: Step | None,
        reason: EscalationReason,
        outcome: Outcome | None,
        *,
        result: TurnResult | None = None,
        role: Role | None = None,
    ) -> None:
        question = self._recovery_questions.pop(task.id, None)
        narrative_question, attempted = _escalation_narrative(reason, outcome, result)
        if question is None:
            question = narrative_question
        history = load_history(run_dir(self._project_root, task.id))
        await self._escalations.raise_escalation(
            task,
            step=step,
            reason=reason,
            question=question,
            attempted=attempted,
            reports=_history_context(history),
            facts=await self._facts_text(task),
            pane_tail=await self._pane_tail(task, step, role_override=role),
        )

    async def _pane_tail(
        self, task: Task, step: Step | None, *, role_override: Role | None = None
    ) -> str:
        role = role_override
        if role is None and step is not None:
            role = ACTING_ROLE[step]
        pane_id = task.panes.get(role) if role is not None else None
        if pane_id is None:
            return ""
        pane = self._pane_factory(pane_id)
        return await pane.capture_tail(ESCALATION_PANE_TAIL_LINES)

    async def _run_turn(
        self,
        task: Task,
        step: Step,
        *,
        role_override: Role | None = None,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
    ) -> _TurnExecution:
        role = role_override if role_override is not None else ACTING_ROLE[step]
        session = await self._sessions.ensure(task, role)
        fresh = self._sessions.launched_fresh(task.id, role)

        def intervention_active() -> bool:
            return self._intervention_active(task.id)

        task_run_dir = run_dir(self._project_root, task.id)
        history = load_history(task_run_dir)
        plan = await self._plan_turn(
            task,
            step,
            history,
            fresh=fresh,
            template_override=template_override,
            kind_override=kind_override,
        )
        rung = pick_rung(task, role, self._settings)
        run = self._runs.get_live(task.id, role)
        if run is None:
            message = f"{task.id} has no live {role.value} run"
            raise ShikiruError(message)
        usage_before = await session.usage()
        helper_rung = pick_rung(task, Role.HELPER, self._settings)
        spec = TurnSpec(
            step=step,
            iteration=task.iteration,
            kind=plan.kind,
            template=plan.template,
            context=plan.context,
            prompt_prefix=self._settings.agents[rung.agent].prompt_prefix,
            facts=plan.facts,
            cwd=task_root(self._project_root, task.id),
            helper_model=helper_rung.model,
            helper_effort=helper_rung.effort,
            helper_timeout=timedelta(seconds=self._settings.orchestration.timeouts.helper),
        )
        pane = self._pane_factory(task.panes[role])
        queue = self._sessions.subscribe(task.id, role)
        try:
            helper = Helper(self._agent_pool.get(helper_rung.agent))
            injected = self._sessions.injected_lines(task.id, role)

            def on_nudge() -> None:
                self._events.append(
                    EventKind.NUDGE,
                    task_id=task.id,
                    message=f"{task.id} {role.value} nudge",
                    data={"step": step.value, "role": role.value},
                )

            self._events.append(
                EventKind.TURN_STARTED,
                task_id=task.id,
                message=f"{task.id} {role.value} turn started",
                data={
                    "step": step.value,
                    "role": role.value,
                    "iteration": task.iteration,
                    "agent": rung.agent,
                    "model": rung.model,
                },
            )
            result = await self._turns.run(
                session,
                pane,
                queue,
                run_dir=task_run_dir,
                role=role,
                spec=spec,
                helper=helper,
                injected=injected,
                intervention_active=intervention_active,
                on_nudge=on_nudge,
            )
            usage_after = await session.usage()
            turn_dir = (role_dir(task_run_dir, role.value) / CURRENT_TURN_LINK_NAME).resolve()
            return _TurnExecution(
                result=result,
                task_id=task.id,
                role=role,
                pane=pane,
                signals=queue,
                turn_dir=turn_dir,
                spec=spec,
                helper=helper,
                session=session,
                prompt_path=turn_dir / PROMPT_FILENAME,
                injected=injected,
                intervention_active=intervention_active,
                rung=rung,
                usage_before=usage_before,
                usage_after=usage_after,
                on_nudge=on_nudge,
            )
        except asyncio.CancelledError:
            self._sessions.unsubscribe(task.id, role, queue)
            raise
        except Exception:
            self._sessions.unsubscribe(task.id, role, queue)
            raise

    async def _resume_execution(self, execution: _TurnExecution) -> _TurnExecution:
        result = await self._turns.resume(
            execution.pane,
            execution.signals,
            turn_dir=execution.turn_dir,
            spec=execution.spec,
            helper=execution.helper,
            prompt_path=execution.prompt_path,
            injected=execution.injected,
            intervention_active=execution.intervention_active,
            on_nudge=execution.on_nudge,
        )
        usage_after = await execution.session.usage()
        return dataclasses.replace(execution, result=result, usage_after=usage_after)

    async def _close_execution(self, execution: _TurnExecution) -> None:
        if execution.closed:
            return
        self._sessions.unsubscribe(execution.task_id, execution.role, execution.signals)
        execution.closed = True

    async def _plan_turn(
        self,
        task: Task,
        step: Step,
        history: list[tuple[str, Report]],
        *,
        fresh: bool,
        template_override: PromptName | None = None,
        kind_override: ReportKind | None = None,
    ) -> _TurnPlan:
        template, kind = _template_for_step(step, task.iteration)
        if template_override is not None:
            template = template_override
        if kind_override is not None:
            kind = kind_override
        force_handoff = task.id in self._force_handoff
        context: dict[str, object] = {
            "request": task.request,
            "trackers": list(self._settings.project.trackers),
            "reports": _history_context(history),
            "required_mode": (
                Mode.SPLIT.value if step is Step.TRIAGE and task.mode is Mode.SPLIT else ""
            ),
        }
        if template is PromptName.CONSULT:
            context["question"] = self._recovery_questions.pop(task.id, _latest_question(history))
        handoff = (fresh and bool(history)) or force_handoff
        needs_facts = template in (PromptName.VERIFY, PromptName.CONFLICTS) or handoff
        facts_text = await self._facts_text(task) if needs_facts else ""
        if handoff:
            template = PromptName.HANDOFF
        self._force_handoff.discard(task.id)
        if needs_facts:
            context["facts"] = facts_text
        if template is PromptName.VERIFY:
            context["final_round"] = task.iteration >= self._settings.orchestration.max_iterations
        if template is PromptName.CONFLICTS:
            context["conflict"] = self._integration_context.get(task.id, "")
        return _TurnPlan(template=template, kind=kind, context=context, facts=facts_text)

    async def _facts_text(self, task: Task) -> str:
        facts = await self._git_facts.collect(
            task_root(self._project_root, task.id), task.repos, self._settings.project.trackers
        )
        return render_facts(facts)

    async def _apply(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
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
        updated = self._require_task(task.id)
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
        new_tasks = _new_tasks_for(outcome)
        origin = TaskOrigin.SPLIT if isinstance(outcome, TriageReport) else TaskOrigin.LEADER
        selected_mode = outcome.mode if isinstance(outcome, TriageReport) else None
        children = self._tasks.apply_scope_change(
            task.id,
            transition,
            new_tasks,
            origin=origin,
            mode=selected_mode,
        )
        new_ids = [child.id for child in children]
        self._emit_scope_change(task, outcome, new_ids)

    async def _escalate_scope_change_protocol(
        self, task: Task, step: Step, outcome: Outcome, error: UsageError
    ) -> Task:
        self._emit_turn_failed(task, step, ProtocolError())
        transition = dataclasses.replace(
            Transition.stay(task, step=step, status=Status.ESCALATED),
            escalation_reason=EscalationReason.AGENT_PROTOCOL,
        )
        self._tasks.apply_transition(task.id, transition)
        updated = self._require_task(task.id)
        await self._titles.refresh(updated)
        self._recovery_questions[task.id] = str(error)
        await self._raise_escalation(
            updated,
            step,
            EscalationReason.AGENT_PROTOCOL,
            outcome,
        )
        return updated

    def _emit_scope_change(self, task: Task, outcome: Outcome, new_ids: list[str]) -> None:
        if not isinstance(outcome, PlanReport | Verdict):
            return
        if not outcome.new_tasks and not outcome.scope_changes:
            return
        self._events.append(
            EventKind.SCOPE_CHANGE,
            task_id=task.id,
            message=f"{task.id} scope changed: {len(new_ids)} new task(s)",
            data={"new_task_ids": new_ids, "scope_changes": list(outcome.scope_changes)},
        )

    def _emit_step_started(self, task: Task) -> None:
        if task.step is None:
            return
        role = ACTING_ROLE[task.step]
        rung = pick_rung(task, role, self._settings)
        self._events.append(
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
            self._events.append(
                EventKind.VERDICT,
                task_id=task.id,
                message=summary,
                data={
                    "decision": outcome.decision,
                    "summary": summary,
                    "issues": [issue.model_dump() for issue in outcome.issues],
                    "residual_risks": list(outcome.residual_risks),
                },
            )

    def _require_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            message = f"{task_id} vanished while its flow was running"
            raise ShikiruError(message)
        return task

    def set_consult_question(self, task_id: str, question: str) -> None:
        """Set the Operator's answer consumed by the next consult turn."""
        self._recovery_questions[task_id] = question
