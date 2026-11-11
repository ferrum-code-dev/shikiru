"""Step transitions for the task workflow."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.ladder import (
    REJECTIONS_FIELD,
    move_up,
    pick_rung,
    reset_for_integration_attempt,
    reset_for_new_iteration,
)
from shikiru.domain.reports import (
    DECISION_ESCALATE,
    STATUS_COMPLETED,
    ConsultAnswer,
    FeedbackAnalysis,
    ImplementationReport,
    PlanReport,
    TriageReport,
    Verdict,
)
from shikiru.domain.tasks import (
    DEFAULT_COMPLEXITY,
    Counters,
    EscalationReason,
    Mode,
    Role,
    Status,
    StatusReason,
    Step,
    Task,
)

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.config.settings import ComplexityName, Settings
    from shikiru.domain.events import Event

CONSULT_LIMIT = 3
CONFLICT_ROUNDS = 2

Outcome = (
    TriageReport | PlanReport | ImplementationReport | ConsultAnswer | Verdict | FeedbackAnalysis
)

ACTING_ROLE: dict[Step, Role] = {
    Step.TRIAGE: Role.LEADER,
    Step.PLAN: Role.LEADER,
    Step.IMPLEMENT: Role.IMPLEMENTER,
    Step.CONSULT: Role.LEADER,
    Step.VERIFY: Role.LEADER,
    Step.INTEGRATE: Role.IMPLEMENTER,
}
"""Which role acts at each step: the Implementer only acts in `integrate` on conflicts."""


@dataclass(frozen=True)
class Transition:
    """The task's new step/status/iteration/counters/rungs, plus side effects as data.

    `base` is the task snapshot the decision was computed from; `rebased_onto` reconciles the
    decision with the row as it is when the decision is written.
    """

    base: Task
    step: Step | None
    status: Status
    iteration: int
    counters: Counters
    rung_index: dict[Role, int]
    set_overrides: dict[Role, Rung] = field(default_factory=dict)
    cleared_overrides: dict[Role, Rung] = field(default_factory=dict)
    complexity: ComplexityName | None = None
    mode: Mode | None = None
    title: str | None = None
    status_reason: StatusReason | None = None
    escalation_reason: EscalationReason | None = None
    repoint_dependents: bool = False
    enqueue_new_tasks: bool = False

    def rebased_onto(self, current: Task) -> Transition:
        """Keep what changed since `base` and apply only what this decision itself changed.

        A role whose override differs between `base` and `current` was repinned by the operator
        while the turn ran: its rung index and, for the Implementer, `rejections_on_rung` stay as
        stored, so neither the turn's rejection nor an automatic move undoes the pin. Every other
        iteration, counter and rung index takes the decision's value only where the decision
        changed it relative to `base`.
        """
        repinned = {
            role for role in Role if current.overrides.get(role) != self.base.overrides.get(role)
        }
        changed = {
            name: value
            for name, value in self.counters
            if value != getattr(self.base.counters, name)
            and not (name == REJECTIONS_FIELD and Role.IMPLEMENTER in repinned)
        }
        rung_index = dict(current.rung_index)
        rung_index.update(
            {
                role: index
                for role, index in self.rung_index.items()
                if role not in repinned and index != self.base.rung_index.get(role)
            }
        )
        return dataclasses.replace(
            self,
            iteration=self.iteration
            if self.iteration != self.base.iteration
            else current.iteration,
            counters=current.counters.model_copy(update=changed),
            rung_index=rung_index,
        )

    @staticmethod
    def stay(task: Task, *, step: Step | None, status: Status = Status.RUNNING) -> Transition:
        """Build a transition that keeps every field of `task` except `step` and `status`."""
        return Transition(
            base=task,
            step=step,
            status=status,
            iteration=task.iteration,
            counters=task.counters,
            rung_index=dict(task.rung_index),
        )

    @staticmethod
    def escalate(task: Task, reason: EscalationReason, *, step: Step) -> Transition:
        """Build an escalation transition while preserving the task's other state."""
        base = Transition.stay(task, step=step, status=Status.ESCALATED)
        return dataclasses.replace(base, escalation_reason=reason)

    @staticmethod
    def escalate_current(task: Task, reason: EscalationReason) -> Transition:
        """Escalate at the current step, preserving a missing step for an unstarted task."""
        if task.step is None:
            base = Transition.stay(task, step=None, status=Status.ESCALATED)
            return dataclasses.replace(base, escalation_reason=reason)
        return Transition.escalate(task, reason, step=task.step)


