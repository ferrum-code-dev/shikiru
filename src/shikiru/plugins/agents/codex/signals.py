"""Codex hook and rollout signal mapping."""

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
ROLLOUT_PATH_FIELD = "rollout_path"
PROMPT_FIELD = "prompt"
MESSAGE_FIELD = "message"
LAST_ASSISTANT_MESSAGE_FIELD = "last_assistant_message"
ERROR_FIELD = "error"
TURN_ID_FIELD = "turn_id"

SESSION_START_EVENT = "SessionStart"
USER_PROMPT_SUBMIT_EVENT = "UserPromptSubmit"
PRE_TOOL_USE_EVENT = "PreToolUse"
POST_TOOL_USE_EVENT = "PostToolUse"
STOP_EVENT = "Stop"
PERMISSION_REQUEST_EVENT = "PermissionRequest"
POST_COMPACT_EVENT = "PostCompact"
SESSION_END_EVENT = "SessionEnd"
STOP_FAILURE_EVENT = "StopFailure"

_ERROR_EVENT_TYPES = frozenset(("error", "turn_failed", "turn_error"))
_ERROR_MESSAGE_FIELDS = (MESSAGE_FIELD, ERROR_FIELD, "reason", "detail", "text")
_STATUS_FIELDS = ("status", "status_code", "http_status", "code")
_STATUS_UNAVAILABLE_MIN = 500
_STATUS_UNAVAILABLE_MAX = 599
_STATUS_QUOTA = 429
_STATUS_AUTH = (401, 403)
_DEFAULT_FAILURE_MESSAGE = "Codex turn failed"
_DEFAULT_PERMISSION_MESSAGE = "Codex permission request"
_LIMIT_TEXTS = ("usage limit", "rate limit", "rate_limit", "quota", "usage_limited")
_CONTEXT_TEXTS = ("context exceeded", "context length", "context window", "too many tokens")
_UNAVAILABLE_TEXTS = ("overloaded", "temporarily unavailable", "service unavailable")
_INVALID_HOOK_VALUE = "Codex hook entry has no string {key!r}"


def _session_started(entry: Mapping[str, Any]) -> AgentSignal:
    return SessionStarted(session_id=_optional_string(entry, SESSION_ID_FIELD))


def _prompt_submitted(entry: Mapping[str, Any]) -> AgentSignal:
    return PromptSubmitted(text=_string(entry, PROMPT_FIELD))


def _pre_tool_use(_entry: Mapping[str, Any]) -> AgentSignal:
    return Activity(tool_running=True)


def _post_tool_use(_entry: Mapping[str, Any]) -> AgentSignal:
    return Activity(tool_running=False)


def _permission_requested(entry: Mapping[str, Any]) -> AgentSignal:
    return AttentionNeeded(message=_permission_message(entry))


def _compacted(_entry: Mapping[str, Any]) -> AgentSignal:
    return Compacted()


def _session_ended(_entry: Mapping[str, Any]) -> AgentSignal:
    return SessionEnded()


_SIMPLE_SIGNAL_BUILDERS = {
    SESSION_START_EVENT: _session_started,
    USER_PROMPT_SUBMIT_EVENT: _prompt_submitted,
    PRE_TOOL_USE_EVENT: _pre_tool_use,
    POST_TOOL_USE_EVENT: _post_tool_use,
    PERMISSION_REQUEST_EVENT: _permission_requested,
    POST_COMPACT_EVENT: _compacted,
    SESSION_END_EVENT: _session_ended,
}


def hook_signal(
    entry: Mapping[str, Any],
    *,
    rollout: Iterable[Mapping[str, Any]],
    reset_time: ResetTimeParser,
) -> AgentSignal:
    """Map one Codex hook payload and its rollout state to a normalized signal."""
    event = _string(entry, HOOK_EVENT_FIELD)
    builder = _SIMPLE_SIGNAL_BUILDERS.get(event)
    if builder is not None:
        return builder(entry)
    if event in (STOP_EVENT, STOP_FAILURE_EVENT):
        return _stop_signal(entry, rollout, reset_time)
    message = f"unknown Codex hook event {event!r}"
    raise ValueError(message)


