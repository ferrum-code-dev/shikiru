from __future__ import annotations

from datetime import UTC, datetime

from shikiru.agents.contracts import SessionStarted, TurnEnded
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from tests.support.clock import FakeClock

SESSION_ID = "session-without-usage"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
INIT_LINE = '{"type":"system","subtype":"init","session_id":"session-without-usage"}'
RESULT_LINE = '{"type":"result","subtype":"success","result":"Finished"}'


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
