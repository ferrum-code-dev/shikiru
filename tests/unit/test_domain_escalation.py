from __future__ import annotations

import pytest

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import options_for
from shikiru.domain.tasks import EscalationReason


@pytest.mark.parametrize("reason", list(EscalationReason))
def test_every_reason_has_exactly_one_recommended_option(reason: EscalationReason) -> None:
    options = options_for(reason)

    recommended = [option for option in options if option.recommended]
    assert len(recommended) == 1


@pytest.mark.parametrize(
    ("reason", "labels"),
    [
        (EscalationReason.DECISION_REQUIRED, {ResolutionKind.ANSWER, ResolutionKind.CANCEL}),
        (
            EscalationReason.ITERATIONS_EXHAUSTED,
            {ResolutionKind.ANSWER, ResolutionKind.ACCEPT, ResolutionKind.CANCEL},
        ),
        (EscalationReason.INTEGRATION_CONFLICT, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.INTEGRATION_BLOCKED, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.PR_CLOSED, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.AGENT_AUTH, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.LADDER_EXHAUSTED, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.QUOTA_EXHAUSTED, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
        (EscalationReason.DEPENDENCY_CANCELLED, {ResolutionKind.RETRY, ResolutionKind.CANCEL}),
    ],
)
def test_options_match_the_escalation_table(
    reason: EscalationReason, labels: set[ResolutionKind]
) -> None:
    options = options_for(reason)

    assert {option.label for option in options} == labels


def test_decision_required_recommends_answer() -> None:
    options = options_for(EscalationReason.DECISION_REQUIRED)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label is ResolutionKind.ANSWER


def test_iterations_exhausted_recommends_answer() -> None:
    options = options_for(EscalationReason.ITERATIONS_EXHAUSTED)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label is ResolutionKind.ANSWER


def test_agent_failures_recommend_retry() -> None:
    options = options_for(EscalationReason.AGENT_CRASH)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label is ResolutionKind.RETRY
