from __future__ import annotations

from typing import Literal

import pytest

from shikiru.agents.contracts import Rung
from shikiru.domain.steps import (
    CONFLICT_ROUNDS,
    CONSULT_LIMIT,
    Outcome,
    Transition,
    first_step,
    next_step,
)
from shikiru.domain.tasks import (
    Counters,
    EscalationReason,
    Mode,
    Role,
    Status,
    StatusReason,
    Step,
    Task,
)
from tests.support.factories import (
    ESCALATE_AFTER,
    MAX_ITERATIONS,
    make_consult_answer,
    make_implementation_report,
    make_plan_report,
    make_settings,
    make_task,
    make_triage_report,
    make_verdict,
)

_SETTINGS = make_settings()


def _next(task: Task, outcome: Outcome) -> Transition:
    return next_step(task, outcome, _SETTINGS)


def test_first_step_with_no_mode_starts_at_triage() -> None:
    task = make_task(mode=None)

    transition = first_step(task)

    assert transition.step is Step.TRIAGE
    assert transition.iteration == 0


def test_first_step_with_mode_plan_skips_triage() -> None:
    task = make_task(mode=Mode.PLAN)

    transition = first_step(task)

    assert transition.step is Step.PLAN
    assert transition.iteration == 0


def test_first_step_with_mode_implement_starts_at_iteration_one() -> None:
    task = make_task(mode=Mode.IMPLEMENT)

    transition = first_step(task)

    assert transition.step is Step.IMPLEMENT
    assert transition.iteration == 1


def test_triage_implement_starts_the_first_implement_at_iteration_one() -> None:
    task = make_task(step=Step.TRIAGE, iteration=0)

    transition = _next(task, make_triage_report(mode=Mode.IMPLEMENT))

    assert transition.step is Step.IMPLEMENT
    assert transition.status is Status.RUNNING
    assert transition.iteration == 1


def test_triage_plan_moves_to_plan_step() -> None:
    task = make_task(step=Step.TRIAGE)

    transition = _next(task, make_triage_report(mode=Mode.PLAN))

    assert transition.step is Step.PLAN


def test_plan_moves_to_integrate_and_enqueues_new_tasks() -> None:
    task = make_task(step=Step.PLAN)

    transition = _next(task, make_plan_report())

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING
    assert transition.enqueue_new_tasks is True


@pytest.mark.parametrize(
    ("step", "outcome"),
    [
        pytest.param(Step.PLAN, make_plan_report(), id="plan-completed"),
        pytest.param(Step.VERIFY, make_verdict(decision="accept"), id="verify-accepted"),
    ],
)
def test_entering_integrate_resets_conflict_rounds(step: Step, outcome: Outcome) -> None:
    task = make_task(step=step, counters=Counters(conflict_rounds=1))

    transition = _next(task, outcome)

    assert transition.step is Step.INTEGRATE
    assert transition.counters.conflict_rounds == 0


def test_implement_completed_moves_to_verify() -> None:
    task = make_task(step=Step.IMPLEMENT)

    transition = _next(task, make_implementation_report(status="completed"))

    assert transition.step is Step.VERIFY


@pytest.mark.parametrize("status", ["needs_decision", "blocked"])
def test_implement_needs_decision_or_blocked_moves_to_consult(
    status: Literal["needs_decision", "blocked"],
) -> None:
    task = make_task(step=Step.IMPLEMENT, counters=Counters(consults=1))

    transition = _next(task, make_implementation_report(status=status))

    assert transition.step is Step.CONSULT
    assert transition.counters.consults == 2


@pytest.mark.parametrize(
    ("step", "consults", "expected_status"),
    [
        pytest.param(Step.IMPLEMENT, CONSULT_LIMIT, Status.ESCALATED, id="implement-at-limit"),
        pytest.param(Step.IMPLEMENT, CONSULT_LIMIT - 1, Status.RUNNING, id="implement-below-limit"),
        pytest.param(Step.INTEGRATE, CONSULT_LIMIT, Status.ESCALATED, id="integrate-at-limit"),
        pytest.param(
            Step.INTEGRATE, CONSULT_LIMIT - 1, Status.INTEGRATING, id="integrate-below-limit"
        ),
    ],
)
def test_consult_limit_controls_implementation_and_conflict_decisions(
    step: Step, consults: int, expected_status: Status
) -> None:
    status = Status.INTEGRATING if step is Step.INTEGRATE else Status.RUNNING
    task = make_task(step=step, status=status, counters=Counters(consults=consults))

    transition = _next(task, make_implementation_report(status="needs_decision"))

    assert transition.status is expected_status
    assert transition.step is Step.CONSULT
    assert transition.counters.consults == min(consults + 1, CONSULT_LIMIT)
    if expected_status is Status.ESCALATED:
        assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_consult_answer_returns_to_implement_at_the_same_iteration() -> None:
    task = make_task(
        step=Step.CONSULT, status=Status.RUNNING, iteration=1, counters=Counters(consults=1)
    )

    transition = _next(task, make_consult_answer(decision="answer"))

    assert transition.step is Step.IMPLEMENT
    assert transition.status is Status.RUNNING
    assert transition.iteration == 1


def test_consult_answer_during_integration_returns_to_integrate() -> None:
    task = make_task(step=Step.CONSULT, status=Status.INTEGRATING, counters=Counters(consults=1))

    transition = _next(task, make_consult_answer(decision="answer"))

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING


