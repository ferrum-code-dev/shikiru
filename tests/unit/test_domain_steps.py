from __future__ import annotations

from typing import Literal

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityLadder
from shikiru.domain.ladder import pick_rung
from shikiru.domain.steps import (
    CONFLICT_ROUNDS,
    CONSULT_LIMIT,
    Outcome,
    Transition,
    first_step,
    next_step,
    task_acting_role,
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


@pytest.mark.parametrize(
    ("mode", "step", "iteration"),
    [(None, Step.TRIAGE, 0), (Mode.PLAN, Step.PLAN, 0), (Mode.IMPLEMENT, Step.IMPLEMENT, 1)],
)
def test_first_step_follows_the_mode_and_starts_implementation_at_iteration_one(
    mode: Mode | None, step: Step, iteration: int
) -> None:
    transition = first_step(make_task(mode=mode))

    assert (transition.step, transition.iteration) == (step, iteration)


@pytest.mark.parametrize(
    ("mode", "step", "expected"),
    [
        (Mode.IMPLEMENT, None, Role.IMPLEMENTER),
        (Mode.PLAN, None, Role.LEADER),
        (None, Step.IMPLEMENT, Role.IMPLEMENTER),
    ],
)
def test_task_acting_role_uses_current_or_first_scheduled_step(
    mode: Mode | None, step: Step | None, expected: Role
) -> None:
    task = make_task(mode=mode, step=step)

    assert task_acting_role(task) is expected


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
    assert transition.mode is Mode.PLAN


_TRIAGED_COMPLEXITY_CASES = {
    "unset_takes_the_triaged": (None, "high"),
    "explicit_normal_is_kept": ("normal", None),
}


@pytest.mark.parametrize(
    ("submitted", "expected"),
    _TRIAGED_COMPLEXITY_CASES.values(),
    ids=_TRIAGED_COMPLEXITY_CASES.keys(),
)
def test_triage_replaces_only_an_unset_complexity(
    submitted: str | None, expected: str | None
) -> None:
    task = make_task(step=Step.TRIAGE, complexity=submitted)

    transition = _next(task, make_triage_report(complexity="high"))

    assert transition.complexity == expected


@pytest.mark.parametrize(
    ("submitted_complexity", "triaged_complexity", "expected_index"),
    [(None, "high", 0), (None, "normal", 1), ("normal", "high", 1)],
    ids=["new-ladder", "same-ladder", "explicit-complexity"],
)
def test_triage_rebases_a_queued_implementer_fallback_when_the_ladder_changes(
    submitted_complexity: str | None, triaged_complexity: str, expected_index: int
) -> None:
    high_rung = Rung(agent="codex", model="high-model", effort=None)
    settings = make_settings(implementer_complexity={"high": ComplexityLadder(ladder=[high_rung])})
    task = make_task(
        step=Step.TRIAGE,
        complexity=submitted_complexity,
        rung_index={Role.IMPLEMENTER: 1, Role.LEADER: 1},
    )

    transition = next_step(task, make_triage_report(complexity=triaged_complexity), settings)

    assert transition.rung_index[Role.IMPLEMENTER] == expected_index
    assert transition.rung_index[Role.LEADER] == 1
    updated = task.model_copy(
        update={
            "complexity": transition.complexity or task.complexity,
            "rung_index": transition.rung_index,
        }
    )
    if expected_index == 0:
        assert pick_rung(updated, Role.IMPLEMENTER, settings) == high_rung
    else:
        assert (
            pick_rung(updated, Role.IMPLEMENTER, settings) == settings.roles.implementer.ladder[1]
        )


_NORMAL_LADDER = [
    Rung(agent="claude", model="normal-claude", effort=None),
    Rung(agent="codex", model="normal-codex", effort=None),
]
_HIGH_CLAUDE = Rung(agent="claude", model="high-claude", effort=None)
_HIGH_CODEX = Rung(agent="codex", model="high-codex", effort=None)


@pytest.mark.parametrize(
    ("old_index", "high_ladder", "expected_index"),
    [
        (1, [_HIGH_CLAUDE, _HIGH_CODEX], 1),
        (1, [_HIGH_CODEX, _HIGH_CLAUDE], 0),
        (1, [_HIGH_CLAUDE], 0),
        (0, [_HIGH_CODEX, _HIGH_CLAUDE], 0),
    ],
    ids=["preserves-fallback", "reordered", "agent-absent", "initial-rung"],
)
def test_triage_keeps_an_advanced_agent_in_the_new_complexity_ladder(
    old_index: int, high_ladder: list[Rung], expected_index: int
) -> None:
    settings = make_settings(
        implementer_ladder=_NORMAL_LADDER,
        implementer_complexity={"high": ComplexityLadder(ladder=high_ladder)},
    )
    task = make_task(
        step=Step.TRIAGE,
        complexity=None,
        rung_index={Role.IMPLEMENTER: old_index},
    )

    transition = next_step(task, make_triage_report(complexity="high"), settings)

    updated = task.model_copy(
        update={"complexity": transition.complexity, "rung_index": transition.rung_index}
    )
    assert updated.rung_index[Role.IMPLEMENTER] == expected_index
    assert pick_rung(updated, Role.IMPLEMENTER, settings) == high_ladder[expected_index]


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


_OVERRIDE = Rung(agent="claude", model="haiku", effort=None)
_TOP_RUNG = len(_SETTINGS.roles.implementer.ladder) - 1


@pytest.mark.parametrize(
    ("rung", "override", "rung_after", "rejections_after", "cleared"),
    [
        (0, None, 1, 0, {}),
        (0, _OVERRIDE, 1, 0, {Role.IMPLEMENTER: _OVERRIDE}),
        (_TOP_RUNG, None, _TOP_RUNG, ESCALATE_AFTER, {}),
    ],
)
def test_verify_changes_requested_at_escalate_after_bumps_the_rung_until_the_top(
    rung: int,
    override: Rung | None,
    rung_after: int,
    rejections_after: int,
    cleared: dict[Role, Rung],
) -> None:
    task = make_task(
        step=Step.VERIFY,
        rung_index={Role.IMPLEMENTER: rung},
        counters=Counters(rejections_on_rung=ESCALATE_AFTER - 1, consults=2),
        overrides={Role.IMPLEMENTER: override} if override else {},
    )

    transition = _next(task, make_verdict(decision="changes_requested"))

    assert transition.rung_index[Role.IMPLEMENTER] == rung_after
    assert transition.counters.rejections_on_rung == rejections_after
    assert transition.counters.consults == 0
    assert transition.cleared_overrides == cleared
    assert transition.set_overrides == {}
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


@pytest.mark.parametrize(
    ("decision", "rounds", "rounds_after", "status", "reason"),
    [
        ("accept", 0, 0, Status.INTEGRATING, None),
        ("accept", 1, 1, Status.INTEGRATING, None),
        ("changes_requested", 0, 1, Status.INTEGRATING, None),
        (
            "changes_requested",
            CONFLICT_ROUNDS - 1,
            CONFLICT_ROUNDS,
            Status.ESCALATED,
            EscalationReason.INTEGRATION_CONFLICT,
        ),
    ],
)
def test_integrate_conflict_verdicts_count_rounds_until_they_are_exhausted(
    decision: Literal["accept", "changes_requested"],
    rounds: int,
    rounds_after: int,
    status: Status,
    reason: EscalationReason | None,
) -> None:
    task = make_task(
        step=Step.INTEGRATE, status=Status.INTEGRATING, counters=Counters(conflict_rounds=rounds)
    )

    transition = _next(task, make_verdict(decision=decision))

    assert transition.counters.conflict_rounds == rounds_after
    assert transition.status is status
    assert transition.escalation_reason is reason
    assert transition.step is Step.INTEGRATE


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
