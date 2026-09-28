"""Incremental adapter for the current role's live view-log signals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from shikiru.view_log import (
    VIEW_SESSION_STARTED_SIGNAL,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TOOL_FINISHED_SIGNAL,
    VIEW_TOOL_STARTED_SIGNAL,
    VIEW_TURN_ENDED_SIGNAL,
    VIEW_TURN_FAILED_SIGNAL,
    VIEW_TURN_KEY,
)

if TYPE_CHECKING:
    from pathlib import Path

_LINE_ENDING = b"\n"


@dataclass(frozen=True)
class TurnActivity:
    """The last current-turn signal and tool state read from a role's view log."""

    last_signal_at: datetime
    tool_running: bool
    ended: bool


@dataclass
class _TailState:
    offset: int
    pending: bytes
    turn_name: str
    last_signal_at: datetime | None = None
    tool_running: bool = False
    ended: bool = False


class RoleViewTail:
    """Scan each role log once, then read only newly appended rows."""

    def __init__(self) -> None:
        """Start with no role logs cached."""
        self._states: dict[Path, _TailState] = {}

    def read(self, path: Path, turn_name: str) -> TurnActivity | None:
        """Return signal state for `turn_name`, advancing only the unread file tail."""
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return None
        state = self._states.get(path)
        if state is None or size < state.offset or state.turn_name != turn_name:
            state = self._initial_state(path, turn_name)
        chunk = self._read_new_bytes(path, state)
        self._consume(state, chunk)
        self._states[path] = state
        if state.last_signal_at is None:
            return None
        return TurnActivity(state.last_signal_at, state.tool_running, state.ended)

    @staticmethod
    def _initial_state(path: Path, turn_name: str) -> _TailState:
        state = _TailState(offset=0, pending=b"", turn_name=turn_name)
        with path.open("rb") as handle:
            for line in handle:
                if line.endswith(_LINE_ENDING):
                    RoleViewTail._consume_row(state, json.loads(line))
                else:
                    state.pending = line
            offset = handle.tell()
        state.offset = offset
        return state

    @staticmethod
    def _read_new_bytes(path: Path, state: _TailState) -> bytes:
        with path.open("rb") as handle:
            handle.seek(state.offset)
            chunk = handle.read()
            state.offset = handle.tell()
        return chunk

    @staticmethod
    def _consume(state: _TailState, chunk: bytes) -> None:
        rows = (state.pending + chunk).split(_LINE_ENDING)
        state.pending = rows.pop()
        for line in rows:
            if line:
                RoleViewTail._consume_row(state, json.loads(line))

    @staticmethod
    def _consume_row(state: _TailState, row: object) -> None:
        if not isinstance(row, dict):
            message = "role view log row must be a JSON object"
            raise TypeError(message)
        typed_row = cast("dict[str, object]", row)
        row_turn = typed_row.get(VIEW_TURN_KEY)
        signal = typed_row.get(VIEW_SIGNAL_KEY)
        timestamp = typed_row.get(VIEW_TIME_KEY)
        if (
            not isinstance(row_turn, str)
            or not isinstance(signal, str)
            or not isinstance(timestamp, str)
        ):
            message = "role view log turn, signal and timestamp must be strings"
            raise TypeError(message)
        if row_turn != state.turn_name:
            return
        state.last_signal_at = datetime.fromisoformat(timestamp)
        if signal == VIEW_SESSION_STARTED_SIGNAL:
            state.ended = False
            state.tool_running = False
        if signal == VIEW_TOOL_STARTED_SIGNAL:
            state.tool_running = True
        elif signal == VIEW_TOOL_FINISHED_SIGNAL:
            state.tool_running = False
        state.ended = state.ended or signal in (VIEW_TURN_ENDED_SIGNAL, VIEW_TURN_FAILED_SIGNAL)