@pytest.mark.parametrize(
    ("step", "status", "outcome"),
    [
        pytest.param(
            Step.CONSULT,
            Status.RUNNING,
            make_consult_answer(decision="escalate"),
            id="consult-escalation",
        ),
        pytest.param(
            Step.VERIFY,
            Status.RUNNING,
            make_verdict(decision="escalate"),
            id="verify-escalation",
        ),
        pytest.param(
            Step.INTEGRATE,
            Status.INTEGRATING,
            make_verdict(decision="escalate"),
            id="integration-escalation",
        ),
    ],
)
def test_escalation_decisions_raise_decision_required(
    step: Step, status: Status, outcome: Outcome
) -> None:
    task = make_task(step=step, status=status, counters=Counters(consults=1))

    transition = _next(task, outcome)

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.DECISION_REQUIRED


def test_verify_accept_moves_to_integrate() -> None:
    task = make_task(step=Step.VERIFY)

    transition = _next(task, make_verdict(decision="accept"))

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING


def test_verify_changes_requested_increments_iteration_and_rejections() -> None:
    task = make_task(step=Step.VERIFY, iteration=1, counters=Counters(consults=2))

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.step is Step.IMPLEMENT
    assert transition.iteration == 2
    assert transition.counters.rejections_on_rung == 1
    assert transition.counters.consults == 0
    assert transition.rung_index.get(Role.IMPLEMENTER, 0) == 0


def test_verify_changes_requested_at_escalate_after_bumps_the_rung_and_resets() -> None:
    task = make_task(step=Step.VERIFY, counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1))

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.rung_index[Role.IMPLEMENTER] == 1
    assert transition.counters.rejections_on_rung == 0


def test_verify_changes_requested_at_escalate_after_keeps_the_new_iterations_consult_reset() -> (
    None
):
    task = make_task(
        step=Step.VERIFY,
        counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1, consults=2),
    )

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.counters.consults == 0


def test_verify_changes_requested_bumping_the_rung_clears_an_operator_override() -> None:
    override = Rung(agent="claude", model="haiku", effort=None)
    task = make_task(
        step=Step.VERIFY,
        counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1),
        overrides={Role.IMPLEMENTER: override},
    )

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.overrides == {}


def test_verify_changes_requested_at_the_top_of_the_ladder_keeps_counting() -> None:
    top_index = len(_SETTINGS.roles.implementer.ladder) - 1
    task = make_task(
        step=Step.VERIFY,
        rung_index={Role.IMPLEMENTER: top_index},
        counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1),
    )

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.rung_index[Role.IMPLEMENTER] == top_index
    assert transition.counters.rejections_on_rung == ESCALATE_AFTER
    assert transition.status is Status.RUNNING


def test_verify_changes_requested_in_the_final_round_raises_iterations_exhausted() -> None:
    task = make_task(step=Step.VERIFY, iteration=MAX_ITERATIONS)

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.ITERATIONS_EXHAUSTED
    assert transition.step is Step.VERIFY


def test_verify_supersede_cancels_and_enqueues_new_tasks() -> None:
    task = make_task(step=Step.VERIFY)

    transition = _next(task, make_verdict(decision="supersede"))

    assert transition.status is Status.CANCELLED
    assert transition.status_reason is StatusReason.SUPERSEDED
    assert transition.enqueue_new_tasks is True
    assert transition.repoint_dependents is True


def test_integrate_conflict_accept_continues_integrating() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, make_verdict(decision="accept"))

    assert transition.status is Status.INTEGRATING
    assert transition.step is Step.INTEGRATE


def test_integrate_conflict_accept_does_not_reset_conflict_rounds() -> None:
    task = make_task(
        step=Step.INTEGRATE, status=Status.INTEGRATING, counters=Counters(conflict_rounds=1)
    )

    transition = _next(task, make_verdict(decision="accept"))

    assert transition.counters.conflict_rounds == 1


def test_integrate_conflict_changes_requested_counts_a_round() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.counters.conflict_rounds == 1
    assert transition.status is Status.INTEGRATING


def test_integrate_conflict_rounds_exhausted_raises_integration_conflict() -> None:
    task = make_task(
        step=Step.INTEGRATE,
        status=Status.INTEGRATING,
        counters=Counters(conflict_rounds=CONFLICT_ROUNDS - 1),
    )

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.status is Status.ESCALATED
    assert transition.escalation_reason is EscalationReason.INTEGRATION_CONFLICT


def test_integrate_conflict_rejects_a_supersede_verdict() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    with pytest.raises(ValueError, match="supersede"):
        _next(task, make_verdict(decision="supersede"))


def test_integrate_conflicts_report_completed_returns_to_the_post_rebase_verify() -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING)

    transition = _next(task, make_implementation_report(status="completed"))

    assert transition.step is Step.INTEGRATE
    assert transition.status is Status.INTEGRATING


@pytest.mark.parametrize("status", ["needs_decision", "blocked"])
def test_integrate_conflicts_report_not_completed_moves_to_consult(
    status: Literal["needs_decision", "blocked"],
) -> None:
    task = make_task(step=Step.INTEGRATE, status=Status.INTEGRATING, counters=Counters(consults=1))

    transition = _next(task, make_implementation_report(status=status))

    assert transition.step is Step.CONSULT
    assert transition.counters.consults == 2


def test_next_step_rejects_an_outcome_that_does_not_match_the_current_step() -> None:
    task = make_task(step=Step.TRIAGE)

    with pytest.raises(ValueError, match="no transition"):
        _next(task, make_verdict(decision="accept"))
