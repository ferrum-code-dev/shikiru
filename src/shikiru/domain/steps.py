"""Step transitions for the task workflow (docs/design/03-workflow.md §2-3, §6)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from shikiru.domain.ladder import move_up, reset_for_integration_attempt, reset_for_new_iteration
from shikiru.domain.tasks import Counters, EscalationReason, Mode, Role, Status, Step, Task

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.config.settings import Settings

CONSULT_LIMIT = 3
CONFLICT_ROUNDS = 2


@dataclass(frozen=True)
class TriageDecision:
    """The Leader's triage choice."""

    mode: Mode


@dataclass(frozen=True)
class PlanDecision:
    """The Leader's plan turn; it always moves the task into integration."""


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
    """The Leader's verdict on an implement turn."""

    decision: Literal["accept", "changes_requested", "escalate", "supersede"]


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
    """The task's new step/status/iteration/counters/rungs, plus side effects as data."""

    step: Step | None
    status: Status
    iteration: int
    counters: Counters
    rung_index: dict[Role, int]
    overrides: dict[Role, Rung]
    status_reason: str | None = None
    escalation_reason: EscalationReason | None = None
    repoint_dependents: bool = False
    enqueue_new_tasks: bool = False

    @staticmethod
    def stay(task: Task, *, step: Step | None, status: Status = Status.RUNNING) -> Transition:
        """Build a transition that keeps every field of `task` except `step` and `status`."""
        return Transition(
            step=step,
            status=status,
            iteration=task.iteration,
            counters=task.counters,
            rung_index=dict(task.rung_index),
            overrides=dict(task.overrides),
        )


def _escalate(task: Task, reason: EscalationReason, *, step: Step | None = None) -> Transition:
    base = Transition.stay(task, step=step if step is not None else task.step)
    return dataclasses.replace(base, status=Status.ESCALATED, escalation_reason=reason)


def first_step(task: Task) -> Transition:
    """Compute the transition for a task's very first turn (03 §1.1, §2; submit flags, 05 §1).

    A task with no `mode` starts at `triage`. `submit --mode implement|plan` skips triage
    straight into that step; `implement` also starts numbering from iteration 1, matching the
    turn dirs (`NN-<step>-i<iteration>`) and 03 §2's "implement (i = 1)". `--mode split` has no
    first turn of its own, since `split` only ever happens as a triage outcome (03 §2), so it is
    rejected here.
    """
    if task.mode is None:
        return Transition.stay(task, step=Step.TRIAGE)
    if task.mode is Mode.PLAN:
        return Transition.stay(task, step=Step.PLAN)
    if task.mode is Mode.IMPLEMENT:
        return dataclasses.replace(Transition.stay(task, step=Step.IMPLEMENT), iteration=1)
    message = "submit --mode split has no first step; split only happens as a triage outcome"
    raise ValueError(message)


def _from_triage(task: Task, outcome: TriageDecision) -> Transition:
    if outcome.mode is Mode.IMPLEMENT:
        return dataclasses.replace(Transition.stay(task, step=Step.IMPLEMENT), iteration=1)
    if outcome.mode is Mode.PLAN:
        return Transition.stay(task, step=Step.PLAN)
    base = Transition.stay(task, step=None, status=Status.DONE)
    return dataclasses.replace(
        base, status_reason="split", repoint_dependents=True, enqueue_new_tasks=True
    )


def _from_plan(task: Task) -> Transition:
    base = Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    return dataclasses.replace(
        base, counters=reset_for_integration_attempt(task.counters), enqueue_new_tasks=True
    )


def _from_implement(task: Task, outcome: ImplementDecision) -> Transition:
    if outcome.status == "completed":
        return Transition.stay(task, step=Step.VERIFY)
    if task.counters.consults >= CONSULT_LIMIT:
        return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.CONSULT)
    counters = task.counters.model_copy(update={"consults": task.counters.consults + 1})
    return dataclasses.replace(Transition.stay(task, step=Step.CONSULT), counters=counters)


def _from_consult(task: Task, outcome: ConsultDecision) -> Transition:
    if outcome.decision == "escalate":
        return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.CONSULT)
    return Transition.stay(task, step=Step.IMPLEMENT)


def _changes_requested(task: Task, settings: Settings) -> Transition:
    if task.iteration >= settings.orchestration.max_iterations:
        return _escalate(task, EscalationReason.ITERATIONS_EXHAUSTED, step=Step.VERIFY)
    rejections = task.counters.rejections_on_rung + 1
    counters = reset_for_new_iteration(task.counters).model_copy(
        update={"rejections_on_rung": rejections}
    )
    rung_index = dict(task.rung_index)
    overrides = dict(task.overrides)
    if rejections >= settings.roles.implementer.escalate_after:
        move = move_up(task, Role.IMPLEMENTER, settings)
        if move is not None:
            rung_index = move.rung_index
            overrides = move.overrides
            counters = counters.model_copy(update={"rejections_on_rung": 0})
    return Transition(
        step=Step.IMPLEMENT,
        status=Status.RUNNING,
        iteration=task.iteration + 1,
        counters=counters,
        rung_index=rung_index,
        overrides=overrides,
    )


def _conflict_round(task: Task) -> Transition:
    rounds = task.counters.conflict_rounds + 1
    counters = task.counters.model_copy(update={"conflict_rounds": rounds})
    base = dataclasses.replace(Transition.stay(task, step=Step.INTEGRATE), counters=counters)
    if rounds >= CONFLICT_ROUNDS:
        return dataclasses.replace(
            base, status=Status.ESCALATED, escalation_reason=EscalationReason.INTEGRATION_CONFLICT
        )
    return dataclasses.replace(base, status=Status.INTEGRATING)


def _from_verify(task: Task, outcome: VerifyDecision, settings: Settings) -> Transition:
    match outcome.decision:
        case "accept":
            base = Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
            return dataclasses.replace(base, counters=reset_for_integration_attempt(task.counters))
        case "changes_requested":
            return _changes_requested(task, settings)
        case "escalate":
            return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.VERIFY)
        case "supersede":
            base = Transition.stay(task, step=None, status=Status.CANCELLED)
            return dataclasses.replace(
                base, status_reason="superseded", repoint_dependents=True, enqueue_new_tasks=True
            )


def _from_integrate(task: Task, outcome: ConflictVerifyDecision) -> Transition:
    match outcome.decision:
        case "accept":
            return Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
        case "changes_requested":
            return _conflict_round(task)
        case "escalate":
            return _escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.INTEGRATE)


def next_step(task: Task, outcome: Outcome, settings: Settings) -> Transition:
    """Apply `outcome` to `task`'s current step and return the resulting transition."""
    match task.step, outcome:
        case Step.TRIAGE, TriageDecision():
            return _from_triage(task, outcome)
        case Step.PLAN, PlanDecision():
            return _from_plan(task)
        case Step.IMPLEMENT, ImplementDecision():
            return _from_implement(task, outcome)
        case Step.CONSULT, ConsultDecision():
            return _from_consult(task, outcome)
        case Step.VERIFY, VerifyDecision():
            return _from_verify(task, outcome, settings)
        case Step.INTEGRATE, ConflictVerifyDecision():
            return _from_integrate(task, outcome)
        case _:
            message = f"no transition for step {task.step!r} with outcome {outcome!r}"
            raise ValueError(message)
