"""`TaskFlow`: runs a task's steps to `integrate` or an escalation (03-workflow.md §2-3).

Every rule of the review loop lives in `domain.steps.next_step`; this module only orchestrates
around it: resolving each step's turn, applying the resulting `Transition`, inserting any new
tasks a split/plan/supersede carries, and raising an escalation when one is reached. The
`integrate` step's own turn (rebase, conflicts, merge strategy) is Task 16's job, so the loop
stops the moment a task's step becomes `integrate` or its status leaves `running`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.ladder import pick_rung
from shikiru.domain.recovery import Completed, TurnResult
from shikiru.domain.reports import (
    ConsultAnswer,
    ImplementationReport,
    PlanReport,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.steps import ACTING_ROLE, first_step, next_step
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status, Step, TaskOrigin
from shikiru.engine.turns import TURNS_DIR_NAME, TurnSpec, read_turn_report
from shikiru.errors import ShikiruError
from shikiru.helper.helper import Helper
from shikiru.prompts.renderer import PromptName
from shikiru.workspace.facts import render_facts
from shikiru.workspace.roots import run_dir, task_root

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.reports import NewTask, Report
    from shikiru.domain.steps import Outcome, Transition
    from shikiru.domain.tasks import Task
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.escalations import EscalationRaiser
    from shikiru.engine.sessions import SessionManager
    from shikiru.engine.titles import TaskTitles
    from shikiru.engine.turns import TurnRunner
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.pane import TmuxPane
    from shikiru.workspace.facts import GitFacts


class RecoveryNotImplementedError(ShikiruError):
    """A turn ended without a valid report; recovering from that is Task 15's job (03 §7)."""


def clear_intervention(tasks: TaskStore, events: EventLog, task_id: str) -> None:
    """Clear `task_id`'s intervention hold: on a valid report, or an operator resume (03 §9)."""
    task = tasks.get(task_id)
    if task is None or Hold.INTERVENTION not in task.holds:
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


def _new_tasks_for(outcome: Outcome) -> list[NewTask]:
    if isinstance(outcome, TriageReport):
        return list(outcome.subtasks)
    if isinstance(outcome, PlanReport | Verdict):
        return list(outcome.new_tasks)
    return []


def _resolve_ref(ref: str, sibling_ids: list[str]) -> str:
    return sibling_ids[int(ref[1:]) - 1] if ref.startswith("#") else ref


def _escalation_narrative(reason: EscalationReason, outcome: Outcome) -> tuple[str, str]:
    if reason is EscalationReason.ITERATIONS_EXHAUSTED:
        return (
            "The final round still requested changes; choose accept, supersede or cancel.",
            "Implement and verify cycled to the configured iteration limit.",
        )
    if isinstance(outcome, ConsultAnswer) and outcome.escalation is not None:
        return outcome.escalation.question, "The Leader escalated during a consult turn."
    if isinstance(outcome, Verdict) and outcome.escalation is not None:
        return outcome.escalation.question, "The Leader escalated during verification."
    if isinstance(outcome, ImplementationReport):
        return outcome.question, "The Implementer's question exceeded the consult limit."
    return "An Operator decision is needed to continue.", "Automatic recovery has no option left"


