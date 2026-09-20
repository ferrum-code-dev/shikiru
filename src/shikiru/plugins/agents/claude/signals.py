"""Claude Code hook and transcript failure mapping."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

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
)

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.agents.sdk.reset_time import ResetTimeParser

HOOK_EVENT_FIELD = "hook_event_name"
SESSION_ID_FIELD = "session_id"
TRANSCRIPT_PATH_FIELD = "transcript_path"
PROMPT_FIELD = "prompt"
LAST_ASSISTANT_MESSAGE_FIELD = "last_assistant_message"
MESSAGE_FIELD = "message"
ERROR_FIELD = "error"
API_ERROR_FLAG = "isApiErrorMessage"
API_ERROR_STATUS_FIELD = "apiErrorStatus"

SESSION_START_EVENT = "SessionStart"
USER_PROMPT_SUBMIT_EVENT = "UserPromptSubmit"
PRE_TOOL_USE_EVENT = "PreToolUse"
POST_TOOL_USE_EVENT = "PostToolUse"
STOP_EVENT = "Stop"
STOP_FAILURE_EVENT = "StopFailure"
NOTIFICATION_EVENT = "Notification"
POST_COMPACT_EVENT = "PostCompact"
SESSION_END_EVENT = "SessionEnd"

_LIMIT_TEXT = "limit"
_PROMPT_TOO_LONG_TEXT = "prompt is too long"
_STATUS_UNAVAILABLE_MIN = 500
_STATUS_UNAVAILABLE_MAX = 599
_STATUS_QUOTA = 429
_STATUS_AUTH = (401, 403)
_STATUS_QUOTA_TEXT = "rate limit"
_DEFAULT_FAILURE_MESSAGE = "Claude turn failed"
_INVALID_HOOK_VALUE = "Claude hook entry has no string {key!r}"


def _session_started(entry: Mapping[str, Any]) -> AgentSignal:
    return SessionStarted(session_id=_string(entry, SESSION_ID_FIELD))


def _prompt_submitted(entry: Mapping[str, Any]) -> AgentSignal:
    return PromptSubmitted(text=_string(entry, PROMPT_FIELD))


def _pre_tool_use(_entry: Mapping[str, Any]) -> AgentSignal:
    return Activity(tool_running=True)


def _post_tool_use(_entry: Mapping[str, Any]) -> AgentSignal:
    return Activity(tool_running=False)


def _turn_ended(entry: Mapping[str, Any]) -> AgentSignal:
    return TurnEnded(last_message=_string(entry, LAST_ASSISTANT_MESSAGE_FIELD))


def _notification(entry: Mapping[str, Any]) -> AgentSignal:
    return AttentionNeeded(message=_string(entry, MESSAGE_FIELD))


def _compacted(_entry: Mapping[str, Any]) -> AgentSignal:
    return Compacted()


def _session_ended(_entry: Mapping[str, Any]) -> AgentSignal:
    return SessionEnded()


_SIMPLE_SIGNAL_BUILDERS = {
    SESSION_START_EVENT: _session_started,
    USER_PROMPT_SUBMIT_EVENT: _prompt_submitted,
    PRE_TOOL_USE_EVENT: _pre_tool_use,
    POST_TOOL_USE_EVENT: _post_tool_use,
    STOP_EVENT: _turn_ended,
    NOTIFICATION_EVENT: _notification,
    POST_COMPACT_EVENT: _compacted,
    SESSION_END_EVENT: _session_ended,
}


def hook_signal(
    entry: Mapping[str, Any],
    *,
    transcript: Iterable[Mapping[str, Any]],
    reset_time: ResetTimeParser,
) -> AgentSignal:
    """Map one Claude hook payload to a normalized agent signal."""
    event = entry[HOOK_EVENT_FIELD]
    if event == STOP_FAILURE_EVENT:
        return _turn_failed(entry, transcript, reset_time)
    builder = _SIMPLE_SIGNAL_BUILDERS.get(event)
    if builder is not None:
        return builder(entry)
    message = f"unknown Claude hook event {event!r}"
    raise ValueError(message)


def latest_api_error(entries: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the latest transcript entry marked as an API error."""
    latest: Mapping[str, Any] | None = None
    for entry in entries:
        if entry.get(API_ERROR_FLAG) is True:
            latest = entry
    return latest


def classify_failure(
    entry: Mapping[str, Any] | None, reset_time: ResetTimeParser
) -> tuple[FailureKind, str, datetime | None]:
    """Classify a Claude transcript API error and parse a known quota reset time."""
    if entry is None:
        return FailureKind.OTHER, _DEFAULT_FAILURE_MESSAGE, None
    message = _error_text(entry)
    lowered = message.lower()
    status = entry.get(API_ERROR_STATUS_FIELD)
    if _is_status(status, _STATUS_QUOTA) or _LIMIT_TEXT in lowered or _STATUS_QUOTA_TEXT in lowered:
        return FailureKind.QUOTA, message, reset_time.parse(message)
    if _is_unavailable_status(status) or "overloaded" in lowered:
        return FailureKind.UNAVAILABLE, message, None
    if _is_status(status, *_STATUS_AUTH) or "authentication" in lowered:
        return FailureKind.AUTH, message, None
    if _PROMPT_TOO_LONG_TEXT in lowered:
        return FailureKind.CONTEXT, message, None
    return FailureKind.OTHER, message, None


def _turn_failed(
    hook: Mapping[str, Any], transcript: Iterable[Mapping[str, Any]], reset_time: ResetTimeParser
) -> TurnFailed:
    entry = latest_api_error(transcript)
    if entry is None:
        entry = hook
    kind, message, resets_at = classify_failure(entry, reset_time)
    if entry is hook:
        hook_message = hook.get(ERROR_FIELD)
        if isinstance(hook_message, str) and hook_message:
            message = hook_message
    return TurnFailed(kind=kind, message=message, resets_at=resets_at)


def _error_text(entry: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    message = entry.get(MESSAGE_FIELD)
    if isinstance(message, str):
        pieces.append(message)
    elif isinstance(message, Mapping):
        typed_message = cast("Mapping[str, Any]", message)
        content = typed_message.get("content")
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            pieces.extend(_text_items(cast("list[object]", content)))
    content = entry.get("content")
    if isinstance(content, str):
        pieces.append(content)
    elif isinstance(content, list):
        pieces.extend(_text_items(cast("list[object]", content)))
    if not pieces:
        error = entry.get(ERROR_FIELD)
        if isinstance(error, str):
            pieces.append(error)
    return " ".join(piece for piece in pieces if piece)


def _is_status(value: object, *wanted: int) -> bool:
    return isinstance(value, int) and value in wanted


def _is_unavailable_status(value: object) -> bool:
    return isinstance(value, int) and _STATUS_UNAVAILABLE_MIN <= value <= _STATUS_UNAVAILABLE_MAX


def _text_items(items: list[object]) -> list[str]:
    return [
        text
        for item in items
        if isinstance(item, Mapping)
        for text in [cast("Mapping[str, Any]", item).get("text")]
        if isinstance(text, str)
    ]


def _string(entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        message = _INVALID_HOOK_VALUE.format(key=key)
        raise TypeError(message)
    return value
