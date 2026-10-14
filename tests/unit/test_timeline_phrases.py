from __future__ import annotations

from datetime import UTC, datetime

import pytest

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


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        pytest.param(
            _event(EventKind.STEP_STARTED, step="implement", iteration=2),
            Phrase("step started · implement, round 2"),
            id="step-names-its-round",
        ),
        pytest.param(
            _event(EventKind.VERDICT, "needs work", decision="changes_requested"),
            Phrase("verdict · changes requested", GLYPH_FAIL, Tone.FAILURE),
            id="changes-requested-fails",
        ),
        pytest.param(
            _event(EventKind.RUNG_CHANGED, role="leader", **{"from": "opus", "to": "sonnet"}),
            Phrase("rung changed · leader · opus to sonnet", GLYPH_ATTENTION, Tone.WARNING),
            id="rung-change-names-both-models",
        ),
        pytest.param(
            _event(EventKind.ESCALATION, "decision required"),
            Phrase("escalated · decision required", GLYPH_ALARM, Tone.CRITICAL),
            id="escalation-is-critical",
        ),
        pytest.param(
            _event(EventKind.ENGINE_STARTED, "engine started"),
            Phrase("T-1 engine started"),
            id="unlisted-kind-falls-back-to-its-message",
        ),
    ],
)
def test_engine_events_read_as_one_human_phrase(event: Event, expected: Phrase) -> None:
    assert event_phrase(event) == expected


@pytest.mark.parametrize(
    ("signal", "data", "expected"),
    [
        pytest.param(
            "Message",
            {"text": "  Plan looks\nright,   start  "},
            Phrase('"Plan looks right, start"'),
            id="message-collapses-whitespace-and-is-quoted",
        ),
        pytest.param(
            "ToolStarted",
            {"name": "Bash", "detail": '{"command":"pytest -q"}'},
            Phrase("ran pytest -q"),
            id="shell-tool-shows-its-command",
        ),
        pytest.param(
            "ToolFinished",
            {"name": "Bash", "outcome": "failure", "exit_code": 2},
            Phrase("Bash · exit 2", GLYPH_FAIL, Tone.FAILURE),
            id="failed-tool-fails",
        ),
        pytest.param("ToolFinished", {"name": "Read"}, None, id="bare-finish-has-no-row"),
        pytest.param("UsageReported", {}, None, id="usage-is-bookkeeping"),
        pytest.param(
            "TurnFailed",
            {"kind": "quota", "message": "limit reached"},
            Phrase("quota: limit reached", GLYPH_FAIL, Tone.FAILURE),
            id="failed-turn-shows-kind-and-message",
        ),
        pytest.param("FutureSignal", {}, Phrase("FutureSignal"), id="unknown-signal-is-visible"),
    ],
)
def test_agent_signals_read_as_one_human_phrase(
    signal: str, data: dict[str, object], expected: Phrase | None
) -> None:
    assert signal_phrase(signal, data) == expected
