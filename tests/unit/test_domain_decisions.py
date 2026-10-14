from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shikiru.domain.briefing import VerdictCounts
from shikiru.domain.decisions import OperatorAction, derive_decisions
from shikiru.domain.events import Event, EventKind

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _event(event_id: int, kind: EventKind, **data: object) -> Event:
    return Event(
        id=event_id, ts=START + timedelta(minutes=event_id), kind=kind, message="m", data=data
    )


def _verdict(event_id: int, decision: str, *severities: str) -> Event:
    issues = [{"severity": severity, "description": "d"} for severity in severities]
    return _event(event_id, EventKind.VERDICT, decision=decision, issues=issues)


def test_rounds_follow_the_verdicts_and_operator_actions_follow_time() -> None:
    decisions = derive_decisions(
        [_verdict(1, "changes_requested", "major", "major", "minor"), _verdict(5, "accept")], []
    )
    assert decisions.rounds == (
        VerdictCounts("changes_requested", 0, 2, 1),
        VerdictCounts("accept", 0, 0, 0),
    )
    assert derive_decisions([], []).rounds == ()
    events = [
        _event(7, EventKind.OPERATOR_MESSAGE, role="leader", text="look at X"),
        _event(6, EventKind.ESCALATION_RESOLVED, resolution="cancel"),
        _event(5, EventKind.ESCALATION_RESOLVED, resolution="accept"),
        _event(4, EventKind.ESCALATION_RESOLVED, resolution="retry"),
        _event(3, EventKind.ESCALATION_RESOLVED, resolution="answer", text="  Use\nmocked QA "),
        _event(2, EventKind.OPERATOR_MESSAGE, role="leader"),
    ]
    assert [(entry.action, entry.text) for entry in derive_decisions([], events).operator] == [
        (OperatorAction.MESSAGED, None),
        (OperatorAction.ANSWERED, "Use mocked QA"),
        (OperatorAction.RETRIED, None),
        (OperatorAction.ACCEPTED, None),
        (OperatorAction.CANCELLED, None),
        (OperatorAction.MESSAGED, "look at X"),
    ]
