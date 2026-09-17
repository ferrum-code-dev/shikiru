"""Step transitions for the task workflow (docs/design/03-workflow.md §2-3, §6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shikiru.domain.ladder import reset_for_rung_change
from shikiru.domain.tasks import Counters, EscalationReason, Mode, Role, Status, Step, Task

CONSULT_LIMIT = 3
CONFLICT_ROUNDS = 2


@dataclass(frozen=True)
class TriageDecision:
    """The Leader's triage choice; `subtask_count` matters only when `mode` is `split`."""

    mode: Mode
    subtask_count: int = 0


@dataclass(frozen=True)
class PlanDecision:
    """The Leader's plan turn; `new_task_count` is how many tasks the plan enqueues."""

    new_task_count: int = 0


@dataclass(frozen=True)
class ImplementDecision:
    """The Implementer's report status for one implement turn."""

    status: Literal["completed", "needs_decision", "blocked"]


@dataclass(frozen=True)
class ConsultDecision:
    """The Leader's answer to a consult turn."""

    decision: Literal["answer", "escalate"]


@dataclass(frozen=True)
class VerifyDecision:
    """The Leader's verdict; `new_task_count` matters only when `decision` is `supersede`."""

    decision: Literal["accept", "changes_requested", "escalate", "supersede"]
    new_task_count: int = 0


@dataclass(frozen=True)
class ConflictVerifyDecision:
    """The Leader's verdict on a post-rebase `conflicts` turn during integration (03 §6)."""

    decision: Literal["accept", "changes_requested", "escalate"]


Outcome = (
    TriageDecision
    | PlanDecision
    | ImplementDecision
    | ConsultDecision
    | VerifyDecision
    | ConflictVerifyDecision
)


@dataclass(frozen=True)
class Transition:
    """The task's new step/status/iteration/counters/rung_index, plus side effects as data."""

    step: Step | None
    status: Status
    iteration: int
    counters: Counters
    rung_index: dict[Role, int]
    status_reason: str | None = None
    escalation_reason: EscalationReason | None = None
    new_task_count: int = 0
    repoint_dependents: bool = False


def _same(task: Task, *, step: Step, status: Status = Status.RUNNING) -> Transition:
    return Transition(
        step=step,
        status=status,
        iteration=task.iteration,
        counters=task.counters,
        rung_index=dict(task.rung_index),
    )


def _escalate(task: Task, reason: EscalationReason, *, step: Step | None = None) -> Transition:
    return Transition(
        step=step if step is not None else task.step,
        status=Status.ESCALATED,
        iteration=task.iteration,
        counters=task.counters,
        rung_index=dict(task.rung_index),
        escalation_reason=reason,
    )


def _changes_requested(task: Task, *, max_iterations: int, escalate_after: int) -> Transition:
    if task.iteration >= max_iterations:
        return _escalate(task, EscalationReason.ITERATIONS_EXHAUSTED, step=Step.VERIFY)
    rejections = task.counters.rejections_on_rung + 1
    counters = task.counters.model_copy(update={"rejections_on_rung": rejections, "consults": 0})
    rung_index = dict(task.rung_index)
    if rejections >= escalate_after:
        rung_index[Role.IMPLEMENTER] = rung_index.get(Role.IMPLEMENTER, 0) + 1
        counters = reset_for_rung_change(counters)
    return Transition(
        step=Step.IMPLEMENT,
        status=Status.RUNNING,
        iteration=task.iteration + 1,
        counters=counters,
        rung_index=rung_index,
    )


def _conflict_round(task: Task) -> Transition:
    rounds = task.counters.conflict_rounds + 1
    counters = task.counters.model_copy(update={"conflict_rounds": rounds})
    if rounds >= CONFLICT_ROUNDS:
        return Transition(
            step=Step.INTEGRATE,
            status=Status.ESCALATED,
            iteration=task.iteration,
            counters=counters,
            rung_index=dict(task.rung_index),
            escalation_reason=EscalationReason.INTEGRATION_CONFLICT,
        )
    return Transition(
        step=Step.INTEGRATE,
        status=Status.INTEGRATING,
        iteration=task.iteration,
        counters=counters,
        rung_index=dict(task.rung_index),
    )


