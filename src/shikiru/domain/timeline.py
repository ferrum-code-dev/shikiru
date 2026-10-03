"""Pure parsing and classification for the engine's normalized task evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

TIMELINE_ACTIVITY_CATEGORY = "activity"
TIMELINE_ALL_CATEGORY = "all"
TIMELINE_ENGINE_CATEGORY = "engine"
TIMELINE_SIGNAL_CATEGORY = "signal"
TIMELINE_DECISION_CATEGORY = "decision"
TIMELINE_TRANSITION_CATEGORY = "transition"
TIMELINE_TOOL_CATEGORY = "tool"
TIMELINE_MESSAGE_CATEGORY = "message"
TIMELINE_USAGE_CATEGORY = "usage"
TIMELINE_EVIDENCE_CATEGORY = "evidence"
TIMELINE_DEFAULT_CATEGORY = TIMELINE_ACTIVITY_CATEGORY
TIMELINE_CATEGORIES = frozenset(
    {
        TIMELINE_ACTIVITY_CATEGORY,
        TIMELINE_ALL_CATEGORY,
        TIMELINE_ENGINE_CATEGORY,
        TIMELINE_SIGNAL_CATEGORY,
        TIMELINE_DECISION_CATEGORY,
        TIMELINE_TRANSITION_CATEGORY,
        TIMELINE_TOOL_CATEGORY,
        TIMELINE_MESSAGE_CATEGORY,
        TIMELINE_USAGE_CATEGORY,
        TIMELINE_EVIDENCE_CATEGORY,
    }
)
SIGNAL_TOOL_NAMES = frozenset({"ToolStarted", "ToolFinished"})
SIGNAL_MESSAGE_NAMES = frozenset({"Message"})
SIGNAL_USAGE_NAMES = frozenset({"UsageReported", "QuotaReported"})
SIGNAL_LIFECYCLE_NAMES = frozenset({"SessionStarted", "TurnEnded", "TurnFailed", "Compacted"})
TIMELINE_DECISION_KINDS = frozenset(
    {
        "verdict",
        "scope_change",
        "escalation",
        "escalation_resolved",
        "task_done",
        "task_cancelled",
        "pr_opened",
        "pr_withdrawn",
    }
)
TIMELINE_SOURCE_ENGINE = "engine"
TIMELINE_SOURCE_AGENT = "agent"
INCOMPLETE_SIGNAL_SUMMARY = "Incomplete live JSONL row"
MALFORMED_SIGNAL_SUMMARY = "Malformed normalized signal evidence"
MISSING_SIGNAL_SUMMARY = "Normalized signal evidence is unavailable"
MISSING_TIMESTAMP = "recorded timestamp is missing"
TIMELINE_JSON_INDENT = 2


@dataclass(frozen=True)
class ParsedSignal:
    """One parsed normalized signal row and its recorded evidence identity."""

    role: str
    turn: str | None
    signal: str | None
    category: str
    timestamp: datetime | None
    timestamp_error: str | None
    summary: str
    detail: str
    start_offset: int
    end_offset: int
    incomplete: bool


def parse_signal_row(
    raw: bytes,
    *,
    role: str,
    start_offset: int,
    end_offset: int,
    incomplete: bool,
) -> ParsedSignal:
    """Parse one normalized `view.jsonl` row without interpreting provider formats."""
    text = raw.decode("utf-8", errors="replace")
    try:
        raw_data: object = json.loads(text)
    except json.JSONDecodeError:
        return ParsedSignal(
            role=role,
            turn=None,
            signal=None,
            category=TIMELINE_EVIDENCE_CATEGORY,
            timestamp=None,
            timestamp_error=MISSING_TIMESTAMP,
            summary=INCOMPLETE_SIGNAL_SUMMARY if incomplete else MALFORMED_SIGNAL_SUMMARY,
            detail=text,
            start_offset=start_offset,
            end_offset=end_offset,
            incomplete=incomplete,
        )
    if not isinstance(raw_data, dict):
        return ParsedSignal(
            role=role,
            turn=None,
            signal=None,
            category=TIMELINE_EVIDENCE_CATEGORY,
            timestamp=None,
            timestamp_error=MISSING_TIMESTAMP,
            summary=MALFORMED_SIGNAL_SUMMARY,
            detail=text,
            start_offset=start_offset,
            end_offset=end_offset,
            incomplete=incomplete,
        )
    data = cast("dict[str, object]", raw_data)
    turn = data.get("turn")
    signal = data.get("signal")
    timestamp, timestamp_error = _timestamp(data.get("ts"))
    signal_name = signal if isinstance(signal, str) else None
    return ParsedSignal(
        role=role,
        turn=turn if isinstance(turn, str) else None,
        signal=signal_name,
        category=signal_category(signal_name),
        timestamp=timestamp,
        timestamp_error=timestamp_error,
        summary=signal_name or MISSING_SIGNAL_SUMMARY,
        detail=json.dumps(data, ensure_ascii=False, sort_keys=True, indent=TIMELINE_JSON_INDENT),
        start_offset=start_offset,
        end_offset=end_offset,
        incomplete=incomplete,
    )


def signal_category(signal: str | None) -> str:
    """Classify a normalized signal name for evidence filtering."""
    if signal is None:
        return TIMELINE_EVIDENCE_CATEGORY
    if signal in SIGNAL_TOOL_NAMES:
        return TIMELINE_TOOL_CATEGORY
    if signal in SIGNAL_MESSAGE_NAMES:
        return TIMELINE_MESSAGE_CATEGORY
    if signal in SIGNAL_USAGE_NAMES:
        return TIMELINE_USAGE_CATEGORY
    if signal in SIGNAL_LIFECYCLE_NAMES:
        return TIMELINE_TRANSITION_CATEGORY
    return TIMELINE_SIGNAL_CATEGORY


def event_category(kind: str) -> str:
    """Return the operator-facing category for one persisted engine event."""
    return (
        TIMELINE_DECISION_CATEGORY
        if kind in TIMELINE_DECISION_KINDS
        else TIMELINE_TRANSITION_CATEGORY
    )


def _timestamp(value: object) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str):
        return None, MISSING_TIMESTAMP
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, "recorded timestamp is invalid"
    if parsed.tzinfo is None:
        return None, "recorded timestamp has no UTC offset"
    return parsed.astimezone(UTC), None
