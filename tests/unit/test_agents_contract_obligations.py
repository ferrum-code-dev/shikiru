from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fake_agent.turns import FakeTurnParser

from shikiru.agents.testing import assert_agent_obligations

if TYPE_CHECKING:
    from shikiru.agents.contracts import AgentSignal


def _parse(entries: list[dict[str, object]]) -> list[AgentSignal]:
    parser = FakeTurnParser()
    return [signal for entry in entries for signal in parser.feed(json.dumps(entry))]


def test_agent_obligations_accept_a_valid_compacted_session_stream() -> None:
    signals = _parse(
        [
            {"event": "session_started", "session_id": "session-a"},
            {
                "event": "usage_reported",
                "usage": {
                    "uncached_input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 10,
                    "cache_write_tokens": 5,
                    "cost_usd": None,
                    "cost_basis": "unknown",
                    "context_used_pct": 90,
                },
            },
            {"event": "compacted", "pre_tokens": 2000, "post_tokens": 400},
            {
                "event": "usage_reported",
                "usage": {
                    "uncached_input_tokens": 120,
                    "output_tokens": 60,
                    "cache_read_tokens": 12,
                    "cache_write_tokens": 6,
                    "cost_usd": None,
                    "cost_basis": "unknown",
                    "context_used_pct": 30,
                },
            },
            {"event": "turn_ended", "last_message": "done"},
        ]
    )

    assert_agent_obligations(signals, "session-a")


@pytest.mark.parametrize(
    ("entries", "session_id"),
    [
        (
            [
                {"event": "message", "text": "before start"},
                {"event": "session_started", "session_id": "session-a"},
                {"event": "turn_ended", "last_message": "done"},
            ],
            "session-a",
        ),
        (
            [
                {"event": "session_started", "session_id": "session-a"},
                {"event": "session_started", "session_id": "session-a"},
                {"event": "turn_ended", "last_message": "done"},
            ],
            "session-a",
        ),
        (
            [
                {"event": "session_started", "session_id": "wrong-session"},
                {"event": "turn_ended", "last_message": "done"},
            ],
            "session-a",
        ),
    ],
)
def test_agent_obligations_reject_invalid_session_start_sequences(
    entries: list[dict[str, object]], session_id: str
) -> None:
    with pytest.raises(AssertionError):
        assert_agent_obligations(_parse(entries), session_id)


def test_agent_obligations_reject_usage_decreases_after_compaction() -> None:
    signals = _parse(
        [
            {"event": "session_started", "session_id": "session-a"},
            {
                "event": "usage_reported",
                "usage": {
                    "uncached_input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 10,
                    "cache_write_tokens": 5,
                    "cost_usd": None,
                    "cost_basis": "unknown",
                    "context_used_pct": 90,
                },
            },
            {"event": "compacted", "pre_tokens": 2000, "post_tokens": 400},
            {
                "event": "usage_reported",
                "usage": {
                    "uncached_input_tokens": 120,
                    "output_tokens": 60,
                    "cache_read_tokens": 12,
                    "cache_write_tokens": 6,
                    "cost_usd": None,
                    "cost_basis": "unknown",
                    "context_used_pct": 95,
                },
            },
            {"event": "turn_ended", "last_message": "done"},
        ]
    )

    with pytest.raises(AssertionError):
        assert_agent_obligations(signals, "session-a")
