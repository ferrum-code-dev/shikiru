"""Human one-line phrases for recorded engine events and normalized agent signals."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from shikiru.agents.contracts import (
    Compacted,
    Message,
    QuotaReported,
    SessionStarted,
    ToolFinished,
    ToolOutcome,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    UsageReported,
)
from shikiru.domain.events import EventKind

if TYPE_CHECKING:
    from shikiru.domain.events import Event

GLYPH_OK = "\N{CHECK MARK}"
GLYPH_FAIL = "\N{BALLOT X}"
GLYPH_ATTENTION = "!"
GLYPH_ALARM = "\N{DOUBLE EXCLAMATION MARK}\N{VARIATION SELECTOR-15}"
NO_GLYPH = ""
PHRASE_SEPARATOR = " · "
SIGNAL_MESSAGE = Message.__name__
SIGNAL_TOOL_STARTED = ToolStarted.__name__
SIGNAL_TOOL_FINISHED = ToolFinished.__name__
SIGNAL_TURN_ENDED = TurnEnded.__name__
SIGNAL_TURN_FAILED = TurnFailed.__name__
_BOOKKEEPING = frozenset(
    {
        SessionStarted.__name__,
        UsageReported.__name__,
        QuotaReported.__name__,
        Compacted.__name__,
    }
)
_WORD_SEPARATOR = " "
_QUOTE = '"'
_ROUND_WORD = "round"
_LIST_SEPARATOR = ", "
_EXIT_LABEL = "exit"
_SUCCESS_EXIT_CODE = 0
_TURN_ENDED_LABEL = "turn ended"
_TASK_ID_SEPARATOR = " "
_DETAIL_FIELDS = ("command", "file_path", "path", "pattern", "url", "query")
_TOOL_VERBS = {
    "Bash": "ran",
    "command": "ran",
    "Edit": "edited",
    "MultiEdit": "edited",
    "Write": "edited",
    "NotebookEdit": "edited",
    "file_change": "edited",
    "Read": "read",
    "Grep": "searched",
    "Glob": "searched",
}


class Tone(StrEnum):
    """How strongly a phrase should stand out."""

    PLAIN = "plain"
    SUCCESS = "success"
    FAILURE = "failure"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Phrase:
    """A one-line human description with its status glyph and tone."""

    text: str
    glyph: str = NO_GLYPH
    tone: Tone = Tone.PLAIN


type Detail = Callable[[Event], str]


@dataclass(frozen=True)
class _EventSpec:
    label: str
    glyph: str = NO_GLYPH
    tone: Tone = Tone.PLAIN
    detail: Detail | None = None


def _field(key: str) -> Detail:
    def read(event: Event) -> str:
        value = event.data.get(key)
        return "" if value is None else str(value)

    return read


def _message(event: Event) -> str:
    prefix = f"{event.task_id}{_TASK_ID_SEPARATOR}" if event.task_id else ""
    return event.message.removeprefix(prefix)


def _step_round(event: Event) -> str:
    parts = [str(event.data.get("step", ""))]
    iteration = event.data.get("iteration")
    if isinstance(iteration, int) and iteration >= 1:
        parts.append(f"{_ROUND_WORD} {iteration}")
    return _LIST_SEPARATOR.join(part for part in parts if part)


def _rung_change(event: Event) -> str:
    moved = f"{event.data.get('from')} to {event.data.get('to')}"
    return PHRASE_SEPARATOR.join(part for part in (_field("role")(event), moved) if part)


def _new_tasks(event: Event) -> str:
    count = len(event.data.get("new_task_ids") or ())
    return f"{count} new" if count else ""


_VERDICTS = {
    "accept": Phrase("accepted", GLYPH_OK, Tone.SUCCESS),
    "changes_requested": Phrase("changes requested", GLYPH_FAIL, Tone.FAILURE),
    "escalate": Phrase("escalate", GLYPH_ATTENTION, Tone.WARNING),
    "supersede": Phrase("superseded"),
}
_EVENTS: dict[str, _EventSpec] = {
    EventKind.TASK_QUEUED: _EventSpec("task queued"),
    EventKind.TASK_STARTED: _EventSpec("task started", detail=_field("slot")),
    EventKind.DISPATCH_FAILED: _EventSpec(
        "dispatch failed", GLYPH_FAIL, Tone.FAILURE, _field("error")
    ),
    EventKind.STEP_STARTED: _EventSpec("step started", detail=_step_round),
    EventKind.TURN_STARTED: _EventSpec("turn started", detail=_field("role")),
    EventKind.TURN_COMPLETED: _EventSpec("turn completed", GLYPH_OK, Tone.SUCCESS, _field("role")),
    EventKind.TURN_FAILED: _EventSpec("turn failed", GLYPH_FAIL, Tone.FAILURE, _message),
    EventKind.TURN_STOPPED: _EventSpec("turn stopped", detail=_field("role")),
    EventKind.NUDGE: _EventSpec("nudge sent", GLYPH_ATTENTION, Tone.WARNING, _field("role")),
    EventKind.HELPER_ACTION: _EventSpec("helper", GLYPH_ATTENTION, Tone.WARNING, _message),
    EventKind.RUNG_CHANGED: _EventSpec("rung changed", GLYPH_ATTENTION, Tone.WARNING, _rung_change),
    EventKind.COMPACTED: _EventSpec("context compacted", detail=_field("role")),
    EventKind.QUOTA_BLOCKED: _EventSpec(
        "quota blocked", GLYPH_ATTENTION, Tone.WARNING, _field("agent")
    ),
    EventKind.QUOTA_CLEARED: _EventSpec("quota cleared", detail=_field("agent")),
    EventKind.SCOPE_CHANGE: _EventSpec("scope changed", detail=_new_tasks),
    EventKind.HOLD_SET: _EventSpec("hold set", detail=_field("hold")),
    EventKind.HOLD_CLEARED: _EventSpec("hold cleared", detail=_field("hold")),
    EventKind.TAKEN_OVER: _EventSpec("taken over", detail=_field("role")),
    EventKind.HANDED_BACK: _EventSpec("handed back", detail=_field("role")),
    EventKind.TAKEOVER_LOST: _EventSpec(
        "takeover lost", GLYPH_ATTENTION, Tone.WARNING, _field("role")
    ),
    EventKind.OPERATOR_MESSAGE: _EventSpec("operator message", detail=_field("role")),
    EventKind.ESCALATION: _EventSpec("escalated", GLYPH_ALARM, Tone.CRITICAL, _message),
    EventKind.ESCALATION_RESOLVED: _EventSpec(
        "escalation resolved", GLYPH_OK, Tone.SUCCESS, _field("resolution")
    ),
    EventKind.INTEGRATION_STARTED: _EventSpec("integration started"),
    EventKind.INTEGRATION_WAITING: _EventSpec(
        "integration waiting", GLYPH_ATTENTION, Tone.WARNING, _message
    ),
    EventKind.PR_OPENED: _EventSpec("PR opened", GLYPH_OK, Tone.SUCCESS, _field("url")),
    EventKind.PR_WITHDRAWN: _EventSpec("PR withdrawn", detail=_field("url")),
    EventKind.TASK_DONE: _EventSpec("task done", GLYPH_OK, Tone.SUCCESS, _message),
    EventKind.TASK_RETRIED: _EventSpec("task retried"),
    EventKind.TASK_CANCELLED: _EventSpec("task cancelled", GLYPH_FAIL, Tone.FAILURE),
    EventKind.PANE_RESTORED: _EventSpec("pane restored", detail=_field("pane")),
    EventKind.ENGINE_FAULT: _EventSpec("engine fault", GLYPH_FAIL, Tone.FAILURE, _message),
}


def _joined(label: str, detail: str) -> str:
    return f"{label}{PHRASE_SEPARATOR}{detail}" if detail else label


def event_phrase(event: Event) -> Phrase:
    """Describe one engine event in a human phrase; an unlisted kind shows its recorded message."""
    if event.kind is EventKind.VERDICT:
        verdict = _VERDICTS.get(str(event.data.get("decision")))
        if verdict is None:
            return Phrase(_joined("verdict", event.message))
        return Phrase(_joined("verdict", verdict.text), verdict.glyph, verdict.tone)
    spec = _EVENTS.get(event.kind)
    if spec is None:
        return Phrase(event.message)
    detail = spec.detail(event) if spec.detail is not None else ""
    return Phrase(_joined(spec.label, detail), spec.glyph, spec.tone)


def _tool_target(detail: str) -> str:
    try:
        decoded: object = json.loads(detail)
    except json.JSONDecodeError:
        return detail
    if not isinstance(decoded, dict):
        return detail
    fields = cast("dict[str, object]", decoded)
    for key in _DETAIL_FIELDS:
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value
    return detail


def _tool_started(data: Mapping[str, object]) -> Phrase:
    name = str(data.get("name", ""))
    target = _tool_target(str(data.get("detail") or ""))
    verb = _TOOL_VERBS.get(name, name)
    return Phrase(" ".join(part for part in (verb, target) if part))


def _tool_finished(data: Mapping[str, object]) -> Phrase | None:
    outcome = data.get("outcome")
    exit_code = data.get("exit_code")
    has_exit = type(exit_code) is int
    if outcome not in (ToolOutcome.SUCCESS.value, ToolOutcome.FAILURE.value) and not has_exit:
        return None
    failed = outcome == ToolOutcome.FAILURE.value or (
        has_exit and exit_code != _SUCCESS_EXIT_CODE and outcome != ToolOutcome.SUCCESS.value
    )
    label = f"{_EXIT_LABEL} {exit_code}" if has_exit else str(outcome)
    glyph, tone = (GLYPH_FAIL, Tone.FAILURE) if failed else (GLYPH_OK, Tone.SUCCESS)
    return Phrase(_joined(str(data.get("name", "")), label), glyph, tone)


def _message_text(data: Mapping[str, object], key: str) -> str:
    return _WORD_SEPARATOR.join(str(data.get(key) or "").split())


def signal_phrase(signal: str, data: Mapping[str, object]) -> Phrase | None:
    """Describe one normalized agent signal; bookkeeping and empty signals have no phrase (None)."""
    if signal == SIGNAL_MESSAGE:
        text = _message_text(data, "text")
        return Phrase(f"{_QUOTE}{text}{_QUOTE}") if text else None
    if signal == SIGNAL_TOOL_STARTED:
        return _tool_started(data)
    if signal == SIGNAL_TOOL_FINISHED:
        return _tool_finished(data)
    if signal == SIGNAL_TURN_ENDED:
        return Phrase(
            _joined(_TURN_ENDED_LABEL, _message_text(data, "last_message")), GLYPH_OK, Tone.SUCCESS
        )
    if signal == SIGNAL_TURN_FAILED:
        return Phrase(
            f"{data.get('kind')}: {_message_text(data, 'message')}", GLYPH_FAIL, Tone.FAILURE
        )
    return None if signal in _BOOKKEEPING else Phrase(signal)
