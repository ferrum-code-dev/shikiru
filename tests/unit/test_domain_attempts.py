from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.attempts import (
    AttemptItem,
    AttemptKind,
    AttemptTone,
    attempt_lines,
    parse_attempt_lines,
    summarise_attempts,
)
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
) -> tuple[AttemptItem, ...]:
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


def _toned(item: AttemptItem, tone: AttemptTone) -> list[str]:
    return [part.text for part in item.parts if part.tone is tone]


def test_nothing_recorded_leaves_only_the_reason() -> None:
    assert [item.kind for item in _digest([])] == [AttemptKind.REASON]


def test_recorded_work_reads_chronologically_and_omits_rows_without_data() -> None:
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

    items = _digest(events, reports, iteration=2, facts=_FACTS)

    assert [(item.kind, item.label) for item in items] == [
        (AttemptKind.REASON, ""),
        (AttemptKind.ROUNDS, "Rounds"),
        (AttemptKind.TURN_OK, "Implementer"),
        (AttemptKind.VERDICT, "Leader"),
        (AttemptKind.RECOVERY, "Recovery"),
        (AttemptKind.PRODUCED, "Produced"),
        (AttemptKind.OPERATOR, "Operator"),
    ]
    rounds, implementer, verdict, recovery, produced, operator = items[1:]
    assert _toned(rounds, AttemptTone.STEP) == ["implement", "verify", "consult"]
    assert _toned(rounds, AttemptTone.REWORK) == ["(iteration 2 of 4)"]
    assert _toned(implementer, AttemptTone.SUCCESS) == ["3 commits", "12 tests passed"]
    assert _toned(implementer, AttemptTone.FAILURE) == ["1 failed"]
    assert verdict.text == "changes requested (1 blocker)"
    assert _toned(verdict, AttemptTone.REWORK) == ["changes requested"]
    assert _toned(verdict, AttemptTone.FAILURE) == ["1 blocker"]
    assert recovery.text == "2 nudges, 1 rung change"
    assert _toned(recovery, AttemptTone.REWORK) == [recovery.text]
    assert produced.text == "2 commits, 2 files, +12 \N{MINUS SIGN}4"
    assert _toned(produced, AttemptTone.SUCCESS) == ["+12"]
    assert _toned(produced, AttemptTone.FAILURE) == ["\N{MINUS SIGN}4"]
    assert operator.text == "1 message"


def test_turns_without_reports_and_failed_turns_still_get_a_row() -> None:
    items = _digest([_completed("gone"), _event(EventKind.TURN_FAILED, role="implementer")])

    assert [(item.kind, item.line) for item in items[1:]] == [
        (AttemptKind.TURN_OK, "Leader: verify accept"),
        (AttemptKind.TURN_FAILED, "Implementer: turn failed (boom)"),
    ]
    assert _toned(items[2], AttemptTone.FAILURE) == ["turn failed (boom)"]


def test_the_cap_holds_and_older_turns_collapse_into_an_earlier_count() -> None:
    events = [_completed(f"t{i}") for i in range(20)]
    events.append(_event(EventKind.NUDGE))

    items = _digest(events)

    assert len(items) == _LIMIT
    assert items[0].kind is AttemptKind.REASON
    assert (items[1].kind, items[1].text) == (AttemptKind.EARLIER, "+15 earlier")
    assert items[-1].kind is AttemptKind.RECOVERY
    assert [item.kind for item in _digest(events, limit=1)] == [AttemptKind.REASON]


def test_the_packet_lines_rebuild_the_same_rows() -> None:
    events = [
        *[_event(EventKind.STEP_STARTED, step="implement")] * 2,
        *[_completed(f"t{i}") for i in range(12)],
        _event(EventKind.TURN_FAILED, role="implementer"),
        _event(EventKind.NUDGE),
    ]
    items = _digest(events, {"t0": _implementation()}, iteration=1, facts=_FACTS)

    assert parse_attempt_lines(attempt_lines(items)) == items


PACKET_ROWS: list[tuple[str, str, list[AttemptKind]]] = [
    ("canned-sentence", "One implementation round.", [AttemptKind.REASON]),
    (
        "digest-with-hyphen-minus",
        (
            "Stuck: it looped.\nRounds: implement \N{MULTIPLICATION SIGN}2\n"
            "Leader verdict: accept\nProduced: 1 commit, 1 file, +3 -1\n+2 earlier"
        ),
        [
            AttemptKind.REASON,
            AttemptKind.ROUNDS,
            AttemptKind.VERDICT,
            AttemptKind.PRODUCED,
            AttemptKind.EARLIER,
        ],
    ),
    ("blank", " \n", []),
]


def test_parsing_a_packet_text_yields_rows_of_each_kind() -> None:
    for name, packet, kinds in PACKET_ROWS:
        assert [item.kind for item in parse_attempt_lines(packet)] == kinds, name
