"""Bounded reads of recorded normalized signal files for task evidence pages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from shikiru.domain.tasks import Role
from shikiru.domain.timeline import (
    MISSING_SIGNAL_SUMMARY,
    TIMELINE_ACTIVITY_CATEGORY,
    TIMELINE_ALL_CATEGORY,
    TIMELINE_DECISION_CATEGORY,
    TIMELINE_ENGINE_CATEGORY,
    TIMELINE_EVIDENCE_CATEGORY,
    TIMELINE_MESSAGE_CATEGORY,
    TIMELINE_SIGNAL_CATEGORY,
    TIMELINE_SOURCE_AGENT,
    TIMELINE_TOOL_CATEGORY,
    TIMELINE_TRANSITION_CATEGORY,
    TIMELINE_USAGE_CATEGORY,
    ParsedSignal,
    parse_signal_row,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

CURSOR_VERSION = 1
SIGNAL_LINE_BLOCK_BYTES = 65536
CURSOR_POSITION_FIELDS = 2
TIMELINE_CURSOR_ERROR = "task evidence cursor is invalid"
TIMELINE_CURSOR_FILTER_ERROR = "task evidence cursor belongs to different filters"
TIMELINE_MISSING_DETAIL = "No recorded normalized signal file is available for this role."


@dataclass(frozen=True)
class TimelineFilters:
    """Filters applied consistently to engine events and normalized signal evidence."""

    role: str | None
    turn: str | None
    category: str


@dataclass(frozen=True)
class TimelineCursor:
    """Positions immediately after the newest rows already shown from each source."""

    event_before: tuple[str, int] | None
    signal_before: Mapping[str, int | None]


@dataclass(frozen=True)
class TimelineItem:
    """One evidence row with stable identity and its source ordering key."""

    identity: str
    timestamp: datetime | None
    source: str
    category: str
    summary: str
    detail: str
    role: str | None
    turn: str | None
    event_id: int | None = None
    signal_offset: int | None = None
    signal_end_offset: int | None = None
    incomplete: bool = False
    timestamp_error: str | None = None


@dataclass(frozen=True)
class SignalBatch:
    """A bounded signal page and a position that safely replays its first row."""

    items: tuple[TimelineItem, ...]
    replay_offset: int | None
    scan_offset: int
    has_more: bool = False


def encode_cursor(cursor: TimelineCursor, filters: TimelineFilters) -> str:
    """Serialize a source-position cursor with the filters that define its ordering."""
    encoded = {
        "version": CURSOR_VERSION,
        "event_before": list(cursor.event_before) if cursor.event_before is not None else None,
        "signal_before": dict(cursor.signal_before),
        "filters": {"role": filters.role, "turn": filters.turn, "category": filters.category},
    }
    return json.dumps(encoded, separators=(",", ":"), sort_keys=True)


def decode_cursor(
    token: str,
    *,
    filters: TimelineFilters,
    roles: Sequence[str],
) -> TimelineCursor:
    """Validate an opaque cursor before continuing a filtered evidence page."""
    data = _cursor_mapping(token)
    expected_filters = {"role": filters.role, "turn": filters.turn, "category": filters.category}
    if data.get("filters") != expected_filters:
        raise ValueError(TIMELINE_CURSOR_FILTER_ERROR)
    signal_before = _signal_positions(data, filters, roles)
    event_before = _event_position(data)
    return TimelineCursor(event_before=event_before, signal_before=signal_before)


def _cursor_mapping(token: str) -> dict[str, object]:
    try:
        raw_data: object = json.loads(token)
    except json.JSONDecodeError as error:
        raise ValueError(TIMELINE_CURSOR_ERROR) from error
    if not isinstance(raw_data, dict):
        raise TypeError(TIMELINE_CURSOR_ERROR)
    untyped_data = cast("dict[object, object]", raw_data)
    if not all(isinstance(key, str) for key in untyped_data):
        raise ValueError(TIMELINE_CURSOR_ERROR)
    data = cast("dict[str, object]", untyped_data)
    if type(data.get("version")) is not int or data.get("version") != CURSOR_VERSION:
        raise ValueError(TIMELINE_CURSOR_ERROR)
    return data


def _signal_positions(
    data: Mapping[str, object], filters: TimelineFilters, roles: Sequence[str]
) -> dict[str, int | None]:
    raw_signal_before = data.get("signal_before")
    valid_roles = {item.value for item in Role}
    if not isinstance(raw_signal_before, dict):
        raise TypeError(TIMELINE_CURSOR_ERROR)
    untyped_positions = cast("dict[object, object]", raw_signal_before)
    if not all(isinstance(role, str) for role in untyped_positions):
        raise TypeError(TIMELINE_CURSOR_ERROR)
    signal_before = cast("dict[str, object]", untyped_positions)
    if not set(signal_before) <= valid_roles:
        raise ValueError(TIMELINE_CURSOR_ERROR)
    if not set(signal_before) <= set(roles):
        raise ValueError(TIMELINE_CURSOR_ERROR)
    if filters.role is not None and set(signal_before) != set(roles):
        raise ValueError(TIMELINE_CURSOR_ERROR)
    positions: dict[str, int | None] = {}
    for role, offset in signal_before.items():
        if offset is not None and (
            not isinstance(offset, int) or isinstance(offset, bool) or offset < -1
        ):
            raise ValueError(TIMELINE_CURSOR_ERROR)
        positions[role] = offset
    return positions


def _event_position(data: Mapping[str, object]) -> tuple[str, int] | None:
    value = data.get("event_before")
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError(TIMELINE_CURSOR_ERROR)
    position = cast("list[object]", value)
    if len(position) != CURSOR_POSITION_FIELDS:
        raise ValueError(TIMELINE_CURSOR_ERROR)
    timestamp_value, event_id = position
    if not isinstance(timestamp_value, str):
        raise TypeError(TIMELINE_CURSOR_ERROR)
    if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
        raise ValueError(TIMELINE_CURSOR_ERROR)
    try:
        timestamp = datetime.fromisoformat(timestamp_value)
    except ValueError as error:
        raise ValueError(TIMELINE_CURSOR_ERROR) from error
    if timestamp.tzinfo is None:
        raise ValueError(TIMELINE_CURSOR_ERROR)
    return timestamp_value, event_id


def initial_cursor(paths: Mapping[str, Path]) -> TimelineCursor:
    """Capture each normalized source's current tail for a stable latest-page read."""
    positions: dict[str, int | None] = {}
    for role, path in paths.items():
        try:
            positions[role] = path.stat().st_size
        except FileNotFoundError:
            positions[role] = None
    return TimelineCursor(event_before=None, signal_before=positions)


