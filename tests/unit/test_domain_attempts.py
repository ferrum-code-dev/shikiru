from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.attempts import summarise_attempts
from shikiru.domain.briefing import FACTS_COMMITS_HEADING, FACTS_DIFF_STAT_HEADING
from shikiru.domain.events import Event, EventKind
from shikiru.domain.reports import Commit, ImplementationReport, Issue, Verdict
from shikiru.domain.reports import TestRun as AgentTestRun
from tests.support.factories import make_task

if TYPE_CHECKING:
    from shikiru.domain.reports import Report
    from shikiru.domain.tasks import Task

_REASON = "The Leader escalated during a consult turn."
_LIMIT = 8
_MAX_ITERATIONS = 4
_FACTS = (
    f"{FACTS_COMMITS_HEADING}\n\n"
    "a1 one\nb2 two\n\n"
    f"{FACTS_DIFF_STAT_HEADING}\n\n"
    " x.py | 3 ++-\n 2 files changed, 12 insertions(+), 4 deletions(-)\n\n### status"
)


def _event(kind: EventKind, **data: object) -> Event:
    return Event(id=1, ts=datetime(2026, 1, 1, tzinfo=UTC), kind=kind, message="boom", data=data)


def _task(iteration: int = 0) -> Task:
    return make_task(iteration=iteration)


def _implementation() -> ImplementationReport:
    return ImplementationReport(
        status="completed",
        summary="done",
        commits=[Commit(sha="a1", subject="s")] * 3,
        tests=[AgentTestRun(name=str(i), result="passed") for i in range(12)]
        + [AgentTestRun(name="x", result="failed")],
        trackers_updated=True,
    )


def _digest(
    events: list[Event],
    reports: dict[str, Report] | None = None,
    *,
    iteration: int = 0,
    facts: str = "",
    limit: int = _LIMIT,
) -> list[str]:
    return summarise_attempts(
        _REASON,
        events,
        reports or {},
        _task(iteration),
        facts,
        max_iterations=_MAX_ITERATIONS,
        limit=limit,
    )


def _completed(turn: str, role: str = "leader") -> Event:
    return _event(EventKind.TURN_COMPLETED, step="verify", role=role, outcome="accept", turn=turn)


def test_nothing_recorded_leaves_only_the_reason() -> None:
    assert _digest([]) == [_REASON]


def test_recorded_work_reads_chronologically_and_omits_lines_without_data() -> None:
    events = [
        *[_event(EventKind.STEP_STARTED, step="implement")] * 3,
        *[_event(EventKind.STEP_STARTED, step="verify")] * 2,
        _event(EventKind.STEP_STARTED, step="consult"),
        _completed("t1", "implementer"),
        _completed("t2"),
        _event(EventKind.NUDGE),
        _event(EventKind.NUDGE),
        _event(EventKind.RUNG_CHANGED),
        _event(EventKind.OPERATOR_MESSAGE),
    ]
    reports: dict[str, Report] = {
        "t1": _implementation(),
        "t2": Verdict(
            decision="changes_requested",
            summary="s",
            issues=[Issue(severity="blocker", description="d")],
        ),
    }

    lines = _digest(events, reports, iteration=2, facts=_FACTS)

    assert lines == [
        _REASON,
        (
            "Rounds: implement \N{MULTIPLICATION SIGN}3, verify \N{MULTIPLICATION SIGN}2, "
            "consult \N{MULTIPLICATION SIGN}1 (iteration 2 of 4)"
        ),
        "Implementer: 3 commits, 12 tests passed, 1 failed",
        "Leader verdict: changes requested (1 blocker)",
        "Recovery: 2 nudges, 1 rung change",
        "Produced: 2 commits, 2 files, +12 -4",
        "Operator: 1 message",
    ]


def test_turns_without_reports_and_failed_turns_still_get_a_line() -> None:
    lines = _digest([_completed("gone"), _event(EventKind.TURN_FAILED, role="implementer")])

    assert lines == [_REASON, "Leader: verify accept", "Implementer: turn failed (boom)"]


def test_the_cap_holds_and_older_turns_collapse_into_an_earlier_count() -> None:
    events = [_completed(f"t{i}") for i in range(20)]
    events.append(_event(EventKind.NUDGE))

    lines = _digest(events)

    assert len(lines) == _LIMIT
    assert lines[0] == _REASON
    assert lines[1] == "+15 earlier"
    assert lines[-1] == "Recovery: 1 nudge"
    assert _digest(events, limit=1) == [_REASON]
