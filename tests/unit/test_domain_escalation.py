from __future__ import annotations

from dataclasses import dataclass

import pytest

from shikiru.agents.contracts import Rung
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import options_for, transition_for_resolution
from shikiru.domain.tasks import Counters, EscalationReason, Role, Status, Step
from tests.support.factories import make_task


@dataclass(frozen=True)
class _ResolutionCase:
    reason: EscalationReason
    step: Step
    resolution: ResolutionKind
    rung: Rung | None
    expected_step: Step
    expected_counters: Counters


def test_every_reason_has_exactly_one_recommended_option() -> None:
    recommended = {
        reason: sum(option.recommended for option in options_for(reason))
        for reason in EscalationReason
    }

    assert set(recommended.values()) == {1}


@pytest.mark.parametrize(
    ("reason", "recommended_label"),
    [
        (EscalationReason.DECISION_REQUIRED, ResolutionKind.ANSWER),
        (EscalationReason.ITERATIONS_EXHAUSTED, ResolutionKind.ANSWER),
        (EscalationReason.AGENT_ERROR, ResolutionKind.RETRY),
    ],
    ids=["decision", "exhausted", "retry-family"],
)
def test_recommended_option_matches_the_reason_family(
    reason: EscalationReason, recommended_label: ResolutionKind
) -> None:
    options = options_for(reason)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label is recommended_label


def test_only_iterations_exhausted_offers_accept() -> None:
    labels_by_reason = {
        reason: {option.label for option in options_for(reason)} for reason in EscalationReason
    }

    offering_accept = [
        reason for reason, labels in labels_by_reason.items() if ResolutionKind.ACCEPT in labels
    ]

    assert offering_accept == [EscalationReason.ITERATIONS_EXHAUSTED]
    assert labels_by_reason[EscalationReason.ITERATIONS_EXHAUSTED] == {
        ResolutionKind.ANSWER,
        ResolutionKind.ACCEPT,
        ResolutionKind.CANCEL,
    }


@pytest.mark.parametrize(
    "case",
    [
        _ResolutionCase(
            EscalationReason.DECISION_REQUIRED,
            Step.IMPLEMENT,
            ResolutionKind.ANSWER,
            None,
            Step.CONSULT,
            Counters(rejections_on_rung=7, conflict_rounds=4),
        ),
        _ResolutionCase(
            EscalationReason.ITERATIONS_EXHAUSTED,
            Step.VERIFY,
            ResolutionKind.ANSWER,
            None,
            Step.CONSULT,
            Counters(rejections_on_rung=7, conflict_rounds=4),
        ),
        _ResolutionCase(
            EscalationReason.INTEGRATION_CONFLICT,
            Step.INTEGRATE,
            ResolutionKind.RETRY,
            None,
            Step.INTEGRATE,
            Counters(rejections_on_rung=7, consults=3),
        ),
        _ResolutionCase(
            EscalationReason.AGENT_ERROR,
            Step.IMPLEMENT,
            ResolutionKind.RETRY,
            Rung(agent="codex", model="sol", effort=None),
            Step.IMPLEMENT,
            Counters(consults=3, conflict_rounds=4),
        ),
        _ResolutionCase(
            EscalationReason.ITERATIONS_EXHAUSTED,
            Step.VERIFY,
            ResolutionKind.ACCEPT,
            None,
            Step.INTEGRATE,
            Counters(rejections_on_rung=7),
        ),
    ],
)
def test_resolution_transition_resets_only_its_counter(
    case: _ResolutionCase,
) -> None:
    reason = case.reason
    step = case.step
    task = make_task(
        status=Status.ESCALATED,
        step=step,
        counters=Counters(rejections_on_rung=7, consults=3, conflict_rounds=4),
    )

    transition = transition_for_resolution(
        task,
        reason,
        step=step,
        resolution=case.resolution,
        rung=case.rung,
        role=Role.IMPLEMENTER if case.rung is not None else None,
    )

    assert transition.step is case.expected_step
    assert transition.status is Status.QUEUED
    assert transition.counters == case.expected_counters
    if case.rung is not None:
        assert transition.set_overrides == {Role.IMPLEMENTER: case.rung}