def first_step(task: Task) -> Transition:
    """Compute the transition for a task's very first turn (submit flags).

    A task with no `mode` starts at `triage`. `submit --mode implement|plan` skips triage
    straight into that step; `implement` also starts numbering from iteration 1, matching the
    turn dirs (`NN-<step>-i<iteration>`) and the `implement` iteration-1 convention.
    """
    if task.mode is None:
        return Transition.stay(task, step=Step.TRIAGE)
    if task.mode is Mode.PLAN:
        return Transition.stay(task, step=Step.PLAN)
    return dataclasses.replace(Transition.stay(task, step=Step.IMPLEMENT), iteration=1)


def task_acting_role(task: Task) -> Role:
    """Return the role for the current step or the task's first scheduled step."""
    step = task.step if task.step is not None else first_step(task).step
    return Role.LEADER if step is None else ACTING_ROLE[step]


TURN_LIFECYCLE_KINDS = (
    EventKind.TURN_STARTED,
    EventKind.TURN_COMPLETED,
    EventKind.TURN_FAILED,
    EventKind.TURN_STOPPED,
)


def acting_role(task: Task, latest_turn: Event | None) -> Role:
    """Return the role of the turn running on `task`, else the step's default role.

    `latest_turn` is the task's newest event of `TURN_LIFECYCLE_KINDS`: a turn runs only while
    that event starts a turn of the task's current step.
    """
    if (
        latest_turn is not None
        and latest_turn.kind is EventKind.TURN_STARTED
        and task.step is not None
        and latest_turn.data.get("step") == task.step.value
    ):
        return Role(str(latest_turn.data["role"]))
    return task_acting_role(task)


def _triaged_complexity(task: Task, outcome: TriageReport) -> ComplexityName | None:
    return outcome.complexity if task.complexity is None else None


def _from_triage(task: Task, outcome: TriageReport, settings: Settings) -> Transition:
    if outcome.mode is Mode.IMPLEMENT:
        base = dataclasses.replace(Transition.stay(task, step=Step.IMPLEMENT), iteration=1)
    else:
        base = Transition.stay(task, step=Step.PLAN)
    complexity = _triaged_complexity(task, outcome)
    implementer = settings.roles.implementer
    if complexity is not None and implementer.ladder_for(complexity) != implementer.ladder_for(
        task.complexity or DEFAULT_COMPLEXITY
    ):
        index = 0
        if task.rung_index.get(Role.IMPLEMENTER, 0) > 0:
            agent = pick_rung(task, Role.IMPLEMENTER, settings).agent
            index = next(
                (
                    index
                    for index, rung in enumerate(implementer.ladder_for(complexity))
                    if rung.agent == agent
                ),
                0,
            )
        base = dataclasses.replace(base, rung_index={**base.rung_index, Role.IMPLEMENTER: index})
    return dataclasses.replace(
        base,
        complexity=complexity,
        mode=outcome.mode,
        title=outcome.title,
    )


def _from_plan(task: Task) -> Transition:
    base = Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    return dataclasses.replace(
        base, counters=reset_for_integration_attempt(task.counters), enqueue_new_tasks=True
    )


def _from_implement(task: Task, outcome: ImplementationReport) -> Transition:
    if outcome.status == STATUS_COMPLETED:
        return Transition.stay(task, step=Step.VERIFY)
    return _consult_or_escalate(task, Status.RUNNING)


def _consult_or_escalate(task: Task, status: Status) -> Transition:
    if task.counters.consults >= CONSULT_LIMIT:
        return Transition.escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.CONSULT)
    counters = task.counters.model_copy(update={"consults": task.counters.consults + 1})
    base = Transition.stay(task, step=Step.CONSULT, status=status)
    return dataclasses.replace(base, counters=counters)


