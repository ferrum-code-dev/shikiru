from __future__ import annotations

from datetime import UTC, datetime

from shikiru.agents.contracts import SessionStarted, TurnEnded, UsageReported
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

    signals = [*parser.feed(INIT_LINE), *parser.feed(RESULT_LINE)]

    assert signals == [SessionStarted(SESSION_ID), TurnEnded("Finished")]


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
    ]

    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert (usage.input_tokens, usage.output_tokens) == (100, 20)
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
    ]

    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert usage.context_used_pct == 0
