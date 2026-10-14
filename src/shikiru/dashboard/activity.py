"""Incremental adapter for the current role's live view-log signals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from shikiru.domain.briefing import one_line
from shikiru.view_log import (
    VIEW_MESSAGE_SIGNAL,
    VIEW_MESSAGE_TEXT_KEY,
    VIEW_SESSION_STARTED_SIGNAL,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TOOL_FINISHED_SIGNAL,
    VIEW_TOOL_STARTED_SIGNAL,
    VIEW_TURN_ENDED_SIGNAL,
    VIEW_TURN_ENDED_TEXT_KEY,
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
    last_signal: str
    tool_running: bool
    ended: bool
    last_words: str | None = None


@dataclass
class _TailState:
    offset: int
    pending: bytes
    turn_name: str
    last_signal_at: datetime | None = None
    last_signal: str | None = None
    tool_running: bool = False
    ended: bool = False
    message_words: str | None = None
    ended_words: str | None = None


class RoleViewTail:
    """Scan each role log once, then read only newly appended rows."""

    def __init__(self, words_limit: int) -> None:
        """Start with no role logs cached; keep the last words to `words_limit` characters."""
        self._words_limit = words_limit
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
        if size > state.offset:
            self._consume(state, self._read_new_bytes(path, state))
        self._states[path] = state
        if state.last_signal_at is None:
            return None
        return TurnActivity(
            state.last_signal_at,
            state.last_signal or "",
            state.tool_running,
            state.ended,
            state.message_words or state.ended_words,
        )

    def _initial_state(self, path: Path, turn_name: str) -> _TailState:
        state = _TailState(offset=0, pending=b"", turn_name=turn_name)
        with path.open("rb") as handle:
            while line := handle.readline():
                if line.endswith(_LINE_ENDING):
                    self._consume_row(state, json.loads(line))
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

    def _consume(self, state: _TailState, chunk: bytes) -> None:
        rows = (state.pending + chunk).split(_LINE_ENDING)
        state.pending = rows.pop()
        for line in rows:
            if line:
                self._consume_row(state, json.loads(line))

    def _consume_row(self, state: _TailState, row: object) -> None:
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
        state.last_signal = signal
        if signal == VIEW_MESSAGE_SIGNAL:
            state.message_words = (
                self._words(typed_row, VIEW_MESSAGE_TEXT_KEY) or state.message_words
            )
        elif signal == VIEW_TURN_ENDED_SIGNAL:
            state.ended_words = (
                self._words(typed_row, VIEW_TURN_ENDED_TEXT_KEY) or state.ended_words
            )
        if signal == VIEW_SESSION_STARTED_SIGNAL:
            state.ended = False
            state.tool_running = False
        if signal == VIEW_TOOL_STARTED_SIGNAL:
            state.tool_running = True
        elif signal == VIEW_TOOL_FINISHED_SIGNAL:
            state.tool_running = False
        state.ended = state.ended or signal in (VIEW_TURN_ENDED_SIGNAL, VIEW_TURN_FAILED_SIGNAL)

    def _words(self, row: dict[str, object], key: str) -> str | None:
        text = row.get(key)
        return one_line(text, self._words_limit) if isinstance(text, str) else None