@dataclass(frozen=True)
class _TurnPlan:
    template: PromptName
    kind: ReportKind
    context: dict[str, object]
    facts: str


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
    ) -> None:
        """Wire every collaborator a step turn needs: sessions, turns, facts, titles, escalation."""
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

    async def run(self, task_id: str) -> None:
        """Run `task_id` from its current step (or its first) until `integrate` or an escalation."""
        task = self._require_task(task_id)
        if task.step is None:
            task = await self._apply(task, first_step(task), None)
        while task.status is Status.RUNNING:
            step = task.step
            if step is None:
                message = f"{task.id} is running with no step set"
                raise ShikiruError(message)
            result = await self._run_turn(task, step)
            if not isinstance(result, Completed):
                message = f"{task.id} step {step.value} ended without a report: {result!r}"
                raise RecoveryNotImplementedError(message)
            clear_intervention(self._tasks, self._events, task.id)
            outcome = result.report
            transition = next_step(task, outcome, self._settings)
            escalated = transition.status is Status.ESCALATED
            escalation_step, reason = transition.step, transition.escalation_reason
            task = await self._apply(task, transition, outcome)
            if escalated:
                if reason is None:
                    message = f"{task.id} escalated with no escalation_reason on its transition"
                    raise ShikiruError(message)
                await self._raise_escalation(task, escalation_step, reason, outcome)
                return

    async def _raise_escalation(
        self, task: Task, step: Step | None, reason: EscalationReason, outcome: Outcome
    ) -> None:
        question, attempted = _escalation_narrative(reason, outcome)
        history = load_history(run_dir(self._project_root, task.id))
        await self._escalations.raise_escalation(
            task,
            step=step,
            reason=reason,
            question=question,
            attempted=attempted,
            reports=_history_context(history),
            facts="",
        )

    async def _run_turn(self, task: Task, step: Step) -> TurnResult:
        role = ACTING_ROLE[step]
        session = await self._sessions.ensure(task, role)
        fresh = self._sessions.launched_fresh(task.id, role)
        task_run_dir = run_dir(self._project_root, task.id)
        history = load_history(task_run_dir)
        plan = await self._plan_turn(task, step, history, fresh=fresh and bool(history))
        rung = pick_rung(task, role, self._settings)
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
        helper = Helper(self._agent_pool.get(helper_rung.agent))
        try:
            return await self._turns.run(
                session,
                pane,
                queue,
                run_dir=task_run_dir,
                role=role,
                spec=spec,
                helper=helper,
                injected=self._sessions.injected_lines(task.id, role),
            )
        finally:
            self._sessions.unsubscribe(task.id, role, queue)

    async def _plan_turn(
        self, task: Task, step: Step, history: list[tuple[str, Report]], *, fresh: bool
    ) -> _TurnPlan:
        template, kind = _template_for_step(step, task.iteration)
        context: dict[str, object] = {
            "request": task.request,
            "trackers": list(self._settings.project.trackers),
            "reports": _history_context(history),
        }
        if template is PromptName.CONSULT:
            context["question"] = _latest_question(history)
        needs_facts = template is PromptName.VERIFY or fresh
        facts_text = await self._facts_text(task) if needs_facts else ""
        if fresh:
            template = PromptName.HANDOFF
        if needs_facts:
            context["facts"] = facts_text
        if template is PromptName.VERIFY:
            context["final_round"] = task.iteration >= self._settings.orchestration.max_iterations
        return _TurnPlan(template=template, kind=kind, context=context, facts=facts_text)

    async def _facts_text(self, task: Task) -> str:
        facts = await self._git_facts.collect(
            task_root(self._project_root, task.id), task.repos, self._settings.project.trackers
        )
        return render_facts(facts)

    async def _apply(self, task: Task, transition: Transition, outcome: Outcome | None) -> Task:
        self._tasks.apply_transition(task.id, transition)
        if transition.enqueue_new_tasks:
            if outcome is None:
                message = f"{task.id}'s transition enqueues new tasks but has no outcome"
                raise ShikiruError(message)
            self._apply_new_tasks(task, transition, outcome)
        if outcome is not None:
            self._emit_turn_completed(task, outcome)
        updated = self._require_task(task.id)
        await self._titles.refresh(updated)
        return updated

    def _apply_new_tasks(self, task: Task, transition: Transition, outcome: Outcome) -> None:
        new_tasks = _new_tasks_for(outcome)
        origin = TaskOrigin.SPLIT if isinstance(outcome, TriageReport) else TaskOrigin.LEADER
        new_ids = self._insert_new_tasks(task, origin, new_tasks)
        if transition.repoint_dependents:
            self._tasks.repoint_dependents(task.id, new_ids)
        self._emit_scope_change(task, outcome, new_ids)

    def _insert_new_tasks(
        self, parent: Task, origin: TaskOrigin, new_tasks: Sequence[NewTask]
    ) -> list[str]:
        inserted = [
            self._tasks.insert_child(new_task, depends_on=[], parent_id=parent.id, origin=origin)
            for new_task in new_tasks
        ]
        ids = [child.id for child in inserted]
        for new_task, child in zip(new_tasks, inserted, strict=True):
            if new_task.depends_on:
                resolved = [_resolve_ref(ref, ids) for ref in new_task.depends_on]
                self._tasks.set_depends_on(child.id, resolved)
        return ids

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

    def _emit_turn_completed(self, task: Task, outcome: Outcome) -> None:
        self._events.append(
            EventKind.TURN_COMPLETED,
            task_id=task.id,
            message=_turn_summary(outcome),
            data={
                "step": task.step.value if task.step is not None else None,
                "iteration": task.iteration,
                "summary": _turn_summary(outcome),
                "question": _turn_question(outcome),
            },
        )

    def _require_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            message = f"{task_id} vanished while its flow was running"
            raise ShikiruError(message)
        return task