def _from_triage(task: Task, outcome: TriageDecision) -> Transition:
    if outcome.mode is Mode.IMPLEMENT:
        return _same(task, step=Step.IMPLEMENT)
    if outcome.mode is Mode.PLAN:
        return _same(task, step=Step.PLAN)
    return Transition(
        step=None,
        status=Status.DONE,
        iteration=task.iteration,
        counters=task.counters,
        rung_index=dict(task.rung_index),
        status_reason="split",
        new_task_count=outcome.subtask_count,
        repoint_dependents=True,
    )


def _from_plan(task: Task, outcome: PlanDecision) -> Transition:
    return Transition(
        step=Step.INTEGRATE,
        status=Status.INTEGRATING,
        iteration=task.iteration,
        counters=task.counters,
        rung_index=dict(task.rung_index),
        new_task_count=outcome.new_task_count,
    )


def _from_implement(task: Task, outcome: ImplementDecision) -> Transition:
    if outcome.status == "completed":
        return _same(task, step=Step.VERIFY)
    counters = task.counters.model_copy(update={"consults": task.counters.consults + 1})
    return Transition(
        step=Step.CONSULT,
        status=Status.RUNNING,
        iteration=task.iteration,
        counters=counters,
        rung_index=dict(task.rung_index),
    )


def _from_consult(task: Task, outcome: ConsultDecision) -> Transition:
    if task.counters.consults > CONSULT_LIMIT or outcome.decision == "escalate":
        return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.CONSULT)
    return _same(task, step=Step.IMPLEMENT)


def _from_verify(
    task: Task, outcome: VerifyDecision, *, max_iterations: int, escalate_after: int
) -> Transition:
    match outcome.decision:
        case "accept":
            return Transition(
                step=Step.INTEGRATE,
                status=Status.INTEGRATING,
                iteration=task.iteration,
                counters=task.counters,
                rung_index=dict(task.rung_index),
            )
        case "changes_requested":
            return _changes_requested(
                task, max_iterations=max_iterations, escalate_after=escalate_after
            )
        case "escalate":
            return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.VERIFY)
        case "supersede":
            return Transition(
                step=None,
                status=Status.CANCELLED,
                iteration=task.iteration,
                counters=task.counters,
                rung_index=dict(task.rung_index),
                status_reason="superseded",
                new_task_count=outcome.new_task_count,
                repoint_dependents=True,
            )


def _from_integrate(task: Task, outcome: ConflictVerifyDecision) -> Transition:
    match outcome.decision:
        case "accept":
            return _same(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
        case "changes_requested":
            return _conflict_round(task)
        case "escalate":
            return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.INTEGRATE)


def next_step(
    task: Task, outcome: Outcome, *, max_iterations: int, escalate_after: int
) -> Transition:
    """Apply `outcome` to `task`'s current step and return the resulting transition.

    `max_iterations` and `escalate_after` come from `orchestration.max_iterations` and
    `roles.implementer.escalate_after`; passing them in keeps this function pure.
    """
    match task.step, outcome:
        case Step.TRIAGE, TriageDecision():
            return _from_triage(task, outcome)
        case Step.PLAN, PlanDecision():
            return _from_plan(task, outcome)
        case Step.IMPLEMENT, ImplementDecision():
            return _from_implement(task, outcome)
        case Step.CONSULT, ConsultDecision():
            return _from_consult(task, outcome)
        case Step.VERIFY, VerifyDecision():
            return _from_verify(
                task, outcome, max_iterations=max_iterations, escalate_after=escalate_after
            )
        case Step.INTEGRATE, ConflictVerifyDecision():
            return _from_integrate(task, outcome)
        case _:
            message = f"no transition for step {task.step!r} with outcome {outcome!r}"
            raise ValueError(message)
