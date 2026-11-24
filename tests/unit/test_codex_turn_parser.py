"""Codex turn parser behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import (
    Compacted,
    FailureKind,
    QuotaReported,
    QuotaWindow,
    SessionStarted,
    ToolFinished,
    ToolKind,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.domain.model_pricing import priced_usage
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    Action,
    Failed,
    RecoveryPolicy,
    TurnCounters,
    WaitForQuota,
    decide,
)
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
_QUOTA_FAILURE_RESET_AT = datetime(2026, 9, 26, 5, tzinfo=UTC)
_STREAMED_USED_PERCENT = 20
_STREAMED_RESET_AT = datetime(2026, 9, 25, 13, tzinfo=UTC)
_ROLLOUT_USED_PERCENT = 100
_ROLLOUT_RESET_AT = datetime(2026, 9, 25, 18, 0, 1, tzinfo=UTC)
_AFTER_STREAMED_RESET = timedelta(hours=1, seconds=1)
_USAGE_LIMIT_FAILURE = {
    "type": "turn.failed",
    "error": {
        "message": json.dumps(
            {
                "status": 429,
                "code": "usage_limit_reached",
                "message": "Usage limit reached",
            }
        )
    },
}


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
                        "timestamp": _SAMPLE_NOW.isoformat(),
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
                QuotaWindow(
                    "5h",
                    _PRIMARY_USED_PERCENT,
                    _RESET_AT,
                    _SAMPLE_NOW,
                ),
                QuotaWindow(
                    "7d",
                    _SECONDARY_USED_PERCENT,
                    None,
                    _SAMPLE_NOW,
                ),
            )
        ),
        UsageReported(
            Usage(
                _INPUT_TOKENS - _CACHED_INPUT_TOKENS,
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


def test_feed_reports_in_turn_quota_before_the_turn_finishes(tmp_path: Path) -> None:
    clock = FakeClock(_SAMPLE_NOW)
    parser = CodexTurnParser(
        session_id=_SESSION_ID,
        initial_usage=None,
        home=tmp_path / "codex-home",
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )

    signals = parser.feed(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 20,
                            "resets_at": _RESET_AT.isoformat(),
                        }
                    },
                },
            }
        )
    )

    assert signals == [
        QuotaReported(
            (
                QuotaWindow(
                    "5h",
                    20,
                    _RESET_AT,
                    _SAMPLE_NOW,
                ),
            )
        )
    ]


@pytest.mark.parametrize("rollout_contains_streamed_sample", [False, True])
def test_streamed_quota_reset_survives_rollout_dedup_for_recovery(
    tmp_path: Path, rollout_contains_streamed_sample: bool
) -> None:
    home = tmp_path / "codex-home"
    rollout = tmp_path / "rollout.jsonl"
    clock = FakeClock(_SAMPLE_NOW)
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=home,
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    parser.feed(json.dumps({"type": "thread.started", "thread_id": _SESSION_ID}))
    quota_event = {
        "timestamp": (_SAMPLE_NOW + timedelta(seconds=1)).isoformat(),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {
                    "used_percent": 100,
                    "resets_at": _QUOTA_FAILURE_RESET_AT.isoformat(),
                }
            },
        },
    }

    streamed = parser.feed(json.dumps(quota_event))
    if rollout_contains_streamed_sample:
        rollout.write_text(json.dumps(quota_event) + "\n", encoding="utf-8")
    parser.feed(
        json.dumps(
            {
                "type": "turn.failed",
                "error": {
                    "message": json.dumps(
                        {
                            "status": 429,
                            "code": "usage_limit_reached",
                            "message": "Usage limit reached",
                        }
                    )
                },
            }
        )
    )

    signals = parser.finish(1, "")
    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]
    action = _quota_recovery(failure, _SAMPLE_NOW)

    assert isinstance(streamed[0], QuotaReported)
    assert not any(isinstance(signal, QuotaReported) for signal in signals)
    assert failure.kind is FailureKind.QUOTA
    assert failure.resets_at == _QUOTA_FAILURE_RESET_AT
    assert action == WaitForQuota(_QUOTA_FAILURE_RESET_AT + QUOTA_MARGIN)


def test_newer_rollout_quota_replaces_an_earlier_streamed_sample(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    clock = FakeClock(_SAMPLE_NOW)
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path / "codex-home",
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    streamed = parser.feed(
        json.dumps(
            {
                "timestamp": _SAMPLE_NOW.isoformat(),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": _STREAMED_USED_PERCENT,
                            "resets_at": _STREAMED_RESET_AT.isoformat(),
                        }
                    },
                },
            }
        )
    )
    clock.advance(_AFTER_STREAMED_RESET.total_seconds())
    rollout.write_text(
        json.dumps(
            {
                "timestamp": (_SAMPLE_NOW + _AFTER_STREAMED_RESET).isoformat(),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": _ROLLOUT_USED_PERCENT,
                            "resets_at": _ROLLOUT_RESET_AT.isoformat(),
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    parser.feed(json.dumps(_USAGE_LIMIT_FAILURE))

    signals = parser.finish(1, "")
    quota_updates = [signal for signal in signals if isinstance(signal, QuotaReported)]
    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]

    assert streamed == [
        QuotaReported((QuotaWindow("5h", _STREAMED_USED_PERCENT, _STREAMED_RESET_AT, _SAMPLE_NOW),))
    ]
    assert quota_updates == [
        QuotaReported(
            (
                QuotaWindow(
                    "5h",
                    _ROLLOUT_USED_PERCENT,
                    _ROLLOUT_RESET_AT,
                    _SAMPLE_NOW + _AFTER_STREAMED_RESET,
                ),
            )
        )
    ]
    assert failure.kind is FailureKind.QUOTA
    assert failure.resets_at == _ROLLOUT_RESET_AT
    assert _quota_recovery(failure, clock.now()) == WaitForQuota(_ROLLOUT_RESET_AT + QUOTA_MARGIN)


def test_older_rollout_quota_does_not_replace_a_newer_streamed_sample(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    clock = FakeClock(_SAMPLE_NOW + timedelta(seconds=1))
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path / "codex-home",
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    rollout.write_text(
        json.dumps(
            {
                "timestamp": _SAMPLE_NOW.isoformat(),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": _STREAMED_USED_PERCENT,
                            "resets_at": _STREAMED_RESET_AT.isoformat(),
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    streamed = parser.feed(
        json.dumps(
            {
                "timestamp": clock.now().isoformat(),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": _ROLLOUT_USED_PERCENT,
                            "resets_at": _ROLLOUT_RESET_AT.isoformat(),
                        }
                    },
                },
            }
        )
    )
    parser.feed(json.dumps(_USAGE_LIMIT_FAILURE))

    signals = parser.finish(1, "")
    quota_updates = [
        signal for signal in [*streamed, *signals] if isinstance(signal, QuotaReported)
    ]
    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]

    assert quota_updates == [
        QuotaReported((QuotaWindow("5h", _ROLLOUT_USED_PERCENT, _ROLLOUT_RESET_AT, clock.now()),))
    ]
    assert failure.kind is FailureKind.QUOTA
    assert failure.resets_at == _ROLLOUT_RESET_AT
    assert _quota_recovery(failure, clock.now()) == WaitForQuota(_ROLLOUT_RESET_AT + QUOTA_MARGIN)


def _quota_recovery(failure: TurnFailed, now: datetime) -> Action:
    return decide(
        Failed(failure.kind, failure.resets_at, failure.message),
        TurnCounters(),
        RecoveryPolicy(
            now=now,
            max_quota_wait=timedelta(days=2),
            has_next_rung=False,
            has_next_rung_different_agent=False,
            unavailable_initial_seconds=30,
            unavailable_multiplier=4,
            unavailable_max_seconds=300,
            unavailable_max_retries=3,
        ),
    )


def test_prices_new_rollout_call(tmp_path: Path) -> None:
    clock = FakeClock(_SAMPLE_NOW)
    rollout = tmp_path / "rollout.jsonl"
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path,
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices={
            "openai/test": {
                "pricing": {
                    "prompt": "1",
                    "completion": "2",
                    "input_cache_read": "3",
                    "input_cache_write": "4",
                }
            }
        },
        pricer=priced_usage,
    )
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 0,
    }
    rollout.write_text(
        json.dumps({"type": "turn_context", "payload": {"model": "test"}})
        + "\n"
        + json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": usage, "last_token_usage": usage},
                },
            }
        )
        + "\n"
    )
    parser.feed(json.dumps({"type": "thread.started", "thread_id": "x"}))
    parser.feed(json.dumps({"type": "turn.completed", "usage": usage}))
    usages = [signal.usage for signal in parser.finish(0, "") if isinstance(signal, UsageReported)]
    assert usages[0].cost_usd == 80 + 10 * 2 + 20 * 3
    assert usages[0].cost_basis == "api_equivalent"


def _pricing_rollout_count(total: int, last: int) -> dict[str, object]:
    def counts(value: int) -> dict[str, int]:
        return {
            "input_tokens": value,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
        }

    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": counts(total), "last_token_usage": counts(last)},
        },
    }


def _priced_rollout_result(
    tmp_path: Path,
    entries: list[dict[str, object]],
    previous: list[dict[str, object]] | None = None,
) -> Usage:
    clock = FakeClock(_SAMPLE_NOW)
    rollout = tmp_path / "rollout.jsonl"
    initial = previous or []
    rollout.write_text("".join(json.dumps(entry) + "\n" for entry in initial))
    prior = Usage(10, 0, 0, 0, 10.0, "api_equivalent", None) if initial else None
    parser = CodexTurnParser(
        session_id="existing" if initial else None,
        initial_usage=prior,
        home=tmp_path,
        rollout_path=rollout,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices={
            "openai/first": {
                "pricing": {
                    "prompt": "1",
                    "completion": "2",
                    "input_cache_read": "3",
                    "input_cache_write": "4",
                }
            },
            "openai/second": {
                "pricing": {
                    "prompt": "10",
                    "completion": "2",
                    "input_cache_read": "3",
                    "input_cache_write": "4",
                }
            },
        },
        pricer=priced_usage,
    )
    with rollout.open("a") as output:
        output.writelines(json.dumps(entry) + "\n" for entry in entries)
    parser.feed(json.dumps({"type": "thread.started", "thread_id": "existing"}))
    parser.feed(json.dumps({"type": "turn.completed"}))
    return next(
        signal.usage for signal in parser.finish(0, "") if isinstance(signal, UsageReported)
    )


def test_pricing_uses_each_reported_runtime_model(tmp_path: Path) -> None:
    first: dict[str, object] = {"type": "turn_context", "payload": {"model": "first"}}
    second: dict[str, object] = {"type": "turn_context", "payload": {"model": "second"}}
    usage = _priced_rollout_result(
        tmp_path, [first, _pricing_rollout_count(10, 10), second, _pricing_rollout_count(30, 20)]
    )
    assert usage.cost_usd == 210
    assert usage.cost_basis == "api_equivalent"


@pytest.mark.parametrize(
    "context",
    [
        None,
        {"type": "turn_context", "payload": {}},
        {"type": "turn_context", "payload": {"model": " "}},
        {"type": "turn_context", "payload": {"model": "not-listed"}},
    ],
)
def test_pricing_is_unknown_when_runtime_model_is_missing(
    tmp_path: Path, context: dict[str, object] | None
) -> None:
    entries = [] if context is None else [context]
    usage = _priced_rollout_result(tmp_path, [*entries, _pricing_rollout_count(10, 10)])
    assert usage.cost_usd is None
    assert usage.cost_basis == "unknown"


def test_pricing_does_not_use_a_previous_turn_model(tmp_path: Path) -> None:
    prior: list[dict[str, object]] = [
        {"type": "turn_context", "payload": {"model": "first"}},
        _pricing_rollout_count(10, 10),
    ]
    usage = _priced_rollout_result(tmp_path, [_pricing_rollout_count(20, 10)], previous=prior)
    assert usage.cost_usd is None
    assert usage.cost_basis == "unknown"


def test_pricing_clears_model_after_unresolvable_context(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [
        {"type": "turn_context", "payload": {"model": "first"}},
        _pricing_rollout_count(10, 10),
        {"type": "turn_context", "payload": {}},
        _pricing_rollout_count(20, 10),
    ]
    usage = _priced_rollout_result(tmp_path, entries)
    assert usage.cost_usd is None
    assert usage.cost_basis == "unknown"


def test_pricing_a_resumed_turn_uses_its_new_model(tmp_path: Path) -> None:
    prior: list[dict[str, object]] = [
        {"type": "turn_context", "payload": {"model": "first"}},
        _pricing_rollout_count(10, 10),
    ]
    entries: list[dict[str, object]] = [
        {"type": "turn_context", "payload": {"model": "second"}},
        _pricing_rollout_count(20, 10),
    ]
    usage = _priced_rollout_result(tmp_path, entries, previous=prior)
    assert usage.cost_usd == 110
    assert usage.cost_basis == "api_equivalent"


def _parser(tmp_path: Path) -> CodexTurnParser:
    clock = FakeClock(_SAMPLE_NOW)
    return CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path / "codex-home",
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )


def test_web_search_completion_carries_the_search_kind_and_query(tmp_path: Path) -> None:
    signals = _parser(tmp_path).feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_7",
                    "type": "web_search",
                    "query": "codex exec --json schema",
                },
            }
        )
    )

    assert signals == [
        ToolStarted("web_search", "codex exec --json schema", ToolKind.SEARCHED),
        ToolFinished("web_search"),
    ]


def test_mcp_web_tools_carry_their_kinds(tmp_path: Path) -> None:
    parser = _parser(tmp_path)

    found = parser.feed(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "item_5",
                    "type": "mcp_tool_call",
                    "server": "docs",
                    "tool": "web_search",
                    "arguments": {"q": "exec --json"},
                    "status": "in_progress",
                },
            }
        )
    )
    fetched = parser.feed(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "item_6",
                    "type": "mcp_tool_call",
                    "server": "docs",
                    "tool": "web_fetch",
                    "arguments": {"url": "https://example.com"},
                    "status": "in_progress",
                },
            }
        )
    )

    assert found == [ToolStarted("web_search", "", ToolKind.SEARCHED)]
    assert fetched == [ToolStarted("web_fetch", "", ToolKind.READ)]


def test_mcp_unknown_tool_keeps_its_raw_name_without_a_kind(tmp_path: Path) -> None:
    signals = _parser(tmp_path).feed(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "item_5",
                    "type": "mcp_tool_call",
                    "server": "docs",
                    "tool": "search",
                    "status": "in_progress",
                },
            }
        )
    )

    assert signals == [ToolStarted("search", "")]
