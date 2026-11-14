"""Claude Code stream-json turn parser and process sequence."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import (
    PERCENT,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    TOOL_DETAIL_MAX_CHARS,
    AgentSignal,
    Compacted,
    FailureKind,
    Message,
    QuotaReported,
    QuotaWindow,
    SessionStarted,
    ToolFinished,
    ToolOutcome,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    Usage,
    UsageReported,
    add_optional,
    module_command,
)
from shikiru.agents.sdk.turn import (
    EMPTY_OUTPUT,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
)
from shikiru.plugins.agents.claude.context import (
    compact_limit_tokens,
    read_transcript,
    transcript_context_tokens,
)
from shikiru.plugins.agents.claude.options import (
    ADD_DIR_FLAG,
    CLAUDE_COMMAND,
    EFFORT_FLAG,
    MODEL_FLAG,
    OUTPUT_FORMAT_FLAG,
    PRINT_FLAG,
    RESUME_FLAG,
    SESSION_ID_FLAG,
    SETTINGS_FLAG,
    STATUS_FORBIDDEN,
    STATUS_PAYMENT_REQUIRED,
    STATUS_TOO_MANY_REQUESTS,
    STATUS_UNAUTHORIZED,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

_STREAM_JSON = "stream-json"
_VERBOSE_FLAG = "--verbose"
_COMPACT_PROMPT = "/compact"
CONTINUE_PROMPT = "Continue the task exactly where you stopped."
HOOK_MODULE = "shikiru.plugins.agents.claude.hook"
COMPACT_MARKER_SUFFIX = ".compact-marker"
COMPACT_STATE_SUFFIX = ".compact-state"
CLAUDE_CONFIG_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"
HOME_ENV_VAR = "HOME"
CLAUDE_DIRNAME = ".claude"
PROJECTS_DIRNAME = "projects"
TRANSCRIPT_SUFFIX = ".jsonl"
_HOOKS_FIELD = "hooks"
_POST_TOOL_USE_EVENT = "PostToolUse"
_HOOK_MATCHER_FIELD = "matcher"
_ANY_TOOL_MATCHER = "*"
_HOOK_TYPE_FIELD = "type"
_COMMAND_HOOK_TYPE = "command"
_HOOK_COMMAND_FIELD = "command"
_SYSTEM_TYPE = "system"
_ASSISTANT_TYPE = "assistant"
_USER_TYPE = "user"
_RESULT_TYPE = "result"
_RATE_LIMIT_TYPE = "rate_limit_event"
_INIT_SUBTYPE = "init"
_COMPACT_BOUNDARY_SUBTYPE = "compact_boundary"
_SUCCESS_SUBTYPE = "success"
_ERROR_TERMINAL_REASONS = (
    "error",
    "api_error",
    "prompt_too_long",
    "context_length_exceeded",
)
_LIST_COST_BASIS = "list"
_EMPTY_RESULT = ""
_TEXT_BLOCK_TYPE = "text"
_TOOL_USE_BLOCK_TYPE = "tool_use"
_TOOL_RESULT_BLOCK_TYPE = "tool_result"
_TEXT_FIELD = "text"
_CONTENT_FIELD = "content"
_MESSAGE_FIELD = "message"
_USAGE_FIELD = "usage"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cache_read_input_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_creation_input_tokens"
_SESSION_ID_FIELD = "session_id"
_MODEL_FIELD = "model"
_MODEL_USAGE_FIELD = "modelUsage"
_MODEL_USAGE_DELTA_FIELD = "model_usage_since_last_result"
_INPUT_TOKENS_DELTA_FIELD = "inputTokens"
_OUTPUT_TOKENS_DELTA_FIELD = "outputTokens"
_CACHE_READ_TOKENS_DELTA_FIELD = "cacheReadInputTokens"
_CACHE_WRITE_TOKENS_DELTA_FIELD = "cacheCreationInputTokens"
_CONTEXT_WINDOW_FIELD = "contextWindow"
_COST_BASIS_FIELD = "costBasis"
_COST_USD_FIELD = "costUSD"
_TOTAL_COST_FIELD = "total_cost_usd"
_COMPACT_METADATA_FIELD = "compact_metadata"
_ORIGIN_FIELD = "origin"
_ORIGIN_KIND_FIELD = "kind"
_TASK_NOTIFICATION_ORIGIN_KIND = "task-notification"
_TASK_STARTED_SUBTYPE = "task_started"
_TASK_UPDATED_SUBTYPE = "task_updated"
_TASK_NOTIFICATION_SUBTYPE = "task_notification"
_TASK_ID_FIELD = "task_id"
_TASK_DESCRIPTION_FIELD = "description"
_TASK_SUMMARY_FIELD = "summary"
_TASK_STATUS_FIELD = "status"
_TASK_PATCH_FIELD = "patch"
_IS_BACKGROUNDED_FIELD = "is_backgrounded"
_RESULT_INDEX_FIELD = "result_index"
_STOP_REASON_FIELD = "stop_reason"
_PRE_TOKENS_FIELD = "pre_tokens"
_POST_TOKENS_FIELD = "post_tokens"
_RATE_LIMIT_INFO_FIELD = "rate_limit_info"
_UNIFIED_WINDOWS_FIELD = "unifiedWindows"
_FIVE_HOUR_PROTOCOL_KEY = "five_hour"
_SEVEN_DAY_PROTOCOL_KEY = "seven_day"
_QUOTA_WINDOW_NAMES = {
    _FIVE_HOUR_PROTOCOL_KEY: QUOTA_WINDOW_FIVE_HOUR,
    _SEVEN_DAY_PROTOCOL_KEY: QUOTA_WINDOW_SEVEN_DAY,
}
_UTILIZATION_FIELD = "utilization"
_RESETS_AT_FIELD = "resetsAt"
_API_ERROR_STATUS_FIELD = "api_error_status"
_STATUS_FIELD = "status"
_IS_ERROR_FIELD = "is_error"
_TERMINAL_REASON_FIELD = "terminal_reason"
_RESULT_FIELD = "result"
_ERROR_FIELD = "error"
_ERRORS_FIELD = "errors"
_TOOL_NAME_FIELD = "name"
_TOOL_ID_FIELD = "id"
_TOOL_USE_ID_FIELD = "tool_use_id"
_TOOL_INPUT_FIELD = "input"
_TYPE_FIELD = "type"
_SUBTYPE_FIELD = "subtype"
_MESSAGE_TEXT_FIELD = "message"
_ERROR_TAG_PATTERN = re.compile(r"\[claude-code:(?P<code>[a-z0-9_-]+)\]", re.IGNORECASE)
_ERROR_STATUSES = (
    STATUS_UNAUTHORIZED,
    STATUS_PAYMENT_REQUIRED,
    STATUS_FORBIDDEN,
    STATUS_TOO_MANY_REQUESTS,
)
_UNAVAILABLE_STATUS_MIN = 500
_UNAVAILABLE_STATUS_MAX = 599
_CONTEXT_ERROR_TEXTS = (
    "prompt is too long",
    "prompt_too_long",
    "context length",
    "context_length_exceeded",
    "context window",
)
_QUOTA_ERROR_TEXTS = ("usage limit", "rate limit", "too many requests")
_UNAVAILABLE_ERROR_TEXTS = ("overloaded", "unrecognized_model", "unrecognized model")
_AUTH_ERROR_TEXTS = ("authentication", "unauthorized", "invalid api key")
_UNKNOWN_ERROR_MESSAGE = "Claude stream ended without a result"
_PROCESS_FAILURE_MESSAGE = "Claude process exited unsuccessfully after emitting a result"
_INVALID_COMPACTION_MESSAGE = "Claude compaction ended without compact_boundary"
_MALFORMED_COMPACTION_MESSAGE = "Claude compaction reported malformed token counts"
_INEFFECTIVE_COMPACTION_MESSAGE = (
    "Claude compaction left {post_tokens} tokens, not below the {limit}-token compaction limit"
)
_MALFORMED_STREAM_MESSAGE = "Claude emitted malformed stream-json"
_STATUS_MESSAGE = "Claude returned API status {status}"
_STDERR_MESSAGE = "Claude exited with status {exit_code}: {message}"


class ClaudeTurnParser:
    """Map Claude's stream-json records and process exit to agent signals."""

    def __init__(
        self,
        *,
        session_id: str,
        model: str,
        initial_usage: Usage | None,
        clock: Clock,
        reset_time: ResetTimeParser,
        compact_limit_tokens: int | None = None,
    ) -> None:
        self._session_id = session_id
        self._model = model
        self._initial_usage = initial_usage or _empty_usage()
        self._clock = clock
        self._reset_time = reset_time
        self._compact_limit_tokens = compact_limit_tokens
        self._tool_names: dict[str, str] = {}
        self._tool_ids: list[str] = []
        self._latest_assistant_usage: Mapping[str, Any] | None = None
        self._saw_assistant_event = False
        self._latest_model_usage: Mapping[str, Any] | None = None
        self._latest_cost_basis: str | None = None
        self._latest_cost: float | None = None
        self._latest_message = ""
        self._record_order = 0
        self._latest_message_order = -1
        self._latest_result_order = -1
        self._saw_result_usage = False
        self._result_input_tokens: int | None = 0
        self._result_output_tokens: int | None = 0
        self._result_cache_read_tokens: int | None = 0
        self._result_cache_write_tokens: int | None = 0
        self._session_started = False
        self._started_session_id: str | None = None
        self._pending_activity: list[AgentSignal] = []
        self._latest_quota: tuple[QuotaWindow, ...] = ()
        self._terminal = False
        self._malformed_line: str | None = None
        self._did_compact = False
        self._post_compaction_tokens: int | None = None
        self._malformed_compaction_metadata = False
        self._failure_payload: Mapping[str, Any] | None = None
        self._success_payload: Mapping[str, Any] | None = None
        self._context_exceeded = False

    @property
    def context_exceeded(self) -> bool:
        """Return whether this process failed because its context was full."""
        return self._context_exceeded

    def feed(self, line: str) -> list[AgentSignal]:
        if self._terminal:
            return []
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            self._malformed_line = self._malformed_line or line
            return []
        if not isinstance(raw, Mapping):
            self._malformed_line = self._malformed_line or line
            return []
        payload = cast("Mapping[str, Any]", raw)
        self._record_order += 1
        handlers = {
            _SYSTEM_TYPE: self._system,
            _ASSISTANT_TYPE: self._assistant,
            _USER_TYPE: self._user,
            _RATE_LIMIT_TYPE: self._rate_limit,
            _RESULT_TYPE: self._result,
        }
        handler = handlers.get(cast("str", payload.get(_TYPE_FIELD)))
        return handler(payload) if handler is not None else []

    def finish(
        self, exit_code: int, stderr: str, *, interrupted: bool = False
    ) -> list[AgentSignal]:
        if self._terminal:
            return []
        if self._malformed_line is not None:
            self._terminal = True
            return [
                *self._usage_signals(),
                TurnFailed(
                    FailureKind.UNKNOWN,
                    _stderr_message(exit_code, stderr, _MALFORMED_STREAM_MESSAGE),
                    None,
                ),
            ]
        if self._failure_payload is not None:
            self._terminal = True
            return self._fail(self._failure_payload, stderr)
        if self._success_payload is not None:
            if exit_code != 0:
                return self._finish_failed_process(exit_code, stderr)
            return self._finish_success(self._success_payload)
        return self._finish_without_result(exit_code, stderr, interrupted=interrupted)

    def _finish_success(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        compaction_problem = self._compaction_problem()
        if compaction_problem is not None:
            self._terminal = True
            return [
                *self._usage_signals(assume_consumption=True),
                TurnFailed(FailureKind.OTHER, compaction_problem, None),
            ]
        usage = self._reported_usage()
        result_text = payload.get(_RESULT_FIELD)
        if self._latest_message_order > self._latest_result_order:
            last_message = self._latest_message
        elif isinstance(result_text, str):
            last_message = result_text
        else:
            last_message = self._latest_message
        self._terminal = True
        signals: list[AgentSignal] = [UsageReported(usage or self._usage_gap())]
        signals.append(TurnEnded(last_message))
        return signals

    def _finish_without_result(
        self, exit_code: int, stderr: str, *, interrupted: bool = False
    ) -> list[AgentSignal]:
        failure = self._classify_failure(None, stderr)
        if failure is not None:
            kind, message, resets_at = failure
            self._terminal = True
            return [
                *self._usage_signals(),
                TurnFailed(kind, _stderr_message(exit_code, stderr, message), resets_at),
            ]
        compaction_problem = self._compaction_problem()
        if interrupted and compaction_problem is None:
            self._terminal = True
            signals: list[AgentSignal] = [*self._usage_signals()]
            return signals
        message = compaction_problem or _UNKNOWN_ERROR_MESSAGE
        kind = FailureKind.OTHER if compaction_problem is not None else FailureKind.UNKNOWN
        self._terminal = True
        return [
            *self._usage_signals(assume_consumption=compaction_problem is not None),
            TurnFailed(kind, _stderr_message(exit_code, stderr, message), None),
        ]

    def _finish_failed_process(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        failure = self._classify_failure(None, stderr)
        kind, message, resets_at = failure or (
            FailureKind.UNKNOWN,
            _PROCESS_FAILURE_MESSAGE,
            None,
        )
        self._terminal = True
        return [
            *self._usage_signals(assume_consumption=True),
            TurnFailed(kind, _stderr_message(exit_code, stderr, message), resets_at),
        ]

    def _system(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        subtype = payload.get(_SUBTYPE_FIELD)
        if subtype == _INIT_SUBTYPE:
            return self._init_session(payload)
        if subtype == _COMPACT_BOUNDARY_SUBTYPE:
            return self._compact_boundary(payload)
        return self._system_activity(payload)

    def _init_session(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        raw_session_id = payload.get(_SESSION_ID_FIELD)
        if not isinstance(raw_session_id, str) or not raw_session_id.strip():
            return []
        session_id = raw_session_id
        model = payload.get(_MODEL_FIELD)
        if isinstance(model, str):
            self._model = model
        if self._session_started and session_id == self._started_session_id:
            return []
        if self._session_started:
            return [SessionStarted(session_id)]
        self._session_started = True
        self._started_session_id = session_id
        pending, self._pending_activity = self._pending_activity, []
        return [SessionStarted(session_id), *pending]

    def _compact_boundary(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._did_compact = True
        metadata = _mapping(payload.get(_COMPACT_METADATA_FIELD))
        if metadata is not None:
            self._malformed_compaction_metadata |= any(
                key in metadata and _optional_nonnegative_integer(metadata, key) is None
                for key in (_PRE_TOKENS_FIELD, _POST_TOKENS_FIELD)
            )
        self._post_compaction_tokens = _optional_nonnegative_integer(metadata, _POST_TOKENS_FIELD)
        if self._malformed_compaction_metadata:
            return []
        return [
            Compacted(
                _optional_nonnegative_integer(metadata, _PRE_TOKENS_FIELD),
                self._post_compaction_tokens,
            )
        ]

    def _system_activity(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        activity = self._background_activity(payload)
        if not activity:
            return []
        if self._session_started:
            return activity
        self._pending_activity.extend(activity)
        return []

    def _background_activity(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        subtype = payload.get(_SUBTYPE_FIELD)
        if subtype == _TASK_STARTED_SUBTYPE:
            description = payload.get(_TASK_DESCRIPTION_FIELD)
            if payload.get(_IS_BACKGROUNDED_FIELD) is True and isinstance(description, str):
                return [Message(description)] if description else []
        if subtype == _TASK_NOTIFICATION_SUBTYPE:
            summary = payload.get(_TASK_SUMMARY_FIELD)
            if isinstance(summary, str) and summary:
                return [Message(summary)]
        if subtype == _TASK_UPDATED_SUBTYPE:
            patch = _mapping(payload.get(_TASK_PATCH_FIELD))
            status = patch.get(_TASK_STATUS_FIELD) if patch is not None else None
            task_id = payload.get(_TASK_ID_FIELD)
            if isinstance(status, str) and isinstance(task_id, str):
                return [Message(f"Background task {task_id}: {status}")]
        return []

    def _assistant(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._saw_assistant_event = True
        blocks = self._assistant_content(payload)
        if not isinstance(blocks, list):
            return []
        signals: list[AgentSignal] = []
        for block in cast("list[object]", blocks):
            signals.extend(self._assistant_block(block))
        return signals

    def _assistant_content(self, payload: Mapping[str, Any]) -> object:
        message = _mapping(payload.get(_MESSAGE_FIELD))
        if message is not None:
            usage = _mapping(message.get(_USAGE_FIELD))
            if usage is not None:
                self._latest_assistant_usage = usage
            return message.get(_CONTENT_FIELD)
        return payload.get(_CONTENT_FIELD)

    def _assistant_block(self, block: object) -> list[AgentSignal]:
        item = _mapping(block)
        if item is None:
            return []
        if item.get(_TYPE_FIELD) == _TEXT_BLOCK_TYPE:
            text = item.get(_TEXT_FIELD)
            if isinstance(text, str) and text:
                self._latest_message = text
                self._latest_message_order = self._record_order
                return [Message(text)]
        if item.get(_TYPE_FIELD) == _TOOL_USE_BLOCK_TYPE:
            return self._tool_started(item)
        return []

    def _tool_started(self, item: Mapping[str, Any]) -> list[AgentSignal]:
        name = item.get(_TOOL_NAME_FIELD)
        if not isinstance(name, str):
            return []
        signals: list[AgentSignal] = [ToolStarted(name, _tool_detail(item.get(_TOOL_INPUT_FIELD)))]
        tool_id = item.get(_TOOL_ID_FIELD)
        if isinstance(tool_id, str):
            self._tool_names[tool_id] = name
            self._tool_ids.append(tool_id)
        return signals

    def _user(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        message = _mapping(payload.get(_MESSAGE_FIELD))
        blocks = message.get(_CONTENT_FIELD) if message is not None else None
        if not isinstance(blocks, list):
            return []
        signals: list[AgentSignal] = []
        for block in cast("list[object]", blocks):
            item = _mapping(block)
            if item is None or item.get(_TYPE_FIELD) != _TOOL_RESULT_BLOCK_TYPE:
                continue
            tool_id = item.get(_TOOL_USE_ID_FIELD)
            if not isinstance(tool_id, str) and self._tool_ids:
                tool_id = self._tool_ids[0]
            if isinstance(tool_id, str):
                name = self._tool_names.pop(tool_id, None)
                if tool_id in self._tool_ids:
                    self._tool_ids.remove(tool_id)
                if name is not None:
                    is_error = item.get(_IS_ERROR_FIELD)
                    outcome = (
                        ToolOutcome.FAILURE
                        if is_error is True
                        else ToolOutcome.SUCCESS
                        if is_error is False
                        else None
                    )
                    signals.append(ToolFinished(name, outcome))
        return signals

    def _rate_limit(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        info = _mapping(payload.get(_RATE_LIMIT_INFO_FIELD))
        if info is None:
            return []
        windows = _mapping(info.get(_UNIFIED_WINDOWS_FIELD)) or info
        observed_at = self._clock.now()
        reported = tuple(
            self._quota_window(name, raw, observed_at)
            for key, name in _QUOTA_WINDOW_NAMES.items()
            if (raw := _mapping(windows.get(key))) is not None
            and isinstance(raw.get(_UTILIZATION_FIELD), int | float)
        )
        if not reported:
            return []
        self._latest_quota = reported
        return [QuotaReported(reported)]

    def _quota_window(
        self, name: str, raw: Mapping[str, Any], observed_at: datetime
    ) -> QuotaWindow:
        reset_value = raw.get(_RESETS_AT_FIELD)
        resets_at = self._reset_time.parse(str(reset_value)) if reset_value is not None else None
        return QuotaWindow(
            name=name,
            used_pct=float(cast("int | float", raw[_UTILIZATION_FIELD])) * PERCENT,
            resets_at=resets_at,
            observed_at=observed_at,
        )

    def _result(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        if self._malformed_line is not None:
            self._terminal = True
            return [TurnFailed(FailureKind.UNKNOWN, _MALFORMED_STREAM_MESSAGE, None)]
        if _is_error_result(payload):
            self._failure_payload = payload
            return []
        if _is_no_work_task_notification_result(payload, self._model):
            return []
        self._success_payload = payload
        self._latest_result_order = self._record_order
        self._accumulate_result_usage(payload)
        model_usage = _mapping(payload.get(_MODEL_USAGE_FIELD))
        selected_model_usage = _select_model_usage(model_usage, self._model)
        if selected_model_usage is not None:
            latest_model_usage = dict(self._latest_model_usage or {})
            latest_model_usage.update(selected_model_usage)
            self._latest_model_usage = latest_model_usage
            cost_basis = selected_model_usage.get(_COST_BASIS_FIELD)
            if isinstance(cost_basis, str):
                self._latest_cost_basis = cost_basis
        total_cost = payload.get(_TOTAL_COST_FIELD)
        if isinstance(total_cost, int | float) and not isinstance(total_cost, bool):
            self._latest_cost = float(total_cost)
        elif selected_model_usage is not None:
            model_cost = selected_model_usage.get(_COST_USD_FIELD)
            if isinstance(model_cost, int | float) and not isinstance(model_cost, bool):
                self._latest_cost = float(model_cost)
        return []

    def _accumulate_result_usage(self, payload: Mapping[str, Any]) -> None:
        source = _result_usage_delta(payload, self._model)
        if source is None:
            return
        self._saw_result_usage = True
        self._result_input_tokens = add_optional(
            self._result_input_tokens, _optional_nonnegative_integer(source, _INPUT_TOKENS_FIELD)
        )
        self._result_output_tokens = add_optional(
            self._result_output_tokens, _optional_nonnegative_integer(source, _OUTPUT_TOKENS_FIELD)
        )
        self._result_cache_read_tokens = add_optional(
            self._result_cache_read_tokens,
            _optional_nonnegative_integer(source, _CACHE_READ_TOKENS_FIELD),
        )
        self._result_cache_write_tokens = add_optional(
            self._result_cache_write_tokens,
            _optional_nonnegative_integer(source, _CACHE_WRITE_TOKENS_FIELD),
        )

    def _reported_usage(self) -> Usage | None:
        source = self._latest_assistant_usage
        token_fields = (
            _INPUT_TOKENS_FIELD,
            _OUTPUT_TOKENS_FIELD,
            _CACHE_READ_TOKENS_FIELD,
            _CACHE_WRITE_TOKENS_FIELD,
        )
        if not self._saw_result_usage and (
            source is None or not any(field in source for field in token_fields)
        ):
            return None
        input_delta = (
            self._result_input_tokens
            if self._saw_result_usage
            else _optional_nonnegative_integer(source, _INPUT_TOKENS_FIELD)
        )
        output_delta = (
            self._result_output_tokens
            if self._saw_result_usage
            else _optional_nonnegative_integer(source, _OUTPUT_TOKENS_FIELD)
        )
        cache_read_delta = (
            self._result_cache_read_tokens
            if self._saw_result_usage
            else _optional_nonnegative_integer(source, _CACHE_READ_TOKENS_FIELD)
        )
        cache_write_delta = (
            self._result_cache_write_tokens
            if self._saw_result_usage
            else _optional_nonnegative_integer(source, _CACHE_WRITE_TOKENS_FIELD)
        )
        uncached_input_tokens = add_optional(self._initial_usage.uncached_input_tokens, input_delta)
        output_tokens = add_optional(self._initial_usage.output_tokens, output_delta)
        cache_read_tokens = add_optional(self._initial_usage.cache_read_tokens, cache_read_delta)
        cache_write_tokens = add_optional(self._initial_usage.cache_write_tokens, cache_write_delta)
        priced = self._latest_cost_basis == _LIST_COST_BASIS and self._latest_cost is not None
        context_window = (
            self._latest_model_usage.get(_CONTEXT_WINDOW_FIELD)
            if self._latest_model_usage is not None
            else None
        )
        context_tokens = _context_tokens(self._latest_assistant_usage)
        context_pct = (
            context_tokens / context_window * PERCENT
            if context_tokens is not None
            and isinstance(context_window, int | float)
            and not isinstance(context_window, bool)
            and context_window > 0
            else None
        )
        return Usage(
            uncached_input_tokens=uncached_input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=self._latest_cost if priced else None,
            cost_basis="api_equivalent" if priced else "unknown",
            context_used_pct=context_pct,
        )

    def _usage_signals(self, *, assume_consumption: bool = False) -> list[UsageReported]:
        usage = self._reported_usage()
        if usage is not None:
            return [UsageReported(usage)]
        if assume_consumption or self._session_started or self._saw_assistant_event:
            return [UsageReported(self._usage_gap())]
        return []

    def _usage_gap(self) -> Usage:
        return Usage(
            uncached_input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            cost_usd=self._initial_usage.cost_usd,
            cost_basis=self._initial_usage.cost_basis,
            context_used_pct=self._initial_usage.context_used_pct,
        )

    def _fail(self, payload: Mapping[str, Any], stderr: str) -> list[AgentSignal]:
        failure = self._classify_failure(payload, stderr)
        if failure is None:
            failure = (FailureKind.UNKNOWN, _UNKNOWN_ERROR_MESSAGE, None)
        kind, message, resets_at = failure
        if kind is FailureKind.QUOTA and resets_at is None:
            resets_at = _latest_reset(self._latest_quota)
        if kind is FailureKind.QUOTA and resets_at is None:
            resets_at = self._reset_time.parse(message)
        return [*self._usage_signals(), TurnFailed(kind, message, resets_at)]

    def _classify_failure(
        self, payload: Mapping[str, Any] | None, stderr: str
    ) -> tuple[FailureKind, str, datetime | None] | None:
        status = _api_error_status(payload)
        message = _error_message(payload, stderr)
        lowered = f"{message} {stderr}".lower()
        tag_match = _ERROR_TAG_PATTERN.search(stderr)
        code = tag_match.group("code").lower() if tag_match is not None else ""
        terminal_reason = payload.get(_TERMINAL_REASON_FIELD) if payload is not None else None
        lowered = f"{lowered} {terminal_reason or ''} {code}"
        classified = _classify_status(status, message, self._reset_time)
        if status == STATUS_PAYMENT_REQUIRED:
            return classified
        if classified is None:
            classified = _classify_text(lowered, message, stderr)
        if classified is None and _contains(lowered, _CONTEXT_ERROR_TEXTS):
            self._context_exceeded = True
            classified = FailureKind.OTHER, message or stderr.strip(), None
        if classified is None and (status is not None or message or stderr):
            classified = FailureKind.UNKNOWN, message or stderr.strip(), None
        if classified is not None and classified[0] is FailureKind.QUOTA:
            kind, failure_message, resets_at = classified
            return kind, failure_message, resets_at or self._reset_time.parse(message)
        return classified

    def _compaction_problem(self) -> str | None:
        limit = self._compact_limit_tokens
        if limit is None:
            return None
        if not self._did_compact:
            return _INVALID_COMPACTION_MESSAGE
        if self._malformed_compaction_metadata:
            return _MALFORMED_COMPACTION_MESSAGE
        post_tokens = self._post_compaction_tokens
        if post_tokens is None or post_tokens >= limit:
            return _INEFFECTIVE_COMPACTION_MESSAGE.format(post_tokens=post_tokens, limit=limit)
        return None


class ClaudeTurnSequence(TurnProcessSequence):
    """Build one turn's prompt, compaction and continuation processes."""

    def __init__(
        self,
        spec: TurnSpec,
        *,
        clock: Clock,
        reset_time: ResetTimeParser,
        compact_at_pct: float,
        args: Sequence[str],
        environment: Callable[[TurnSpec], Mapping[str, str]],
        session_id: str | None = None,
    ) -> None:
        self._clock = clock
        self._reset_time = reset_time
        self._limit = compact_limit_tokens(spec.rung.model, compact_at_pct)
        self._args = tuple(args)
        self._environment = environment
        self._session_id = spec.session_id or session_id or str(uuid.uuid4())
        self._session_exists = spec.session_id is not None
        self._usage = spec.usage
        self._marker = spec.turn_dir / f"{spec.role}{COMPACT_MARKER_SUFFIX}"
        self._state = spec.turn_dir / f"{spec.role}{COMPACT_STATE_SUFFIX}"
        self._then: str | None = None
        self._recovered = False
        self._parser: ClaudeTurnParser | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    def start(self, spec: TurnSpec) -> TurnProcess:
        self._marker.unlink(missing_ok=True)
        if self._session_exists and self._transcript_tokens(spec) >= self._limit:
            return self._compact(spec, then=spec.prompt)
        return self._process(spec, spec.prompt)

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        self._remember_usage(previous.signals)
        if self._then is not None:
            prompt, self._then = self._then, None
            compacted = isinstance(_terminal(previous.signals), TurnEnded)
            return self._process(spec, prompt) if compacted else None
        if self._marker.exists():
            self._marker.unlink()
            return self._compact(spec, then=CONTINUE_PROMPT)
        if self._parser is not None and self._parser.context_exceeded and not self._recovered:
            self._recovered = True
            return self._compact(spec, then=spec.prompt)
        return None

    def _compact(self, spec: TurnSpec, *, then: str) -> TurnProcess:
        self._then = then
        return self._process(spec, _COMPACT_PROMPT, compact=True)

    def _process(self, spec: TurnSpec, prompt: str, *, compact: bool = False) -> TurnProcess:
        session_flag = RESUME_FLAG if self._session_exists else SESSION_ID_FLAG
        self._session_exists = True
        argv: list[str] = [CLAUDE_COMMAND, PRINT_FLAG, prompt, session_flag, self._session_id]
        argv.extend([OUTPUT_FORMAT_FLAG, _STREAM_JSON, _VERBOSE_FLAG, MODEL_FLAG, spec.rung.model])
        if spec.rung.effort is not None:
            argv.extend([EFFORT_FLAG, spec.rung.effort])
        for directory in _unique_paths((spec.run_dir, *spec.add_dirs)):
            argv.extend([ADD_DIR_FLAG, str(directory)])
        argv.extend([SETTINGS_FLAG, self._hook_settings()])
        argv.extend(self._args)
        self._parser = ClaudeTurnParser(
            session_id=self._session_id,
            model=spec.rung.model,
            initial_usage=self._usage,
            clock=self._clock,
            reset_time=self._reset_time,
            compact_limit_tokens=self._limit if compact else None,
        )
        return TurnProcess(tuple(argv), spec.cwd, self._environment(spec), self._parser)

    def _hook_settings(self) -> str:
        command = module_command(HOOK_MODULE, str(self._state), str(self._marker), str(self._limit))
        hook = {_HOOK_TYPE_FIELD: _COMMAND_HOOK_TYPE, _HOOK_COMMAND_FIELD: command}
        matcher = {_HOOK_MATCHER_FIELD: _ANY_TOOL_MATCHER, _HOOKS_FIELD: [hook]}
        return json.dumps({_HOOKS_FIELD: {_POST_TOOL_USE_EVENT: [matcher]}})

    def _transcript_tokens(self, spec: TurnSpec) -> int:
        environment = self._environment(spec)
        config_dir = environment.get(CLAUDE_CONFIG_DIR_ENV_VAR)
        root = Path(config_dir) if config_dir else Path(environment[HOME_ENV_VAR]) / CLAUDE_DIRNAME
        transcripts = sorted(
            (root / PROJECTS_DIRNAME).glob(f"*/{self._session_id}{TRANSCRIPT_SUFFIX}"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if not transcripts:
            return 0
        return transcript_context_tokens(read_transcript(transcripts[-1]))

    def _remember_usage(self, signals: Sequence[AgentSignal]) -> None:
        usages = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
        if usages:
            self._usage = usages[-1]


def _empty_usage() -> Usage:
    return Usage(0, 0, 0, 0, None, "unknown", None)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _optional_nonnegative_integer(payload: Mapping[str, Any] | None, key: str) -> int | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if type(value) is int and value >= 0 else None


def _tool_detail(value: object) -> str:
    if isinstance(value, str):
        detail = value
    elif isinstance(value, Mapping):
        detail = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        detail = ""
    return detail[:TOOL_DETAIL_MAX_CHARS]


def _is_error_result(payload: Mapping[str, Any]) -> bool:
    if payload.get(_IS_ERROR_FIELD) is True:
        return True
    if _api_error_status(payload) is not None:
        return True
    terminal_reason = payload.get(_TERMINAL_REASON_FIELD)
    if isinstance(terminal_reason, str) and terminal_reason.lower() in _ERROR_TERMINAL_REASONS:
        return True
    return payload.get(_SUBTYPE_FIELD) not in (None, _SUCCESS_SUBTYPE) and isinstance(
        payload.get(_SUBTYPE_FIELD), str
    )


def _is_no_work_task_notification_result(payload: Mapping[str, Any], model: str) -> bool:
    origin = _mapping(payload.get(_ORIGIN_FIELD))
    usage = _mapping(payload.get(_USAGE_FIELD))
    if (
        origin is None
        or origin.get(_ORIGIN_KIND_FIELD) != _TASK_NOTIFICATION_ORIGIN_KIND
        or payload.get(_RESULT_FIELD) != _EMPTY_RESULT
        or type(payload.get(_RESULT_INDEX_FIELD)) is not int
        or payload.get(_RESULT_INDEX_FIELD) != 0
        or payload.get(_STOP_REASON_FIELD) is not None
        or payload.get(_TERMINAL_REASON_FIELD) is not None
        or usage is None
    ):
        return False
    zero_usage = all(
        key in usage
        and isinstance(usage[key], int | float)
        and not isinstance(usage[key], bool)
        and usage[key] == 0
        for key in (
            _INPUT_TOKENS_FIELD,
            _OUTPUT_TOKENS_FIELD,
            _CACHE_READ_TOKENS_FIELD,
            _CACHE_WRITE_TOKENS_FIELD,
        )
    )
    if not zero_usage:
        return False
    delta = _mapping(payload.get(_MODEL_USAGE_DELTA_FIELD))
    return delta is None or not _has_model_usage_delta(delta, model)


def _has_model_usage_delta(delta: Mapping[str, Any], model: str) -> bool:
    selected = _select_model_usage(delta, model)
    if selected is None:
        return False
    return any(
        isinstance(selected.get(field), int | float)
        and not isinstance(selected.get(field), bool)
        and selected[field] != 0
        for field in (
            _INPUT_TOKENS_DELTA_FIELD,
            _OUTPUT_TOKENS_DELTA_FIELD,
            _CACHE_READ_TOKENS_DELTA_FIELD,
            _CACHE_WRITE_TOKENS_DELTA_FIELD,
            _COST_USD_FIELD,
        )
    )


def _result_usage_delta(payload: Mapping[str, Any], model: str) -> Mapping[str, Any] | None:
    raw_delta = _mapping(payload.get(_MODEL_USAGE_DELTA_FIELD))
    selected_delta = _select_model_usage(raw_delta, model)
    if selected_delta is not None:
        translated = {
            _INPUT_TOKENS_FIELD: selected_delta.get(_INPUT_TOKENS_DELTA_FIELD),
            _OUTPUT_TOKENS_FIELD: selected_delta.get(_OUTPUT_TOKENS_DELTA_FIELD),
            _CACHE_READ_TOKENS_FIELD: selected_delta.get(_CACHE_READ_TOKENS_DELTA_FIELD),
            _CACHE_WRITE_TOKENS_FIELD: selected_delta.get(_CACHE_WRITE_TOKENS_DELTA_FIELD),
        }
        if any(value is not None for value in translated.values()):
            return translated
    usage = _mapping(payload.get(_USAGE_FIELD))
    token_fields = (
        _INPUT_TOKENS_FIELD,
        _OUTPUT_TOKENS_FIELD,
        _CACHE_READ_TOKENS_FIELD,
        _CACHE_WRITE_TOKENS_FIELD,
    )
    if usage is None or not any(field in usage for field in token_fields):
        return None
    return usage


def _api_error_status(payload: Mapping[str, Any] | None) -> int | None:
    if payload is None:
        return None
    value = payload.get(_API_ERROR_STATUS_FIELD)
    if (isinstance(value, int) and value in _ERROR_STATUSES) or (
        isinstance(value, int) and _UNAVAILABLE_STATUS_MIN <= value <= _UNAVAILABLE_STATUS_MAX
    ):
        return value
    error = _mapping(payload.get(_ERROR_FIELD))
    nested = error.get(_API_ERROR_STATUS_FIELD, error.get(_STATUS_FIELD)) if error else None
    return nested if isinstance(nested, int) else None


def _error_message(payload: Mapping[str, Any] | None, stderr: str) -> str:
    if payload is None:
        tag = _ERROR_TAG_PATTERN.search(stderr)
        return tag.group("code") if tag is not None else stderr.strip()
    parts: list[str] = []
    for key in (_RESULT_FIELD, _ERROR_FIELD):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            typed_value = cast("Mapping[str, object]", value)
            nested_message = typed_value.get(_MESSAGE_TEXT_FIELD)
            if isinstance(nested_message, str):
                parts.append(nested_message)
    errors = payload.get(_ERRORS_FIELD)
    if isinstance(errors, list):
        parts.extend(
            error
            for item in cast("list[object]", errors)
            if isinstance((error := _error_message_from_item(item)), str)
        )
    tag = _ERROR_TAG_PATTERN.search(stderr)
    if tag is not None:
        parts.append(tag.group("code"))
    return "; ".join(part for part in parts if part)


def _error_message_from_item(value: object) -> str | None:
    if isinstance(value, str):
        return value
    payload = _mapping(value)
    if payload is None:
        return None
    message = payload.get(_MESSAGE_TEXT_FIELD)
    return message if isinstance(message, str) else None


def _contains(value: str, fragments: Sequence[str]) -> bool:
    return any(fragment in value for fragment in fragments)


def _classify_status(
    status: int | None, message: str, reset_time: ResetTimeParser
) -> tuple[FailureKind, str, datetime | None] | None:
    detail = message or _STATUS_MESSAGE.format(status=status)
    if status == STATUS_PAYMENT_REQUIRED:
        return FailureKind.AUTH, detail, None
    if status == STATUS_TOO_MANY_REQUESTS:
        return FailureKind.QUOTA, detail, reset_time.parse(message)
    if isinstance(status, int) and _UNAVAILABLE_STATUS_MIN <= status <= _UNAVAILABLE_STATUS_MAX:
        return FailureKind.UNAVAILABLE, detail, None
    if status in (STATUS_UNAUTHORIZED, STATUS_FORBIDDEN):
        return FailureKind.AUTH, detail, None
    return None


def _classify_text(
    lowered: str, message: str, stderr: str
) -> tuple[FailureKind, str, datetime | None] | None:
    if _contains(lowered, _QUOTA_ERROR_TEXTS):
        return FailureKind.QUOTA, message or stderr.strip(), None
    if _contains(lowered, _UNAVAILABLE_ERROR_TEXTS):
        return FailureKind.UNAVAILABLE, message or stderr.strip(), None
    if _contains(lowered, _AUTH_ERROR_TEXTS):
        return FailureKind.AUTH, message or stderr.strip(), None
    return None


def _stderr_message(exit_code: int, stderr: str, message: str) -> str:
    detail = message.strip() or stderr.strip() or EMPTY_OUTPUT
    return _STDERR_MESSAGE.format(exit_code=exit_code, message=detail)


def _context_tokens(usage: Mapping[str, Any] | None) -> int | None:
    if usage is None:
        return None
    token_counts = (
        usage.get(_INPUT_TOKENS_FIELD),
        usage.get(_CACHE_READ_TOKENS_FIELD),
        usage.get(_CACHE_WRITE_TOKENS_FIELD),
    )
    complete_counts: list[int] = []
    for token_count in token_counts:
        if type(token_count) is not int or token_count < 0:
            return None
        complete_counts.append(token_count)
    return sum(complete_counts)


def _select_model_usage(
    model_usage: Mapping[str, Any] | None, model: str
) -> Mapping[str, Any] | None:
    if model_usage is None:
        return None
    exact = _mapping(model_usage.get(model))
    if exact is not None:
        return exact
    if len(model_usage) == 1:
        return _mapping(next(iter(model_usage.values())))
    return None


def _latest_reset(windows: Sequence[QuotaWindow]) -> datetime | None:
    resets = [window.resets_at for window in windows if window.resets_at is not None]
    return min(resets) if resets else None


def _terminal(signals: Sequence[AgentSignal]) -> TurnEnded | TurnFailed | None:
    return next(
        (signal for signal in reversed(signals) if isinstance(signal, (TurnEnded, TurnFailed))),
        None,
    )


def _unique_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        normalized = path.resolve()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path)
    return tuple(unique)
