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
    ("reason", "recommended_label"),
    [
        (EscalationReason.DECISION_REQUIRED, ResolutionKind.ANSWER),
        (EscalationReason.ITERATIONS_EXHAUSTED, ResolutionKind.ANSWER),
        (EscalationReason.AGENT_CRASH, ResolutionKind.RETRY),
        (EscalationReason.AGENT_AUTH, ResolutionKind.RETRY),
        (EscalationReason.LADDER_EXHAUSTED, ResolutionKind.RETRY),
    ],
    ids=["decision", "exhausted", "agent-crash", "agent-auth", "ladder-exhausted"],
)
def test_recommended_option_matches_the_reason_family(
    reason: EscalationReason, recommended_label: ResolutionKind
) -> None:
    options = options_for(reason)

    recommended = next(option for option in options if option.recommended)
    assert recommended.label is recommended_label
