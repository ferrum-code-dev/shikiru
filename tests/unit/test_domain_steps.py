from __future__ import annotations

from typing import Literal

import pytest

from shikiru.domain.steps import (
    CONFLICT_ROUNDS,
    CONSULT_LIMIT,
    ConflictVerifyDecision,
    ConsultDecision,
    ImplementDecision,
    Outcome,
    PlanDecision,
    Transition,
    TriageDecision,
    VerifyDecision,
    next_step,
)
from shikiru.domain.tasks import Counters, EscalationReason, Mode, Role, Status, Step, Task
from tests.support.factories import make_task

MAX_ITERATIONS = 5
ESCALATE_AFTER = 2


def _next(task: Task, outcome: Outcome) -> Transition:
    return next_step(task, outcome, max_iterations=MAX_ITERATIONS, escalate_after=ESCALATE_AFTER)


def test_triage_implement_moves_to_implement_step() -> None:
    task = make_task(step=Step.TRIAGE)

    transition = _next(task, TriageDecision(mode=Mode.IMPLEMENT))

    assert transition.step is Step.IMPLEMENT
    assert transition.status is Status.RUNNING


def test_triage_plan_moves_to_plan_step() -> None:
    task = make_task(step=Step.TRIAGE)

    transition = _next(task, TriageDecision(mode=Mode.PLAN))

    assert transition.step is Step.PLAN


def test_triage_split_closes_the_task_and_enqueues_children() -> None:
    task = make_task(step=Step.TRIAGE)

    transition = _next(task, TriageDecision(mode=Mode.SPLIT, subtask_count=3))

    assert transition.step is None
    assert transition.status is Status.DONE
    assert transition.status_reason == "split"
    assert transition.new_task_count == 3
    assert transition.repoint_dependents is True


def test_plan_moves_to_integrate_and_enqueues_new_tasks() -> None:
    task = make_task(step=Step.PLAN)

    transition = _next(task, PlanDecision(new_task_count=2))

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING
    assert transition.new_task_count == 2


def test_implement_completed_moves_to_verify() -> None:
    task = make_task(step=Step.IMPLEMENT)

    transition = _next(task, ImplementDecision(status="completed"))

    assert transition.step is Step.VERIFY


@pytest.mark.parametrize("status", ["needs_decision", "blocked"])
def test_implement_needs_decision_or_blocked_moves_to_consult(
    status: Literal["needs_decision", "blocked"],
) -> None:
    task = make_task(step=Step.IMPLEMENT, counters=Counters(consults=1))

    transition = _next(task, ImplementDecision(status=status))

    assert transition.step is Step.CONSULT
    assert transition.counters.consults == 2


def test_consult_answer_returns_to_implement_at_the_same_iteration() -> None:
    task = make_task(step=Step.CONSULT, iteration=1, counters=Counters(consults=1))

    transition = _next(task, ConsultDecision(decision="answer"))

    assert transition.step is Step.IMPLEMENT
    assert transition.iteration == 1


def test_consult_escalate_raises_decision_required() -> None:
    task = make_task(step=Step.CONSULT, counters=Counters(consults=1))

    transition = _next(task, ConsultDecision(decision="escalate"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_consult_over_the_limit_escalates_even_on_answer() -> None:
    task = make_task(step=Step.CONSULT, counters=Counters(consults=CONSULT_LIMIT + 1))

    transition = _next(task, ConsultDecision(decision="answer"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_consult_at_the_limit_still_answers() -> None:
    task = make_task(step=Step.CONSULT, counters=Counters(consults=CONSULT_LIMIT))

    transition = _next(task, ConsultDecision(decision="answer"))

    assert transition.step is Step.IMPLEMENT


def test_verify_accept_moves_to_integrate() -> None:
    task = make_task(step=Step.VERIFY)

    transition = _next(task, VerifyDecision(decision="accept"))

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING


def test_verify_changes_requested_increments_iteration_and_rejections() -> None:
    task = make_task(step=Step.VERIFY, iteration=0, counters=Counters(consults=2))

    transition = _next(task, VerifyDecision(decision="changes_requested"))

    assert transition.step is Step.IMPLEMENT
    assert transition.iteration == 1
    assert transition.counters.rejections_on_rung == 1
    assert transition.counters.consults == 0
    assert transition.rung_index.get(Role.IMPLEMENTER, 0) == 0


def test_verify_changes_requested_at_escalate_after_bumps_the_rung_and_resets() -> None:
    task = make_task(step=Step.VERIFY, counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1))

    transition = _next(task, VerifyDecision(decision="changes_requested"))

    assert transition.rung_index[Role.IMPLEMENTER] == 1
    assert transition.counters.rejections_on_rung == 0


def test_verify_changes_requested_in_the_final_round_raises_iterations_exhausted() -> None:
    task = make_task(step=Step.VERIFY, iteration=MAX_ITERATIONS)

    transition = _next(task, VerifyDecision(decision="changes_requested"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.ITERATIONS_EXHAUSTED
    assert transition.step is Step.VERIFY


def test_verify_escalate_raises_decision_required() -> None:
    task = make_task(step=Step.VERIFY)

    transition = _next(task, VerifyDecision(decision="escalate"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_verify_supersede_cancels_and_enqueues_new_tasks() -> None:
    task = make_task(step=Step.VERIFY)

    transition = _next(task, VerifyDecision(decision="supersede", new_task_count=2))

    assert transition.status is Status.CANCELLED
    assert transition.status_reason == "superseded"
    assert transition.new_task_count == 2
    assert transition.repoint_dependents is True


def test_integrate_conflict_accept_continues_integrating() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, ConflictVerifyDecision(decision="accept"))

    assert transition.status is Status.INTEGRATING
    assert transition.step is Step.INTEGRATE


def test_integrate_conflict_changes_requested_counts_a_round() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, ConflictVerifyDecision(decision="changes_requested"))

    assert transition.counters.conflict_rounds == 1
    assert transition.status is Status.INTEGRATING


def test_integrate_conflict_rounds_exhausted_raises_integration_conflict() -> None:
    task = make_task(
        step=Step.INTEGRATE,
        status=Status.INTEGRATING,
        counters=Counters(conflict_rounds=CONFLICT_ROUNDS - 1),
    )

    transition = _next(task, ConflictVerifyDecision(decision="changes_requested"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.INTEGRATION_CONFLICT


def test_integrate_conflict_escalate_raises_decision_required() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, ConflictVerifyDecision(decision="escalate"))

    assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_next_step_rejects_an_outcome_that_does_not_match_the_current_step() -> None:
    task = make_task(step=Step.TRIAGE)

    with pytest.raises(ValueError, match="no transition"):
        _next(task, VerifyDecision(decision="accept"))
