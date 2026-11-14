"""Codex rollout usage rules."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("total", "uncached", "cache_write"),
    [
        ({"cache_write_input_tokens": 0}, 20, 0),
        ({"cache_write_input_tokens": 7}, None, 7),
        ({}, None, None),
        ({"cache_write_input_tokens": 0, "cached_input_tokens": 120}, None, 0),
    ],
    ids=("zero-writes", "non-zero-writes", "writes-unreported", "reads-exceed-input"),
)
def test_uncached_input_is_known_only_when_cache_writes_are_known_zero(
    total: dict[str, int], uncached: int | None, cache_write: int | None
) -> None:
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
                        **total,
                    },
                    "last_token_usage": {"input_tokens": 100},
                    "model_context_window": 1000,
                },
            },
        }
    ]

    usage = rollout_usage(entries)

    assert (usage.uncached_input_tokens, usage.cache_write_tokens) == (uncached, cache_write)
