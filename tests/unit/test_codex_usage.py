"""Codex rollout usage rules."""

from __future__ import annotations

from shikiru.plugins.agents.codex.usage import rollout_usage


def test_cached_input_is_not_counted_twice_in_context_usage() -> None:
    entries = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "output_tokens": 5,
                    },
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "output_tokens": 5,
                    },
                    "model_context_window": 1000,
                },
            },
        }
    ]

    usage = rollout_usage(entries)

    assert usage.context_used_pct == 10.0
    assert usage.input_tokens == 20
    assert usage.cache_read_tokens == 80


def test_cache_writes_are_excluded_from_uncached_input() -> None:
    entries = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 50,
                        "cache_write_input_tokens": 30,
                        "output_tokens": 5,
                    },
                    "last_token_usage": {"input_tokens": 100},
                },
            },
        }
    ]
    usage = rollout_usage(entries)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (20, 50, 30)
