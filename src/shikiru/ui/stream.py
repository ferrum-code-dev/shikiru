"""Renders a role's `view.jsonl` rows as terminal lines."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from shikiru.agents.contracts import (
    Compacted,
    EvidenceFault,
    EvidenceRecovered,
    Message,
    QuotaReported,
    SessionStarted,
    ToolDetailReported,
    ToolFinished,
    ToolOutcome,
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
_DETAIL_SEPARATOR = " · "
_OUTCOME_FIELD = "outcome"
_DETAIL_FIELD = "detail"
_EXIT_CODE_FIELD = "exit_code"
_EXIT_LABEL = "exit"


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
            ToolDetailReported.__name__: _tool_detail_reported,
            ToolFinished.__name__: _tool_finished,
            UsageReported.__name__: _usage,
            QuotaReported.__name__: _silent,
            Compacted.__name__: _compacted,
            EvidenceFault.__name__: _evidence_fault,
            EvidenceRecovered.__name__: _silent,
            TurnEnded.__name__: _turn_ended,
            TurnFailed.__name__: _turn_failed,
        }

    def reset(self) -> None:
        """Reset turn-boundary tracking before replaying a view log from its start."""
        self._turn = None

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


def _tool_detail_reported(row: ViewRow) -> Text:
    name = row.get("name")
    label = f"{name}: " if name else ""
    return Text(f"  {label}{row['detail']}", style=IDLE_COLOR)


def _tool_finished(row: ViewRow) -> Text | None:
    outcome = row.get(_OUTCOME_FIELD)
    exit_code = row.get(_EXIT_CODE_FIELD)
    detail = row.get(_DETAIL_FIELD) or ""
    if (
        outcome not in (ToolOutcome.SUCCESS.value, ToolOutcome.FAILURE.value)
        and type(exit_code) is not int
        and not detail
    ):
        return None
    head = " ".join(part for part in (row["name"], detail) if part)
    details = [head]
    if outcome in (ToolOutcome.SUCCESS.value, ToolOutcome.FAILURE.value):
        details.append(outcome)
    if type(exit_code) is int:
        details.append(f"{_EXIT_LABEL} {exit_code}")
    style = _tool_outcome_style(outcome)
    return Text(f"{_TOOL_GLYPH} {_DETAIL_SEPARATOR.join(details)}", style=style)


def _tool_outcome_style(outcome: object) -> str:
    if outcome == ToolOutcome.FAILURE.value:
        return ALERT_COLOR
    if outcome == ToolOutcome.SUCCESS.value:
        return IDLE_COLOR
    return STYLE_DIM


def _usage(row: ViewRow) -> Text:
    usage = row["usage"]
    tokens = (
        usage["input_tokens"]
        + usage["output_tokens"]
        + usage["cache_read_tokens"]
        + usage["cache_write_tokens"]
    )
    return Text(f"{GLYPH_PENDING} {tokens} tokens", style=STYLE_DIM)


def _compacted(_row: ViewRow) -> Text:
    return Text(f"{GLYPH_PENDING} context compacted", style=STYLE_DIM)


def _evidence_fault(row: ViewRow) -> Text:
    return Text(f"{GLYPH_ESCALATED} {row['message']}", style=ALERT_COLOR)


def _turn_ended(_row: ViewRow) -> Text:
    return Text(f"{GLYPH_DONE} turn ended", style=STYLE_DIM)


def _turn_failed(row: ViewRow) -> Text:
    return Text(f"{GLYPH_ESCALATED} {row['kind']}: {row['message']}", style=ALERT_COLOR)
