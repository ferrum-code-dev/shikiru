from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from shikiru.agents.contracts import (
    Compacted,
    FailureKind,
    Message,
    SessionStarted,
    ToolFinished,
    ToolOutcome,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from tests.support.clock import FakeClock

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "claude-haiku-4-5-20251001"
_COMPACT_LIMIT = 160_000


def _parser(*, compact: bool = False) -> ClaudeTurnParser:
    clock = FakeClock(_NOW)
    return ClaudeTurnParser(
        session_id="unused-session",
        model=_MODEL,
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        compact_limit_tokens=_COMPACT_LIMIT if compact else None,
    )


def _feed(parser: ClaudeTurnParser, fixture: str) -> list[object]:
    return [
        signal
        for line in (_FIXTURES / fixture).read_text(encoding="utf-8").splitlines()
        for signal in parser.feed(line)
    ]


def test_task_notification_result_keeps_later_activity_and_final_usage() -> None:
    parser = _parser()

    activity = _feed(parser, "turn_background_result_then_activity.jsonl")
    terminal = parser.finish(0, "")

    assert activity == [
        SessionStarted("session-background-redacted"),
        Message("Verification is continuing."),
        ToolStarted("Bash", '{"command":"[redacted verification]"}'),
        ToolFinished("Bash", ToolOutcome.SUCCESS),
        Message("Implementation is complete."),
    ]
    assert terminal == [
        UsageReported(Usage(60, 12, 20, 4, None, "unknown", None)),
        TurnEnded("Implementation is complete."),
    ]


def test_intermediate_result_before_compact_boundary_does_not_fail_compaction() -> None:
    parser = _parser(compact=True)

    activity = _feed(parser, "turn_background_result_compaction.jsonl")
    terminal = parser.finish(0, "")

    assert activity == [SessionStarted("session-compact-redacted"), Compacted(150731, 16926)]
    assert terminal == [
        UsageReported(Usage(0, 0, 0, 0, 0.07, "api_equivalent", None)),
        TurnEnded(""),
    ]


def test_task_notification_without_compact_boundary_remains_a_compaction_failure() -> None:
    parser = _parser(compact=True)
    lines = (
        (_FIXTURES / "turn_background_result_compaction.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    parser.feed(lines[0])
    parser.feed(lines[1])

    [failure] = parser.finish(0, "")

    assert isinstance(failure, TurnFailed)
    assert failure.kind is FailureKind.OTHER
