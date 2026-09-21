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
TRANSCRIPT_PATH_FIELD = "transcript_path"
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
_ERROR_EVENT_TYPE = "error"
_ERROR_MESSAGE_FIELD = MESSAGE_FIELD
_STATUS_FIELD = "status"
_STATUS_UNAVAILABLE_MIN = 500
_STATUS_UNAVAILABLE_MAX = 599
_STATUS_QUOTA = 429
_STATUS_AUTH = (401, 403)
_DEFAULT_FAILURE_MESSAGE = "Codex turn failed"
_DEFAULT_PERMISSION_MESSAGE = "Codex permission request"
_LIMIT_TEXTS = ("usage limit", "rate limit", "rate_limit", "quota", "usage_limited")
_CONTEXT_TEXTS = ("context exceeded", "context length", "context window", "too many tokens")
_UNAVAILABLE_TEXTS = ("overloaded", "temporarily unavailable", "service unavailable")
_AUTH_TEXTS = ("authentication", "unauthorized")
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
    if event == STOP_EVENT:
        return _stop_signal(entry, rollout, reset_time)
    message = f"unknown Codex hook event {event!r}"
    raise ValueError(message)


def latest_error(
    entries: Iterable[Mapping[str, Any]], turn_id: str | None
) -> Mapping[str, Any] | None:
    """Return the latest error for the stopped Codex turn."""
    if turn_id is None:
        return None
    latest: Mapping[str, Any] | None = None
    for entry in entries:
        payload = _payload(entry)
        if payload.get("type") != _ERROR_EVENT_TYPE:
            continue
        if payload.get(TURN_ID_FIELD) != turn_id:
            continue
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
    if _is_status(status, *_STATUS_AUTH) or any(text in lowered for text in _AUTH_TEXTS):
        return FailureKind.AUTH, message, None
    if any(text in lowered for text in _CONTEXT_TEXTS):
        return FailureKind.CONTEXT, message, None
    return FailureKind.OTHER, message, None


def rollout_message(entries: Iterable[Mapping[str, Any]], turn_id: str | None = None) -> str:
    """Return the completed message for a Codex turn."""
    if turn_id is None:
        return ""
    message = ""
    for entry in entries:
        payload = _payload(entry)
        if payload.get("type") != "task_complete":
            continue
        if payload.get(TURN_ID_FIELD) != turn_id:
            continue
        candidate = payload.get("last_agent_message")
        if isinstance(candidate, str):
            message = candidate
    return message


def _stop_signal(
    hook: Mapping[str, Any], rollout: Iterable[Mapping[str, Any]], reset_time: ResetTimeParser
) -> AgentSignal:
    entries = list(rollout)
    turn_id = _optional_string(hook, TURN_ID_FIELD)
    error = latest_error(entries, turn_id)
    hook_error = hook.get(ERROR_FIELD)
    if error is None and isinstance(hook_error, str) and hook_error:
        error = hook
    if error is not None:
        kind, message, resets_at = classify_failure(error, reset_time, rollout=entries)
        return TurnFailed(kind=kind, message=message, resets_at=resets_at)
    last_message = _optional_string(hook, LAST_ASSISTANT_MESSAGE_FIELD) or rollout_message(
        entries, turn_id
    )
    return TurnEnded(last_message=last_message)


def _payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    if entry.get("type") != "event_msg":
        return {}
    payload = entry.get("payload")
    return cast("Mapping[str, Any]", payload) if isinstance(payload, Mapping) else {}


def _permission_message(entry: Mapping[str, Any]) -> str:
    for key in (MESSAGE_FIELD, "reason", "prompt", "tool_name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return _DEFAULT_PERMISSION_MESSAGE


def _error_text(entry: Mapping[str, Any]) -> str:
    value = entry.get(_ERROR_MESSAGE_FIELD)
    if isinstance(value, str) and value:
        return value
    return _DEFAULT_FAILURE_MESSAGE


def _status(entry: Mapping[str, Any]) -> int | None:
    value = entry.get(_STATUS_FIELD)
    if isinstance(value, int):
        return value
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