def read_signal_batch(
    path: Path,
    *,
    role: str,
    before_offset: int | None,
    filters: TimelineFilters,
    limit: int,
    scan_limit: int,
) -> SignalBatch:
    """Read at most `limit` matching signal rows backwards without retaining the file."""
    if before_offset == -1:
        return SignalBatch(items=(), replay_offset=-1, scan_offset=-1)
    snapshot = _signal_snapshot(path, before_offset)
    if snapshot is None:
        return _missing_batch(role, filters)
    end_position = snapshot
    return _scan_signal_file(
        path,
        role=role,
        end_position=end_position,
        filters=filters,
        limit=limit,
        scan_limit=scan_limit,
    )


def _signal_snapshot(path: Path, before_offset: int | None) -> int | None:
    try:
        file_size = path.stat().st_size
    except FileNotFoundError:
        return None
    end_position = file_size if before_offset is None else before_offset
    if end_position > file_size:
        message = f"recorded signal file changed before its saved cursor: {path}"
        raise ValueError(message)
    return end_position


def _missing_batch(role: str, filters: TimelineFilters) -> SignalBatch:
    missing = _missing_signal(role)
    if _matches_signal(missing, filters):
        return SignalBatch(items=(missing,), replay_offset=None, scan_offset=0)
    return SignalBatch(items=(), replay_offset=0, scan_offset=0)


