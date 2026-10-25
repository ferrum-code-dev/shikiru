"""Claude's context window and next-request token estimate used for compaction decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

CONTEXT_WINDOW_TOKENS = 200_000
EXTENDED_CONTEXT_WINDOW_TOKENS = 1_000_000
EXTENDED_CONTEXT_MODEL_SUFFIX = "[1m]"
CHARS_PER_TOKEN = 3
_PERCENT = 100
_TYPE_FIELD = "type"
_ASSISTANT_TYPE = "assistant"
_USER_TYPE = "user"
_TOOL_RESULT_TYPE = "tool_result"
_MESSAGE_FIELD = "message"
_ID_FIELD = "id"
_USAGE_FIELD = "usage"
_CONTENT_FIELD = "content"
_CONTEXT_USAGE_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


def compact_limit_tokens(model: str, compact_at_pct: float) -> int:
    """Return the tokens at which a request on `model` must be preceded by compaction."""
    window = (
        EXTENDED_CONTEXT_WINDOW_TOKENS
        if model.endswith(EXTENDED_CONTEXT_MODEL_SUFFIX)
        else CONTEXT_WINDOW_TOKENS
    )
    return int(window * compact_at_pct / _PERCENT)


def pending_tokens(value: object) -> int:
    """Estimate the tokens a tool result adds to the next request."""
    return len(json.dumps(value)) // CHARS_PER_TOKEN


def read_transcript(path: Path) -> list[Mapping[str, Any]]:
    """Return the JSON object entries of a Claude session transcript."""
    entries: list[Mapping[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if isinstance(raw, Mapping):
            entries.append(cast("Mapping[str, Any]", raw))
    return entries


def last_assistant_context(entries: Iterable[Mapping[str, Any]]) -> tuple[str | None, int]:
    """Return the id of the last assistant message and the context its request used."""
    message_id: str | None = None
    tokens = 0
    for entry in entries:
        message = _assistant_message(entry)
        if message is not None:
            raw_id = message.get(_ID_FIELD)
            message_id = raw_id if isinstance(raw_id, str) else None
            tokens = _usage_tokens(message.get(_USAGE_FIELD))
    return message_id, tokens


def transcript_context_tokens(entries: Iterable[Mapping[str, Any]]) -> int:
    """Estimate the next request: the last assistant context plus the tool results since."""
    tokens = 0
    pending = 0
    for entry in entries:
        message = _assistant_message(entry)
        if message is not None:
            tokens = _usage_tokens(message.get(_USAGE_FIELD))
            pending = 0
        else:
            pending += sum(pending_tokens(result) for result in _tool_results(entry))
    return tokens + pending


def _assistant_message(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if entry.get(_TYPE_FIELD) != _ASSISTANT_TYPE:
        return None
    return _mapping(entry.get(_MESSAGE_FIELD))


def _tool_results(entry: Mapping[str, Any]) -> list[object]:
    message = _mapping(entry.get(_MESSAGE_FIELD)) if entry.get(_TYPE_FIELD) == _USER_TYPE else None
    blocks = message.get(_CONTENT_FIELD) if message is not None else None
    if not isinstance(blocks, list):
        return []
    return [
        block.get(_CONTENT_FIELD)
        for item in cast("list[object]", blocks)
        if (block := _mapping(item)) is not None and block.get(_TYPE_FIELD) == _TOOL_RESULT_TYPE
    ]


def _usage_tokens(value: object) -> int:
    usage = _mapping(value)
    if usage is None:
        return 0
    return sum(
        int(amount)
        for field in _CONTEXT_USAGE_FIELDS
        if isinstance(amount := usage.get(field), int | float)
    )


def _mapping(value: object) -> Mapping[str, Any] | None:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None
