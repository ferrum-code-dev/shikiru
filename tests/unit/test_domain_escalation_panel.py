from __future__ import annotations

from datetime import UTC, datetime

from shikiru.domain.attempts import AttemptKind
from shikiru.domain.escalation import Escalation, EscalationStatus, options_for
from shikiru.domain.escalation_evidence import EscalationPacket
from shikiru.domain.escalation_panel import ChoiceKind, build_resolution_panel
from shikiru.domain.reports import EscalationRequest, Option
from shikiru.domain.tasks import EscalationReason

_REQUEST = EscalationRequest(
    question="Which failures?",
    options=[
        Option(label="Transient", consequences="Retry I/O.", needs_input=True),
        Option(label="Fail fast", consequences="Never retry.", recommended=True),
    ],
)
_FULL = EscalationPacket("Which failures?", _REQUEST, (), (("What was tried", "two attempts"),), ())
_DEGRADED = EscalationPacket(None, None, (), (), ("malformed",))
_NO_ALTERNATIVES = EscalationPacket("Which failures?", None, (), (("What was tried", " "),), ())

_ANSWER_KINDS = [ChoiceKind.ANSWER_OPTION] * 2 + [ChoiceKind.OWN_ANSWER, ChoiceKind.CANCEL]


def _escalation(reason: EscalationReason) -> Escalation:
    return Escalation(
        id="E-1",
        task_id="T-1",
        reason=reason,
        headline="h",
        packet_path="p",
        options=options_for(reason),
        status=EscalationStatus.OPEN,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


_OWN_OR_CANCEL = [ChoiceKind.OWN_ANSWER, ChoiceKind.CANCEL]
_RETRY_OR_CANCEL = [ChoiceKind.RETRY, ChoiceKind.CANCEL]
_EXHAUSTED_KINDS = [*_ANSWER_KINDS[:3], ChoiceKind.ACCEPT, ChoiceKind.CANCEL]
_DECISION = EscalationReason.DECISION_REQUIRED
_EXHAUSTED = EscalationReason.ITERATIONS_EXHAUSTED
_BLOCKED = EscalationReason.INTEGRATION_BLOCKED
_STUCK = EscalationReason.AGENT_STUCK
_REASON_ONLY = [AttemptKind.REASON]
_NO_ROWS: list[AttemptKind] = []

_CASES = {
    "alternatives": (_DECISION, _FULL, (_ANSWER_KINDS, 1, True, _REASON_ONLY, False)),
    "question only": (_DECISION, _NO_ALTERNATIVES, (_OWN_OR_CANCEL, 0, True, _NO_ROWS, False)),
    "degraded": (_DECISION, _DEGRADED, (_OWN_OR_CANCEL, 0, False, _NO_ROWS, False)),
    "missing": (_DECISION, None, (_OWN_OR_CANCEL, 0, False, _NO_ROWS, False)),
    "accept": (_EXHAUSTED, _FULL, (_EXHAUSTED_KINDS, 1, True, _REASON_ONLY, True)),
    "retry": (_BLOCKED, _FULL, (_RETRY_OR_CANCEL, 0, False, _REASON_ONLY, True)),
    "retry, no packet": (_STUCK, None, (_RETRY_OR_CANCEL, 0, False, _NO_ROWS, True)),
}


def test_panel_orders_choices_preselects_and_carries_the_disabled_reason() -> None:
    for name, (reason, packet, expected) in _CASES.items():
        panel = build_resolution_panel(_escalation(reason), packet, "Title", None)

        actual = (
            [choice.kind for choice in panel.choices],
            panel.preselected,
            panel.question is not None,
            [item.kind for item in panel.details],
            panel.cause is not None,
        )
        assert actual == expected, name

    disabled = build_resolution_panel(_escalation(_DECISION), _FULL, "Title", "already resolved")
    assert disabled.disabled_reason == "already resolved"
    assert len(disabled.choices) == len(_ANSWER_KINDS)


def test_panel_carries_the_needs_input_flag_of_each_alternative() -> None:
    panel = build_resolution_panel(_escalation(_DECISION), _FULL, "Title", None)

    assert [choice.needs_input for choice in panel.choices[:2]] == [True, False]
