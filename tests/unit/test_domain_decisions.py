from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shikiru.domain.decisions import Decider, derive_decisions
from shikiru.domain.events import Event, EventKind

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LEADER, YOU, ENGINE = Decider.LEADER, Decider.YOU, Decider.ENGINE
PR_URL = "https://github.com/o/r/pull/41"


def _event(event_id: int, kind: EventKind, message: str = "m", **data: object) -> Event:
    return Event(
        id=event_id, ts=START + timedelta(minutes=event_id), kind=kind, message=message, data=data
    )


def test_decisions_are_a_chronological_log_of_who_decided_what_and_why() -> None:
    issues = [
        {"severity": "minor", "description": "style nit"},
        {"severity": "major", "description": "no test", "location": "auth.py"},
        {"severity": "blocker", "description": "redirect loops"},
        {"severity": "major", "description": "dropped"},
    ]
    events = [
        _event(13, EventKind.TASK_DONE),
        _event(12, EventKind.PR_OPENED, url=PR_URL),
        _event(11, EventKind.HANDED_BACK, role="implementer"),
        _event(10, EventKind.TAKEN_OVER, role="implementer"),
        _event(9, EventKind.HOLD_CLEARED, hold="paused"),
        _event(8, EventKind.HOLD_SET, hold="paused"),
        _event(7, EventKind.HOLD_SET, hold="intervention"),
        _event(6, EventKind.OPERATOR_MESSAGE, role="leader", text=" look\nat X "),
        _event(5, EventKind.RUNG_CHANGED, role="implementer", to="opus", operator=True),
        _event(4, EventKind.RUNG_CHANGED, role="implementer", to="opus", **{"from": "sonnet"}),
        _event(
            3,
            EventKind.SCOPE_CHANGE,
            new_task_ids=["t-15", "t-16"],
            scope_changes=["one", "two", "three", "four"],
        ),
        _event(2, EventKind.VERDICT, decision="changes_requested", summary="rework", issues=issues),
        _event(1, EventKind.VERDICT, decision="accept", summary="plan ok"),
    ]

    entries = [
        (e.at.minute, e.decider, e.headline, e.details) for e in derive_decisions(events).entries
    ]

    assert entries == [
        (1, LEADER, "accepted", ("plan ok",)),
        (2, LEADER, "changes requested", ("rework", "redirect loops", "no test (auth.py)")),
        (3, LEADER, "added t-15, t-16", ("one", "two", "three")),
        (4, ENGINE, "Implementer: sonnet \N{RIGHTWARDS ARROW} opus", ()),
        (5, YOU, "Implementer \N{RIGHTWARDS ARROW} opus", ()),
        (6, YOU, 'messaged Leader "look at X"', ()),
        (8, YOU, "paused", ()),
        (9, YOU, "resumed", ()),
        (10, YOU, "took over Implementer", ()),
        (11, YOU, "handed back Implementer", ()),
        (12, ENGINE, "PR #41 opened", ()),
        (13, ENGINE, "PR #41 merged", ()),
    ]

    escalations = [
        _event(1, EventKind.ESCALATION, "which provider?", escalation_id="e1"),
        _event(2, EventKind.ESCALATION_RESOLVED, resolution="answer", text="Use\n OAuth"),
        _event(3, EventKind.ESCALATION, "again?", escalation_id="e2"),
        _event(4, EventKind.ESCALATION_RESOLVED, resolution="retry"),
        _event(5, EventKind.VERDICT, decision="escalate", summary="asks"),
        _event(6, EventKind.ESCALATION_RESOLVED, resolution="accept"),
        _event(7, EventKind.ESCALATION_RESOLVED, resolution="cancel"),
        _event(8, EventKind.TASK_CANCELLED),
        _event(9, EventKind.ESCALATION, "still open?", escalation_id="e3"),
    ]

    assert [(e.decider, e.headline) for e in derive_decisions(escalations).entries] == [
        (LEADER, "escalated: which provider?"),
        (YOU, 'answered "Use OAuth"'),
        (LEADER, "escalated: again?"),
        (YOU, "retried"),
        (YOU, "accepted as is"),
        (YOU, "cancelled"),
        (LEADER, "escalated: still open?"),
    ]
    assert derive_decisions([_event(1, EventKind.NUDGE)]).entries == ()
