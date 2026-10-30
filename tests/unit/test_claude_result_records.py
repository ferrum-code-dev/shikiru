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
        Message("Run the required verification"),
        Message("Verification is continuing."),
        ToolStarted("Bash", '{"command":"[redacted verification]"}'),
        Message("Verification finished; reviewing the result"),
        ToolFinished("Bash", ToolOutcome.SUCCESS),
        ToolStarted("shikiru report", '{"file":"[redacted report]"}'),
        ToolFinished("shikiru report", ToolOutcome.SUCCESS),
        Message("The latest follow-up is complete."),
    ]
    assert len([signal for signal in activity if isinstance(signal, SessionStarted)]) == 1
    [usage_signal, terminal_signal] = terminal
    assert isinstance(usage_signal, UsageReported)
    assert usage_signal.usage == Usage(
        122, 49109, 4828854, 121323, 0.2708916, "api_equivalent", 0.0022
    )
    assert terminal_signal == TurnEnded("The latest follow-up is complete.")


def test_intermediate_result_before_compact_boundary_does_not_fail_compaction() -> None:
    parser = _parser(compact=True)

    activity = _feed(parser, "turn_background_result_compaction.jsonl")
    terminal = parser.finish(0, "")

    assert activity == [
        SessionStarted("session-compact-redacted"),
        Message("Compact the session context"),
        Message("Context compaction completed"),
        Compacted(147542, 13268),
    ]
    assert terminal == [
        UsageReported(Usage(2111, 11959, 146037, 910, 1.481823215, "api_equivalent", None)),
        TurnEnded(""),
    ]


def test_later_assistant_message_becomes_the_final_text_after_an_intermediate_result() -> None:
    parser = _parser()
    parser.feed('{"type":"system","subtype":"init","session_id":"session-a"}')
    parser.feed(
        '{"type":"result","subtype":"success","result":"Earlier outcome",'
        '"stop_reason":"end_turn","terminal_reason":"completed"}'
    )

    activity = parser.feed(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Later work outcome"}]}}'
    )
    terminal = parser.finish(0, "")

    assert activity == [Message("Later work outcome")]
    assert terminal == [TurnEnded("Later work outcome")]


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