def _from_consult(task: Task, outcome: ConsultAnswer) -> Transition:
    if outcome.decision == DECISION_ESCALATE:
        return Transition.escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.CONSULT)
    if task.status is Status.INTEGRATING:
        return Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    return Transition.stay(task, step=Step.IMPLEMENT)


def _changes_requested(task: Task, settings: Settings) -> Transition:
    if task.iteration >= settings.orchestration.max_iterations:
        return Transition.escalate(task, EscalationReason.ITERATIONS_EXHAUSTED, step=Step.VERIFY)
    rejections = task.counters.rejections_on_rung + 1
    rung_index = dict(task.rung_index)
    cleared_overrides: dict[Role, Rung] = {}
    counters = task.counters.model_copy(update={"rejections_on_rung": rejections})
    if rejections >= settings.roles.implementer.escalate_after:
        move = move_up(task, Role.IMPLEMENTER, settings)
        if move is not None:
            rung_index = move.rung_index
            cleared_overrides = move.cleared_overrides
            counters = move.counters
    counters = reset_for_new_iteration(counters)
    return Transition(
        base=task,
        step=Step.IMPLEMENT,
        status=Status.RUNNING,
        iteration=task.iteration + 1,
        counters=counters,
        rung_index=rung_index,
        cleared_overrides=cleared_overrides,
    )


def _conflict_round(task: Task) -> Transition:
    rounds = task.counters.conflict_rounds + 1
    counters = task.counters.model_copy(update={"conflict_rounds": rounds})
    base = dataclasses.replace(Transition.stay(task, step=Step.INTEGRATE), counters=counters)
    if rounds >= CONFLICT_ROUNDS:
        return dataclasses.replace(
            Transition.escalate(task, EscalationReason.INTEGRATION_CONFLICT, step=Step.INTEGRATE),
            counters=counters,
        )
    return dataclasses.replace(base, status=Status.INTEGRATING)


def _from_verify(task: Task, outcome: Verdict, settings: Settings) -> Transition:
    match outcome.decision:
        case "accept":
            base = Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
            return dataclasses.replace(base, counters=reset_for_integration_attempt(task.counters))
        case "changes_requested":
            return _changes_requested(task, settings)
        case "escalate":
            return Transition.escalate(task, EscalationReason.DECISION_REQUIRED, step=Step.VERIFY)
        case "supersede":
            base = Transition.stay(task, step=None, status=Status.CANCELLED)
            return dataclasses.replace(
                base,
                status_reason=StatusReason.SUPERSEDED,
                repoint_dependents=True,
                enqueue_new_tasks=True,
            )


def _from_integrate(task: Task, outcome: Verdict) -> Transition:
    match outcome.decision:
        case "accept":
            return Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
        case "changes_requested":
            return _conflict_round(task)
        case "escalate":
            return Transition.escalate(
                task, EscalationReason.DECISION_REQUIRED, step=Step.INTEGRATE
            )
        case "supersede":
            message = "a post-rebase conflicts verify cannot supersede the task"
            raise ValueError(message)


def _from_integrate_conflicts(task: Task, outcome: ImplementationReport) -> Transition:
    if outcome.status == STATUS_COMPLETED:
        return Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    return _consult_or_escalate(task, Status.INTEGRATING)


def next_step(task: Task, outcome: Outcome, settings: Settings) -> Transition:
    """Apply `outcome` to `task`'s current step and return the resulting transition."""
    match task.step, outcome:
        case Step.TRIAGE, TriageReport():
            return _from_triage(task, outcome, settings)
        case Step.PLAN, PlanReport():
            return _from_plan(task)
        case Step.IMPLEMENT, ImplementationReport():
            return _from_implement(task, outcome)
        case Step.CONSULT, ConsultAnswer():
            return _from_consult(task, outcome)
        case Step.VERIFY, Verdict():
            return _from_verify(task, outcome, settings)
        case Step.INTEGRATE, Verdict():
            return _from_integrate(task, outcome)
        case Step.INTEGRATE, ImplementationReport():
            return _from_integrate_conflicts(task, outcome)
        case _:
            message = f"no transition for step {task.step!r} with outcome {outcome!r}"
            raise ValueError(message)
