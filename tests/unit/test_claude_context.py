from __future__ import annotations

from typing import Any

import pytest

from shikiru.plugins.agents.claude.context import (
    MalformedCompactBoundaryError,
    compact_limit_tokens,
    transcript_context_tokens,
)


@pytest.mark.parametrize(
    ("model", "compact_at_pct", "expected"),
    [
        ("haiku", 80, 160_000),
        ("claude-sonnet-4-5", 25, 50_000),
        ("sonnet[1m]", 80, 800_000),
    ],
)
def test_the_limit_is_the_threshold_share_of_the_window_claude_assumes(
    model: str, compact_at_pct: float, expected: int
) -> None:
    assert compact_limit_tokens(model, compact_at_pct) == expected


def _assistant(message_id: str, **usage: int) -> dict[str, Any]:
    return {"type": "assistant", "message": {"id": message_id, "usage": usage}}


def _tool_result(content: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": content}]},
    }


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], 0),
        (
            [
                _assistant(
                    "m1",
                    input_tokens=10,
                    cache_read_input_tokens=200,
                    cache_creation_input_tokens=30,
                    output_tokens=4,
                )
            ],
            244,
        ),
        ([_assistant("m1", input_tokens=100), _tool_result("x" * 298)], 200),
        (
            [
                _assistant("m1", input_tokens=100),
                _tool_result("x" * 298),
                _assistant("m2", input_tokens=50),
            ],
            50,
        ),
    ],
    ids=["empty", "all-usage-fields", "pending-tool-result", "result-already-sent"],
)
def test_the_estimate_is_the_last_request_plus_tool_results_since(
    entries: list[dict[str, Any]], expected: int
) -> None:
    assert transcript_context_tokens(entries) == expected


def _compact_boundary(metadata: dict[str, Any] | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "system", "subtype": "compact_boundary"}
    if metadata is not None:
        entry["compactMetadata"] = metadata
    return entry


def _compact_summary() -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": "summary of the conversation"},
        "isCompactSummary": True,
    }


_BEFORE_COMPACTION = _assistant("m1", input_tokens=158_000)
_COMPACTED = _compact_boundary({"trigger": "manual", "preTokens": 160_499, "postTokens": 10_000})


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([_BEFORE_COMPACTION, _tool_result("x" * 298)], 158_100),
        ([_BEFORE_COMPACTION, _COMPACTED, _compact_summary()], 10_000),
        (
            [
                _BEFORE_COMPACTION,
                _COMPACTED,
                _compact_summary(),
                _assistant("m2", input_tokens=12_000),
            ],
            12_000,
        ),
        (
            [_BEFORE_COMPACTION, _COMPACTED, _compact_summary(), _tool_result("x" * 298)],
            10_100,
        ),
        (
            [_BEFORE_COMPACTION, _tool_result("x" * 298), _COMPACTED, _compact_summary()],
            10_000,
        ),
    ],
    ids=[
        "no-compaction",
        "compaction-then-nothing",
        "compaction-then-assistant",
        "compaction-then-tool-results",
        "tool-results-before-compaction-are-absorbed",
    ],
)
def test_a_compaction_restarts_the_estimate_from_its_post_compaction_size(
    entries: list[dict[str, Any]], expected: int
) -> None:
    assert transcript_context_tokens(entries) == expected


@pytest.mark.parametrize(
    "boundary",
    [
        _compact_boundary(None),
        _compact_boundary({"trigger": "manual", "preTokens": 160_499}),
        _compact_boundary({"trigger": "manual", "postTokens": -1}),
        _compact_boundary({"trigger": "manual", "postTokens": "10465"}),
    ],
    ids=["no-metadata", "no-post-tokens", "negative", "text"],
)
def test_a_compaction_without_a_usable_post_token_count_fails_loudly(
    boundary: dict[str, Any],
) -> None:
    with pytest.raises(MalformedCompactBoundaryError):
        transcript_context_tokens([_BEFORE_COMPACTION, boundary])
