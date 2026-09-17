from __future__ import annotations

import pytest

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
        (EscalationReason.DECISION_REQUIRED, {"answer", "cancel"}),
        (EscalationReason.ITERATIONS_EXHAUSTED, {"answer", "accept", "cancel"}),
        (EscalationReason.INTEGRATION_CONFLICT, {"retry", "cancel"}),
        (EscalationReason.INTEGRATION_BLOCKED, {"retry", "cancel"}),
        (EscalationReason.PR_CLOSED, {"retry", "cancel"}),
        (EscalationReason.AGENT_AUTH, {"retry", "cancel"}),
        (EscalationReason.LADDER_EXHAUSTED, {"retry", "cancel"}),
        (EscalationReason.QUOTA_EXHAUSTED, {"retry", "cancel"}),
        (EscalationReason.DEPENDENCY_CANCELLED, {"retry", "cancel"}),
    ],
)
def test_options_match_the_escalation_table(reason: EscalationReason, labels: set[str]) -> None:
    options = options_for(reason)

    assert {option.label for option in options} == labels


def test_decision_required_recommends_answer() -> None:
    options = options_for(EscalationReason.DECISION_REQUIRED)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label == "answer"


def test_iterations_exhausted_recommends_answer() -> None:
    options = options_for(EscalationReason.ITERATIONS_EXHAUSTED)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label == "answer"


def test_agent_failures_recommend_retry() -> None:
    options = options_for(EscalationReason.AGENT_CRASH)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label == "retry"
