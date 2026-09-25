"""Renders a role's `view.jsonl` rows as terminal lines (docs/design/06-operator.md §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from shikiru.agents.contracts import (
    Compacted,
    Message,
    QuotaReported,
    SessionStarted,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    UsageReported,
)
from shikiru.ui.glyphs import GLYPH_DONE, GLYPH_ESCALATED, GLYPH_PENDING, ROLE_GLYPHS
from shikiru.ui.tokens import ALERT_COLOR, IDLE_COLOR, ROLE_COLORS, STYLE_BOLD, STYLE_DIM
from shikiru.view_log import VIEW_RUNG_KEY, VIEW_SIGNAL_KEY, VIEW_TURN_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shikiru.domain.tasks import Role

type ViewRow = Mapping[str, Any]

_TOOL_GLYPH = "▸"
_RUNG_SEPARATOR = ":"
_EFFORT_SEPARATOR = "/"


class UnknownViewSignalError(ValueError):
    """A `view.jsonl` row names a signal this renderer does not know."""


class StreamLine:
    """Turns view rows into Rich lines, opening each new turn with its step and rung."""

    def __init__(self, role: Role) -> None:
        """Render rows for `role`, whose colour and glyph mark turn boundaries."""
        self._role = role
        self._turn: str | None = None
        self._renderers: dict[str, Callable[[ViewRow], Text | None]] = {
            SessionStarted.__name__: _session_started,
            Message.__name__: _message,
            ToolStarted.__name__: _tool_started,
            ToolFinished.__name__: _silent,
            UsageReported.__name__: _usage,
            QuotaReported.__name__: _silent,
            Compacted.__name__: _compacted,
            TurnEnded.__name__: _turn_ended,
            TurnFailed.__name__: _turn_failed,
        }

    def render(self, row: ViewRow) -> list[Text]:
        """Return the lines `row` adds to the view, headed by a boundary on a new turn."""
        signal = row[VIEW_SIGNAL_KEY]
        renderer = self._renderers.get(signal)
        if renderer is None:
            message = f"unknown view signal {signal!r}"
            raise UnknownViewSignalError(message)
        lines: list[Text] = []
        if row[VIEW_TURN_KEY] != self._turn:
            self._turn = row[VIEW_TURN_KEY]
            lines.append(self._boundary(row))
        line = renderer(row)
        if line is not None:
            lines.append(line)
        return lines

    def _boundary(self, row: ViewRow) -> Text:
        rung = row[VIEW_RUNG_KEY]
        label = _RUNG_SEPARATOR.join((rung["agent"], rung["model"]))
        if rung["effort"]:
            label = f"{label}{_EFFORT_SEPARATOR}{rung['effort']}"
        return Text(
            f"{ROLE_GLYPHS[self._role]} {row[VIEW_TURN_KEY]} · {label}",
            style=f"{STYLE_BOLD} {ROLE_COLORS[self._role]}",
        )


def _silent(_row: ViewRow) -> None:
    return None


def _session_started(row: ViewRow) -> Text:
    return Text(f"{GLYPH_PENDING} session {row['session_id']}", style=STYLE_DIM)


def _message(row: ViewRow) -> Text:
    return Text(row["text"])


def _tool_started(row: ViewRow) -> Text:
    detail = f" {row['detail']}" if row["detail"] else ""
    return Text(f"{_TOOL_GLYPH} {row['name']}{detail}", style=IDLE_COLOR)


def _usage(row: ViewRow) -> Text:
    usage = row["usage"]
    tokens = usage["input_tokens"] + usage["output_tokens"]
    return Text(f"{GLYPH_PENDING} {tokens} tokens", style=STYLE_DIM)


def _compacted(_row: ViewRow) -> Text:
    return Text(f"{GLYPH_PENDING} context compacted", style=STYLE_DIM)


def _turn_ended(_row: ViewRow) -> Text:
    return Text(f"{GLYPH_DONE} turn ended", style=STYLE_DIM)


def _turn_failed(row: ViewRow) -> Text:
    return Text(f"{GLYPH_ESCALATED} {row['kind']}: {row['message']}", style=ALERT_COLOR)