def _scan_signal_file(
    path: Path,
    *,
    role: str,
    end_position: int,
    filters: TimelineFilters,
    limit: int,
    scan_limit: int,
) -> SignalBatch:
    items: list[TimelineItem] = []
    position = end_position
    scanned = 0
    while position > 0 and len(items) < limit and scanned < scan_limit:
        parsed = _previous_signal(path, role, position)
        if parsed is None:
            position = 0
            break
        position = parsed.start_offset
        scanned += 1
        item = _timeline_item(parsed)
        if _matches_signal(item, filters):
            items.append(item)
    replay_offset = items[0].signal_end_offset if items else position
    scan_offset = position
    return SignalBatch(
        items=tuple(items),
        replay_offset=replay_offset,
        scan_offset=scan_offset,
        has_more=position > 0,
    )


def signal_category_is_requested(category: str) -> bool:
    """Whether an evidence category can contain normalized agent signal rows."""
    return category in {
        TIMELINE_ALL_CATEGORY,
        TIMELINE_SIGNAL_CATEGORY,
        TIMELINE_TOOL_CATEGORY,
        TIMELINE_MESSAGE_CATEGORY,
        TIMELINE_USAGE_CATEGORY,
        TIMELINE_EVIDENCE_CATEGORY,
        TIMELINE_TRANSITION_CATEGORY,
    }


def event_category_is_requested(category: str) -> bool:
    """Whether an evidence category can contain engine event rows."""
    return category in {
        TIMELINE_ALL_CATEGORY,
        TIMELINE_ACTIVITY_CATEGORY,
        TIMELINE_ENGINE_CATEGORY,
        TIMELINE_DECISION_CATEGORY,
        TIMELINE_TRANSITION_CATEGORY,
    }


def _previous_signal(
    path: Path,
    role: str,
    end_position: int,
) -> ParsedSignal | None:
    with path.open("rb") as handle:
        handle.seek(end_position - 1)
        last_byte = handle.read(1)
        incomplete = last_byte != b"\n"
        line_end = end_position - 1 if last_byte == b"\n" else end_position
        if line_end <= 0:
            return None
        search_end = line_end
        line_start = 0
        while search_end > 0:
            block_start = max(search_end - SIGNAL_LINE_BLOCK_BYTES, 0)
            handle.seek(block_start)
            block = handle.read(search_end - block_start)
            delimiter = block.rfind(b"\n")
            if delimiter >= 0:
                line_start = block_start + delimiter + 1
                break
            search_end = block_start
        handle.seek(line_start)
        raw = handle.read(line_end - line_start)
    return parse_signal_row(
        raw,
        role=role,
        start_offset=line_start,
        end_offset=end_position,
        incomplete=incomplete,
    )


def _timeline_item(signal: ParsedSignal) -> TimelineItem:
    timestamp_error = signal.timestamp_error
    if signal.incomplete:
        timestamp_error = "JSONL row has not ended with a newline"
    return TimelineItem(
        identity=f"signal:{signal.role}:{signal.start_offset}",
        timestamp=signal.timestamp,
        source=TIMELINE_SOURCE_AGENT,
        category=signal.category,
        summary=signal.summary,
        detail=signal.detail,
        role=signal.role,
        turn=signal.turn,
        signal_offset=signal.start_offset,
        signal_end_offset=signal.end_offset,
        incomplete=signal.incomplete,
        timestamp_error=timestamp_error,
    )


def _missing_signal(role: str) -> TimelineItem:
    return TimelineItem(
        identity=f"missing:{role}",
        timestamp=None,
        source=TIMELINE_SOURCE_AGENT,
        category=TIMELINE_EVIDENCE_CATEGORY,
        summary=MISSING_SIGNAL_SUMMARY,
        detail=TIMELINE_MISSING_DETAIL,
        role=role,
        turn=None,
        signal_offset=0,
        signal_end_offset=None,
        timestamp_error="recorded timestamp is missing",
    )


def _matches_signal(item: TimelineItem, filters: TimelineFilters) -> bool:
    if filters.role is not None and filters.role != item.role:
        return False
    if filters.turn is not None and filters.turn != item.turn:
        return False
    if filters.category in {TIMELINE_ALL_CATEGORY, TIMELINE_SIGNAL_CATEGORY}:
        return True
    if filters.category in {
        TIMELINE_ACTIVITY_CATEGORY,
        TIMELINE_ENGINE_CATEGORY,
        TIMELINE_DECISION_CATEGORY,
    }:
        return False
    return filters.category == item.category
