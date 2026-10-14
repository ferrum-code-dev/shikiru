from __future__ import annotations

from datetime import UTC, datetime

from shikiru.domain.events import Event, EventKind
from shikiru.domain.timeline_phrases import (
    GLYPH_ALARM,
    GLYPH_ATTENTION,
    GLYPH_FAIL,
    Phrase,
    Tone,
    event_phrase,
    signal_phrase,
)

TASK_ID = "T-1"


def _event(kind: EventKind, message: str = "", **data: object) -> Event:
    return Event(
        id=1,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        task_id=TASK_ID,
        kind=kind,
        message=f"{TASK_ID} {message}",
        data=data,
    )


EVENT_ROWS = [
    (
        "step-names-its-round",
        _event(EventKind.STEP_STARTED, step="implement", iteration=2),
        Phrase("step started · implement, round 2"),
    ),
    (
        "changes-requested-fails",
        _event(EventKind.VERDICT, "needs work", decision="changes_requested"),
        Phrase("verdict · changes requested", GLYPH_FAIL, Tone.FAILURE),
    ),
    (
        "rung-change-names-both-models",
        _event(EventKind.RUNG_CHANGED, role="leader", **{"from": "opus", "to": "sonnet"}),
        Phrase("rung changed · leader · opus to sonnet", GLYPH_ATTENTION, Tone.WARNING),
    ),
    (
        "escalation-is-critical",
        _event(EventKind.ESCALATION, "decision required"),
        Phrase("escalated · decision required", GLYPH_ALARM, Tone.CRITICAL),
    ),
    (
        "unlisted-kind-falls-back-to-its-message",
        _event(EventKind.ENGINE_STARTED, "engine started"),
        Phrase("T-1 engine started"),
    ),
]


def test_engine_events_read_as_one_human_phrase() -> None:
    for name, event, expected in EVENT_ROWS:
        assert event_phrase(event) == expected, name


SIGNAL_ROWS: list[tuple[str, str, dict[str, object], Phrase | None]] = [
    (
        "message-collapses-whitespace-and-is-quoted",
        "Message",
        {"text": "  Plan looks\nright,   start  "},
        Phrase('"Plan looks right, start"'),
    ),
    (
        "shell-tool-shows-its-command",
        "ToolStarted",
        {"name": "Bash", "detail": '{"command":"pytest -q"}'},
        Phrase("ran pytest -q"),
    ),
    (
        "failed-tool-fails",
        "ToolFinished",
        {"name": "Bash", "outcome": "failure", "exit_code": 2},
        Phrase("Bash · exit 2", GLYPH_FAIL, Tone.FAILURE),
    ),
    ("bare-finish-has-no-row", "ToolFinished", {"name": "Read"}, None),
    ("usage-is-bookkeeping", "UsageReported", {}, None),
    (
        "failed-turn-shows-kind-and-message",
        "TurnFailed",
        {"kind": "quota", "message": "limit reached"},
        Phrase("quota: limit reached", GLYPH_FAIL, Tone.FAILURE),
    ),
    ("unknown-signal-is-visible", "FutureSignal", {}, Phrase("FutureSignal")),
]


def test_agent_signals_read_as_one_human_phrase() -> None:
    for name, signal, data, expected in SIGNAL_ROWS:
        assert signal_phrase(signal, data) == expected, name
