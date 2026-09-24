"""The fake agent's hook payload format: `{"event": <name>, "session_id": <id>, ...}` lines
(see package docstring).

Builders (`*_payload`) are used by `fake_agent.repl` to write hook lines; readers
(`signal_from_entry`, `usage_from_entries`, `error_from_entry`) turn them back into
`agents.contracts` types, both for `fake_agent.session` and for `AgentContractSuite`'s
offline checks. Every entry carries `session_id`, so `usage_from_entries` can sum only one
session's turns even when several sessions have shared a role directory over time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import (
    Activity,
    AgentSignal,
    AttentionNeeded,
    Compacted,
    FailureKind,
    PromptSubmitted,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from datetime import datetime

    from shikiru.agents.sdk.reset_time import ResetTimeParser

EVENT_FIELD = "event"
SESSION_ID_FIELD = "session_id"
EVENT_SESSION_STARTED = "session_started"
EVENT_PROMPT_SUBMITTED = "prompt_submitted"
EVENT_ACTIVITY = "activity"
EVENT_TURN_ENDED = "turn_ended"
EVENT_TURN_FAILED = "turn_failed"
EVENT_COMPACTED = "compacted"
EVENT_ATTENTION_NEEDED = "attention_needed"
EVENT_SESSION_ENDED = "session_ended"

INPUT_TOKENS_PER_TURN = 120
OUTPUT_TOKENS_PER_TURN = 60
CACHE_READ_TOKENS_PER_TURN = 20
CACHE_WRITE_TOKENS_PER_TURN = 10


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """One turn's token counts, as recorded on a `turn_ended` hook entry."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def turn_tokens() -> TokenUsage:
    """Return the fixed token counts every fake turn reports."""
    return TokenUsage(
        input_tokens=INPUT_TOKENS_PER_TURN,
        output_tokens=OUTPUT_TOKENS_PER_TURN,
        cache_read_tokens=CACHE_READ_TOKENS_PER_TURN,
        cache_write_tokens=CACHE_WRITE_TOKENS_PER_TURN,
    )


def session_started_payload(session_id: str) -> dict[str, Any]:
    """Build a `session_started` hook entry."""
    return {EVENT_FIELD: EVENT_SESSION_STARTED, SESSION_ID_FIELD: session_id}


def prompt_submitted_payload(session_id: str, text: str) -> dict[str, Any]:
    """Build a `prompt_submitted` hook entry."""
    return {EVENT_FIELD: EVENT_PROMPT_SUBMITTED, SESSION_ID_FIELD: session_id, "text": text}


def activity_payload(session_id: str, *, tool_running: bool) -> dict[str, Any]:
    """Build an `activity` hook entry."""
    return {
        EVENT_FIELD: EVENT_ACTIVITY,
        SESSION_ID_FIELD: session_id,
        "tool_running": tool_running,
    }


def turn_ended_payload(session_id: str, last_message: str, *, tokens: TokenUsage) -> dict[str, Any]:
    """Build a `turn_ended` hook entry, carrying this turn's token counts."""
    return {
        EVENT_FIELD: EVENT_TURN_ENDED,
        SESSION_ID_FIELD: session_id,
        "last_message": last_message,
        "tokens": asdict(tokens),
    }


def turn_failed_payload(
    session_id: str, kind: FailureKind, *, message: str, resets_at: str | None
) -> dict[str, Any]:
    """Build a `turn_failed` hook entry; `resets_at` is an ISO-8601 string or `None`."""
    return {
        EVENT_FIELD: EVENT_TURN_FAILED,
        SESSION_ID_FIELD: session_id,
        "kind": kind.value,
        "message": message,
        "resets_at": resets_at,
    }


def compacted_payload(session_id: str) -> dict[str, Any]:
    """Build a `compacted` hook entry."""
    return {EVENT_FIELD: EVENT_COMPACTED, SESSION_ID_FIELD: session_id}


def attention_needed_payload(session_id: str, message: str) -> dict[str, Any]:
    """Build an `attention_needed` hook entry."""
    return {EVENT_FIELD: EVENT_ATTENTION_NEEDED, SESSION_ID_FIELD: session_id, "message": message}


def session_ended_payload(session_id: str) -> dict[str, Any]:
    """Build a `session_ended` hook entry."""
    return {EVENT_FIELD: EVENT_SESSION_ENDED, SESSION_ID_FIELD: session_id}


def _session_started(entry: Mapping[str, Any]) -> AgentSignal:
    return SessionStarted(session_id=entry[SESSION_ID_FIELD])


def _prompt_submitted(entry: Mapping[str, Any]) -> AgentSignal:
    return PromptSubmitted(text=entry["text"])


def _activity(entry: Mapping[str, Any]) -> AgentSignal:
    return Activity(tool_running=entry["tool_running"])


def _turn_ended(entry: Mapping[str, Any]) -> AgentSignal:
    return TurnEnded(last_message=entry["last_message"])


def _compacted(_entry: Mapping[str, Any]) -> AgentSignal:
    return Compacted()


def _attention_needed(entry: Mapping[str, Any]) -> AgentSignal:
    return AttentionNeeded(message=entry["message"])


def _session_ended(_entry: Mapping[str, Any]) -> AgentSignal:
    return SessionEnded()


_SIMPLE_SIGNAL_BUILDERS: dict[str, Callable[[Mapping[str, Any]], AgentSignal]] = {
    EVENT_SESSION_STARTED: _session_started,
    EVENT_PROMPT_SUBMITTED: _prompt_submitted,
    EVENT_ACTIVITY: _activity,
    EVENT_TURN_ENDED: _turn_ended,
    EVENT_COMPACTED: _compacted,
    EVENT_ATTENTION_NEEDED: _attention_needed,
    EVENT_SESSION_ENDED: _session_ended,
}


def signal_from_entry(entry: Mapping[str, Any], reset_time: ResetTimeParser) -> AgentSignal:
    """Map one hook entry back to its `AgentSignal`, raising on an unknown event."""
    event = entry[EVENT_FIELD]
    if event == EVENT_TURN_FAILED:
        kind, resets_at = error_from_entry(entry, reset_time)
        return TurnFailed(kind=kind, message=entry["message"], resets_at=resets_at)
    builder = _SIMPLE_SIGNAL_BUILDERS.get(event)
    if builder is None:
        message = f"unknown fake agent hook event {event!r}"
        raise ValueError(message)
    return builder(entry)


def error_from_entry(
    entry: Mapping[str, Any], reset_time: ResetTimeParser
) -> tuple[FailureKind, datetime | None]:
    """Map one `turn_failed` hook entry to its `FailureKind` and parsed `resets_at`."""
    kind = FailureKind(entry["kind"])
    raw_resets_at = entry.get("resets_at")
    resets_at = reset_time.parse(raw_resets_at) if raw_resets_at else None
    return kind, resets_at


def usage_from_entries(entries: Iterable[Mapping[str, Any]], *, session_id: str) -> Usage:
    """Sum `session_id`'s `turn_ended` token counts; the fake agent reports no cost or context."""
    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    for entry in entries:
        if entry.get(EVENT_FIELD) != EVENT_TURN_ENDED or entry.get(SESSION_ID_FIELD) != session_id:
            continue
        tokens = entry["tokens"]
        input_tokens += tokens["input_tokens"]
        output_tokens += tokens["output_tokens"]
        cache_read_tokens += tokens["cache_read_tokens"]
        cache_write_tokens += tokens["cache_write_tokens"]
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=None,
        cost_basis="unknown",
        context_used_pct=None,
    )