def latest_error(entries: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the latest rollout entry that represents an agent error."""
    latest: Mapping[str, Any] | None = None
    for entry in entries:
        payload = _payload(entry)
        event_type = payload.get("type")
        if event_type in _ERROR_EVENT_TYPES or _contains_error_payload(payload):
            latest = payload
    return latest


def classify_failure(
    entry: Mapping[str, Any] | None,
    reset_time: ResetTimeParser,
    *,
    rollout: Iterable[Mapping[str, Any]] = (),
) -> tuple[FailureKind, str, datetime | None]:
    """Classify a Codex rollout error and parse a known quota reset time."""
    if entry is None:
        return FailureKind.OTHER, _DEFAULT_FAILURE_MESSAGE, None
    message = _error_text(entry)
    lowered = message.lower()
    status = _status(entry)
    if _is_status(status, _STATUS_QUOTA) or any(text in lowered for text in _LIMIT_TEXTS):
        return FailureKind.QUOTA, message, _quota_reset(rollout, reset_time)
    if _is_unavailable_status(status) or any(text in lowered for text in _UNAVAILABLE_TEXTS):
        return FailureKind.UNAVAILABLE, message, None
    if _is_status(status, *_STATUS_AUTH) or "authentication" in lowered:
        return FailureKind.AUTH, message, None
    if any(text in lowered for text in _CONTEXT_TEXTS):
        return FailureKind.CONTEXT, message, None
    return FailureKind.OTHER, message, None


def rollout_message(entries: Iterable[Mapping[str, Any]], turn_id: str | None = None) -> str:
    """Return the latest assistant message text recorded in a Codex rollout."""
    message = ""
    matching_message = ""
    for entry in entries:
        payload = _payload(entry)
        candidate = _message_from_payload(payload)
        if candidate:
            message = candidate
            if turn_id is None or _turn_matches(payload, turn_id):
                matching_message = candidate
    return matching_message or message


def _stop_signal(
    hook: Mapping[str, Any], rollout: Iterable[Mapping[str, Any]], reset_time: ResetTimeParser
) -> AgentSignal:
    entries = list(rollout)
    error = latest_error(entries)
    if error is None and _contains_error_payload(hook):
        error = hook
    if error is not None:
        kind, message, resets_at = classify_failure(error, reset_time, rollout=entries)
        return TurnFailed(kind=kind, message=message, resets_at=resets_at)
    turn_id = _optional_string(hook, TURN_ID_FIELD)
    last_message = _optional_string(hook, LAST_ASSISTANT_MESSAGE_FIELD) or rollout_message(
        entries, turn_id
    )
    return TurnEnded(last_message=last_message)


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = entry.get("payload")
    if isinstance(payload, Mapping):
        return cast("Mapping[str, Any]", payload)
    return entry


def _contains_error_payload(entry: Mapping[str, Any]) -> bool:
    event_type = entry.get("type")
    if event_type in _ERROR_EVENT_TYPES:
        return True
    return isinstance(entry.get(ERROR_FIELD), Mapping | str)


def _permission_message(entry: Mapping[str, Any]) -> str:
    for key in (MESSAGE_FIELD, "reason", "prompt", "tool_name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return _DEFAULT_PERMISSION_MESSAGE


def _message_from_payload(entry: Mapping[str, Any]) -> str:
    event_type = entry.get("type")
    item = entry.get("item")
    if isinstance(item, Mapping):
        item_payload = cast("Mapping[str, Any]", item)
        item_type = item_payload.get("type")
        if item_type in ("agent_message", "message") or event_type == "item_completed":
            message = _message_value(item_payload)
            if message:
                return message
    if event_type in ("agent_message", "message"):
        return _message_value(entry)
    content = entry.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in cast("list[object]", content):
            if isinstance(item, Mapping):
                text = _text_value(cast("Mapping[str, Any]", item))
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def _turn_matches(entry: Mapping[str, Any], turn_id: str) -> bool:
    if entry.get(TURN_ID_FIELD) == turn_id:
        return True
    item = entry.get("item")
    return (
        isinstance(item, Mapping) and cast("Mapping[str, Any]", item).get(TURN_ID_FIELD) == turn_id
    )


def _message_value(entry: Mapping[str, Any]) -> str:
    for key in ("text", MESSAGE_FIELD, LAST_ASSISTANT_MESSAGE_FIELD, "output"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            text = _text_value(cast("Mapping[str, Any]", value))
            if text:
                return text
        if isinstance(value, list):
            parts: list[str] = []
            for item in cast("list[object]", value):
                if isinstance(item, Mapping):
                    item_text = _text_value(cast("Mapping[str, Any]", item))
                    if item_text:
                        parts.append(item_text)
            text = "".join(parts)
            if text:
                return text
    return ""


def _text_value(entry: Mapping[str, Any]) -> str:
    for key in ("text", "output_text", "value"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""


def _error_text(entry: Mapping[str, Any]) -> str:
    for key in _ERROR_MESSAGE_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, Mapping):
            nested = _error_text(cast("Mapping[str, Any]", value))
            if nested:
                return nested
        if isinstance(value, list):
            parts: list[str] = []
            for item in cast("list[object]", value):
                if isinstance(item, Mapping):
                    text = _error_text(cast("Mapping[str, Any]", item))
                    if text:
                        parts.append(text)
            nested = " ".join(parts)
            if nested:
                return nested
    return _DEFAULT_FAILURE_MESSAGE


def _status(entry: Mapping[str, Any]) -> int | None:
    for key in _STATUS_FIELDS:
        value = entry.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _is_status(value: int | None, *wanted: int) -> bool:
    return value in wanted


def _is_unavailable_status(value: int | None) -> bool:
    return value is not None and _STATUS_UNAVAILABLE_MIN <= value <= _STATUS_UNAVAILABLE_MAX


def _quota_reset(
    entries: Iterable[Mapping[str, Any]], reset_time: ResetTimeParser
) -> datetime | None:
    resets: list[datetime] = []
    for entry in entries:
        payload = _payload(entry)
        rate_limits = payload.get("rate_limits")
        if not isinstance(rate_limits, Mapping):
            continue
        typed_limits = cast("Mapping[str, Any]", rate_limits)
        for name in ("primary", "secondary"):
            window = typed_limits.get(name)
            if not isinstance(window, Mapping):
                continue
            value = cast("Mapping[str, Any]", window).get("resets_at")
            if value is not None:
                parsed = reset_time.parse(str(value))
                if parsed is not None:
                    resets.append(parsed)
    return min(resets) if resets else None


def _optional_string(entry: Mapping[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        message = _INVALID_HOOK_VALUE.format(key=key)
        raise TypeError(message)
    return value


def _string(entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        message = _INVALID_HOOK_VALUE.format(key=key)
        raise TypeError(message)
    return value
