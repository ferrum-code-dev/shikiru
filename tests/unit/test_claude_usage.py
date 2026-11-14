from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from tests.support.clock import FakeClock

SESSION_ID = "session-without-usage"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
INIT_LINE = '{"type":"system","subtype":"init","session_id":"session-without-usage"}'
RESULT_LINE = '{"type":"result","subtype":"success","result":"Finished"}'
CONTEXT_SESSION = "session-context"
CONTEXT_NOW = datetime(2026, 1, 1, tzinfo=UTC)
CONTEXT_INIT_LINE = '{"type":"system","subtype":"init","session_id":"session-context"}'
CONTEXT_ASSISTANT_FIRST = (
    '{"type":"assistant","message":{"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":2,"cache_creation_input_tokens":3},"content":[]}}'
)
CONTEXT_ASSISTANT_LATEST = (
    '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":2,'
    '"cache_read_input_tokens":20,"cache_creation_input_tokens":30},"content":[]}}'
)
CONTEXT_RESULT_TOTALS = (
    '{"type":"result","subtype":"success","usage":{"input_tokens":100,'
    '"output_tokens":20,"cache_read_input_tokens":80,"cache_creation_input_tokens":90},'
    '"modelUsage":{"haiku":{"contextWindow":200,"costBasis":"unknown"}}}'
)
ZERO_CONTEXT_ASSISTANT = (
    '{"type":"assistant","message":{"usage":{"input_tokens":0,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},"content":[]}}'
)
ZERO_CONTEXT_RESULT = (
    '{"type":"result","subtype":"success","usage":{"input_tokens":17094,'
    '"output_tokens":2,"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"modelUsage":{"haiku":{"contextWindow":200000,"costBasis":"unknown"}}}'
)


def test_success_without_reported_usage_does_not_create_measured_token_zeros() -> None:
    clock = FakeClock(NOW)
    parser = ClaudeTurnParser(
        session_id=SESSION_ID,
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    signals = [*parser.feed(INIT_LINE), *parser.feed(RESULT_LINE), *parser.finish(0, "")]

    assert signals == [
        SessionStarted(SESSION_ID),
        UsageReported(Usage(None, None, None, None, None, COST_BASIS_UNKNOWN, None)),
        TurnEnded("Finished"),
    ]


def test_turn_totals_keep_the_last_assistant_context_snapshot() -> None:
    clock = FakeClock(CONTEXT_NOW)
    parser = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    signals = [
        *parser.feed(CONTEXT_INIT_LINE),
        *parser.feed(CONTEXT_ASSISTANT_FIRST),
        *parser.feed(CONTEXT_ASSISTANT_LATEST),
        *parser.feed(CONTEXT_RESULT_TOTALS),
        *parser.finish(0, ""),
    ]

    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert (usage.uncached_input_tokens, usage.output_tokens) == (100, 20)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (80, 90)
    assert usage.cost_usd is None
    assert usage.context_used_pct == 30


def test_complete_reported_zero_context_remains_a_measured_zero() -> None:
    clock = FakeClock(CONTEXT_NOW)
    parser = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    signals = [
        *parser.feed(CONTEXT_INIT_LINE),
        *parser.feed(ZERO_CONTEXT_ASSISTANT),
        *parser.feed(ZERO_CONTEXT_RESULT),
        *parser.finish(0, ""),
    ]

    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert usage.context_used_pct == 0


def test_resumed_legacy_unknown_tokens_stay_unknown_after_a_reported_turn() -> None:
    clock = FakeClock(CONTEXT_NOW)
    parser = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=Usage(None, None, None, None, None, COST_BASIS_UNKNOWN, None),
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    signals = [
        *parser.feed(CONTEXT_INIT_LINE),
        *parser.feed(CONTEXT_RESULT_TOTALS),
        *parser.finish(0, ""),
    ]

    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert (
        usage.uncached_input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
    ) == (None, None, None, None)


def test_unreported_middle_turn_keeps_later_claude_totals_unknown() -> None:
    clock = FakeClock(CONTEXT_NOW)
    first = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None),
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    first_signals = [
        *first.feed(CONTEXT_INIT_LINE),
        *first.feed(
            '{"type":"result","subtype":"success","result":"first",'
            '"usage":{"input_tokens":100,"output_tokens":10,'
            '"cache_read_input_tokens":20,"cache_creation_input_tokens":0}}'
        ),
        *first.finish(0, ""),
    ]
    [first_usage] = [signal.usage for signal in first_signals if isinstance(signal, UsageReported)]

    missing = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=first_usage,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    missing_signals = [
        *missing.feed(CONTEXT_INIT_LINE),
        *missing.feed(RESULT_LINE),
        *missing.finish(0, ""),
    ]
    [missing_usage] = [
        signal.usage for signal in missing_signals if isinstance(signal, UsageReported)
    ]

    later = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=missing_usage,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    later_signals = [
        *later.feed(CONTEXT_INIT_LINE),
        *later.feed(
            '{"type":"result","subtype":"success","result":"later",'
            '"usage":{"input_tokens":50,"output_tokens":5,'
            '"cache_read_input_tokens":10,"cache_creation_input_tokens":0}}'
        ),
        *later.finish(0, ""),
    ]
    [later_usage] = [signal.usage for signal in later_signals if isinstance(signal, UsageReported)]

    assert (first_usage.uncached_input_tokens, first_usage.output_tokens) == (100, 10)
    assert missing_usage == Usage(None, None, None, None, None, COST_BASIS_UNKNOWN, None)
    assert later_usage == Usage(None, None, None, None, None, COST_BASIS_UNKNOWN, None)


@pytest.mark.parametrize("interrupted", [False, True], ids=("failed", "interrupted"))
def test_failed_or_interrupted_started_turn_records_usage_gap(interrupted: bool) -> None:
    clock = FakeClock(CONTEXT_NOW)
    previous = Usage(100, 10, 20, 0, 0.5, "billed", 30)
    parser = ClaudeTurnParser(
        session_id=CONTEXT_SESSION,
        model="haiku",
        initial_usage=previous,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    if interrupted:
        signals = [*parser.feed(CONTEXT_INIT_LINE), *parser.finish(130, "", interrupted=True)]
    else:
        signals = [
            *parser.feed(CONTEXT_INIT_LINE),
            *parser.feed('{"type":"result","subtype":"error","is_error":true,"result":"failed"}'),
            *parser.finish(1, ""),
        ]

    assert UsageReported(Usage(None, None, None, None, 0.5, "billed", 30)) in signals
    if interrupted:
        assert not any(isinstance(signal, TurnEnded) for signal in signals)
    else:
        assert any(isinstance(signal, TurnFailed) for signal in signals)
