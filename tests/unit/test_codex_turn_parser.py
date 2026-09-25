"""Codex turn parser behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    Compacted,
    QuotaReported,
    QuotaWindow,
    SessionStarted,
    TurnEnded,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.codex.turn import CodexTurnParser
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

_SAMPLE_NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
_SESSION_ID = "session-late-rollout"
_CONTEXT_WINDOW = 100_000
_PRE_COMPACTION_TOKENS = 60_000
_POST_COMPACTION_TOKENS = 25_000
_INPUT_TOKENS = 25_000
_OUTPUT_TOKENS = 500
_CACHED_INPUT_TOKENS = 5_000
_PRIMARY_USED_PERCENT = 30.0
_SECONDARY_USED_PERCENT = 40.0
_RESET_AT = datetime(2030, 1, 1, tzinfo=UTC)


def test_finish_reads_rollout_created_after_session_started(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    clock = FakeClock(_SAMPLE_NOW)
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=home,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    started = parser.feed(json.dumps({"type": "thread.started", "thread_id": _SESSION_ID}))
    parser.feed(
        json.dumps(
            {
                "type": "turn.completed",
                "text": "Turn complete",
                "usage": {"input_tokens": 100, "output_tokens": 10},
            }
        )
    )
    rollout = (
        home
        / "sessions"
        / "2030"
        / "01"
        / "01"
        / f"rollout-2030-01-01T00-00-00-{_SESSION_ID}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 60_000},
                                "last_token_usage": {"input_tokens": 60_000},
                                "model_context_window": _CONTEXT_WINDOW,
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "compacted",
                        "payload": {
                            "pre_tokens": _PRE_COMPACTION_TOKENS,
                            "post_tokens": _POST_COMPACTION_TOKENS,
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": _INPUT_TOKENS,
                                    "cached_input_tokens": _CACHED_INPUT_TOKENS,
                                    "output_tokens": _OUTPUT_TOKENS,
                                },
                                "last_token_usage": {"input_tokens": _INPUT_TOKENS},
                                "model_context_window": _CONTEXT_WINDOW,
                            },
                            "rate_limits": {
                                "primary": {
                                    "used_percent": _PRIMARY_USED_PERCENT,
                                    "resets_at": _RESET_AT.isoformat(),
                                },
                                "secondary": {"used_percent": _SECONDARY_USED_PERCENT},
                            },
                        },
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    signals = parser.finish(0, "")

    assert started == [SessionStarted(_SESSION_ID)]
    assert signals == [
        Compacted(_PRE_COMPACTION_TOKENS, _POST_COMPACTION_TOKENS),
        QuotaReported(
            (
                QuotaWindow("primary", _PRIMARY_USED_PERCENT, _RESET_AT, _SAMPLE_NOW),
                QuotaWindow("secondary", _SECONDARY_USED_PERCENT, None, _SAMPLE_NOW),
            )
        ),
        UsageReported(
            Usage(
                _INPUT_TOKENS,
                _OUTPUT_TOKENS,
                _CACHED_INPUT_TOKENS,
                0,
                None,
                "unknown",
                25.0,
            )
        ),
        TurnEnded("Turn complete"),
    ]
