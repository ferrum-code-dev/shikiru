"""Codex JSONL turn parser and process sequence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from shikiru.agents.contracts import (
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
)
from shikiru.agents.sdk.turn import (
    EMPTY_OUTPUT,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
)
from shikiru.plugins.agents.codex.home import codex_home
from shikiru.plugins.agents.codex.options import (
    AUTO_COMPACT_LIMIT_CONFIG,
    CODEX_COMMAND,
    CONFIG_FLAG,
    EXEC_SUBCOMMAND,
    JSON_FLAG,
    MODEL_FLAG,
    MODELS_CACHE_FILENAME,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    USAGE_EVENT_TYPE,
)
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from shikiru.plugins.agents.codex.sandbox import sandbox_config_args
from shikiru.plugins.agents.codex.usage import latest_token_count, rollout_usage

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

_TYPE_FIELD = "type"
_PHASE_START = "start"
_PHASE_PROMPT = "prompt"
_PHASE_RETRY = "retry"
_THREAD_STARTED = "thread.started"
_THREAD_ID_FIELD = "thread_id"
_ITEM_STARTED = "item.started"
_ITEM_COMPLETED = "item.completed"
_ITEM_FIELD = "item"
_ITEM_ID_FIELD = "id"
_ITEM_TYPE_FIELD = "type"
_ITEM_STATUS_FIELD = "status"
_EXIT_CODE_FIELD = "exit_code"
_FAILED_STATUS = "failed"
_AGENT_MESSAGE_TYPE = "agent_message"
_MESSAGE_TEXT_FIELD = "text"
_TURN_COMPLETED = "turn.completed"
_TURN_FAILED = "turn.failed"
_ERROR_TYPE = "error"
_ERROR_FIELD = "error"
_PAYLOAD_FIELD = "payload"
_USAGE_FIELD = "usage"
_INFO_FIELD = "info"
_INPUT_TOKENS_FIELD = "input_tokens"
_CACHED_INPUT_TOKENS_FIELD = "cached_input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_LAST_USAGE_FIELD = "last_token_usage"
_STATUS_FIELD = "status"
_CODE_FIELD = "code"
_MESSAGE_FIELD = "message"
_RESET_AT_FIELD = "resets_at"
_EVENT_MESSAGE_TYPE = "event_msg"
_COMPACTED_TYPE = "compacted"
_PRE_TOKENS_FIELD = "pre_tokens"
_POST_TOKENS_FIELD = "post_tokens"
_MODELS_FIELD = "models"
_MODEL_SLUG_FIELD = "slug"
_MODEL_CONTEXT_WINDOW_FIELD = "context_window"
_TURN_CONTEXT_TYPE = "turn_context"
_MODEL_FIELD = "model"
_CONTEXT_WINDOW_INFO_FIELD = "model_context_window"
_PERCENT = 100
_UNAVAILABLE_STATUS_MIN = 500
_UNAVAILABLE_STATUS_MAX = 599
_PAYMENT_REQUIRED_STATUS = 402
_RATE_LIMIT_STATUS = 429
_AUTH_STATUSES = (401, 403)
_CAPACITY_TEXT = ("at capacity",)
_UNAVAILABLE_TEXT = ("overloaded", "temporarily unavailable", "service unavailable")
_CONTEXT_TEXT = (
    "context_length_exceeded",
    "context exceeded",
    "context length",
    "context window",
    "too many tokens",
)
_CONTEXT_LENGTH_EXCEEDED_CODE = "context_length_exceeded"
_QUOTA_TEXT = ("usage_limit_reached", "usage limit", "rate_limit", "rate limit", "quota")
_AUTH_TEXT = ("authentication", "unauthorized", "invalid api key")
_UNKNOWN_ERROR_MESSAGE = "Codex turn ended without a structured result"
_MALFORMED_STREAM_MESSAGE = "Codex emitted malformed JSONL"
_ROLLOUT_GLOB = "rollout-*.jsonl"
_COMMAND_EXECUTION_TYPE = "command_execution"
_FILE_CHANGE_TYPE = "file_change"
_MCP_TOOL_CALL_TYPE = "mcp_tool_call"
_COMMAND_TOOL_NAME = "command"
_FILE_CHANGE_TOOL_NAME = "file_change"
_MCP_TOOL_NAME = "mcp_tool"
_ZERO_TOKENS = 0
_ZERO_EXIT_CODE = 0
_UNKNOWN_COST = "unknown"


class CodexTurnParser:
    """Map Codex JSONL events and its rollout into agent signals."""

    def __init__(
        self,
        *,
        session_id: str | None,
        initial_usage: Usage | None,
        home: Path,
        rollout_path: Path | None = None,
        clock: Clock,
        reset_time: ResetTimeParser,
    ) -> None:
        self._session_id = session_id
        self._initial_usage = initial_usage or Usage(
            _ZERO_TOKENS,
            _ZERO_TOKENS,
            _ZERO_TOKENS,
            _ZERO_TOKENS,
            None,
            _UNKNOWN_COST,
            None,
        )
        self._home = home
        self._rollout_override = rollout_path
        self._clock = clock
        self._reset_time = reset_time
        self._quota_probe = CodexQuotaProbe(home, clock, reset_time)
        self._latest_streamed_quota: tuple[QuotaWindow, ...] = ()
        self._tools: dict[str, str] = {}
        self._last_message = ""
        self._turn_usage: Mapping[str, Any] | None = None
        self._failure: Mapping[str, Any] | None = None
        self._malformed = False
        self._terminal = False
        self._did_complete = False
        self._context_exceeded = False
        self._rollout = rollout_path or _rollout_path(home, session_id)
        self._initial_compaction_count = (
            _compaction_count(_read_rollout(self._rollout)) if session_id is not None else 0
        )
        self._handlers = {
            _THREAD_STARTED: self._thread_started,
            _ITEM_STARTED: self._item_started,
            _ITEM_COMPLETED: self._item_completed,
            _TURN_COMPLETED: self._turn_completed,
            _TURN_FAILED: self._record_failure,
            _ERROR_TYPE: self._record_failure,
        }

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
            self._malformed = True
            return []
        if not isinstance(raw, Mapping):
            self._malformed = True
            return []
        payload = cast("Mapping[str, Any]", raw)
        handler = self._handlers.get(cast("str", payload.get(_TYPE_FIELD)))
        signals = handler(payload) if handler is not None else []
        windows = self._quota_probe.parse(line, self._clock.now())
        if windows:
            self._latest_streamed_quota = tuple(windows)
            signals.append(QuotaReported(tuple(windows)))
        return signals

    def _turn_completed(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._did_complete = True
        self._turn_usage = _mapping(payload.get(_USAGE_FIELD))
        message = payload.get(_MESSAGE_TEXT_FIELD)
        if isinstance(message, str) and message:
            self._last_message = message
        return []

    def _record_failure(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        error = payload.get(_ERROR_FIELD)
        self._failure = _mapping(error) or (
            {_MESSAGE_FIELD: error} if isinstance(error, str) else payload
        )
        return []

    def finish(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        if self._terminal:
            return []
        self._terminal = True
        if self._malformed:
            return [TurnFailed(FailureKind.UNKNOWN, _MALFORMED_STREAM_MESSAGE, None)]
        if self._rollout is None:
            self._rollout = _rollout_path(self._home, self._session_id)
        entries = _read_rollout(self._rollout)
        signals: list[AgentSignal] = list(
            _compaction_signals(entries, self._initial_compaction_count)
        )
        quota: Sequence[QuotaWindow] = self._latest_streamed_quota
        if not quota:
            quota = _quota_windows(
                self._rollout, entries, self._home, self._clock, self._reset_time
            )
            if quota:
                signals.append(QuotaReported(tuple(quota)))
        usage = _reported_usage(entries, self._turn_usage, self._initial_usage)
        if usage is not None:
            signals.append(UsageReported(usage))
        if self._failure is not None:
            signals.append(self._failed_signal(self._failure, stderr, quota))
        elif self._did_complete:
            signals.append(TurnEnded(self._last_message))
        else:
            failure = _classify_error(stderr, None, "")
            if failure is None:
                signals.append(
                    TurnFailed(
                        FailureKind.UNKNOWN,
                        f"{_UNKNOWN_ERROR_MESSAGE}; process exited with status {exit_code}; "
                        f"stderr tail: {stderr or EMPTY_OUTPUT}",
                        None,
                    )
                )
            else:
                self._context_exceeded = failure.context_exceeded
                signals.append(TurnFailed(failure.kind, stderr.strip(), None))
        return signals

    def _thread_started(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        session_id = payload.get(_THREAD_ID_FIELD)
        if not isinstance(session_id, str) or not session_id.strip():
            return []
        self._session_id = session_id
        self._rollout = self._rollout_override or _rollout_path(self._home, session_id)
        return [SessionStarted(session_id)]

    def _item_started(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        item = _mapping(payload.get(_ITEM_FIELD))
        if item is None:
            return []
        item_id = item.get(_ITEM_ID_FIELD)
        item_type = item.get(_ITEM_TYPE_FIELD)
        if not isinstance(item_id, str) or not isinstance(item_type, str):
            return []
        name = _tool_name(item_type)
        if name is None:
            return []
        self._tools[item_id] = name
        return [ToolStarted(name, _tool_detail(item))]

    def _item_completed(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        item = _mapping(payload.get(_ITEM_FIELD))
        if item is None:
            return []
        signals: list[AgentSignal] = []
        item_id = item.get(_ITEM_ID_FIELD)
        if isinstance(item_id, str):
            name = self._tools.pop(item_id, None)
            if name is not None:
                item_type = item.get(_ITEM_TYPE_FIELD)
                exit_code = (
                    _command_exit_code(item) if item_type == _COMMAND_EXECUTION_TYPE else None
                )
                outcome = _tool_outcome(item.get(_ITEM_STATUS_FIELD), exit_code)
                signals.append(ToolFinished(name, outcome, exit_code))
        if item.get(_ITEM_TYPE_FIELD) == _AGENT_MESSAGE_TYPE:
            text = item.get(_MESSAGE_TEXT_FIELD)
            if isinstance(text, str):
                self._last_message = text
                signals.append(Message(text))
        return signals

    def _failed_signal(
        self,
        payload: Mapping[str, Any],
        stderr: str,
        quota: Sequence[QuotaWindow],
    ) -> TurnFailed:
        message, status, code, reset_value = _error_details(payload)
        classification = _classify_error(message, status, code)
        error_reset = (
            reset_value
            if isinstance(reset_value, datetime)
            else self._reset_time.parse(str(reset_value))
            if reset_value is not None
            else None
        )
        reset = error_reset or _latest_reset(quota) or self._reset_time.parse(message)
        kind = FailureKind.UNKNOWN if classification is None else classification.kind
        self._context_exceeded = classification is not None and classification.context_exceeded
        detail = message or _UNKNOWN_ERROR_MESSAGE
        if stderr:
            detail = f"{detail}; stderr: {stderr.strip()}"
        return TurnFailed(kind, detail, reset if kind is FailureKind.QUOTA else None)


class CodexTurnSequence(TurnProcessSequence):
    """Build Codex prompt and one context-failure retry process."""

    def __init__(
        self,
        spec: TurnSpec,
        *,
        args: Sequence[str],
        environment: Callable[[TurnSpec], Mapping[str, str]],
        clock: Clock,
        reset_time: ResetTimeParser,
        home: Path | None = None,
        git_dirs: Sequence[Path],
        compact_at_pct: float,
        model_context_windows: Mapping[str, int] | None = None,
    ) -> None:
        self._spec = spec
        self._args = tuple(args)
        self._environment = environment
        self._clock = clock
        self._reset_time = reset_time
        self._home = home or codex_home()
        self._git_dirs = tuple(git_dirs)
        self._compact_at_pct = compact_at_pct
        self._session_id = spec.session_id
        self._phase = _PHASE_START
        self._usage = spec.usage
        self._parser: CodexTurnParser | None = None
        self._context_window = context_window(
            self._home,
            spec.rung.model,
            spec.session_id,
            fallback_context_windows=model_context_windows,
        )

    def start(self, spec: TurnSpec) -> TurnProcess:
        self._phase = _PHASE_PROMPT
        return self._process(spec)

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        self._remember_state(previous.signals)
        terminal = _terminal(previous.signals)
        if (
            self._phase == _PHASE_PROMPT
            and isinstance(terminal, TurnFailed)
            and self._parser is not None
            and self._parser.context_exceeded
        ):
            self._phase = _PHASE_RETRY
            return self._process(spec)
        return None

    def _process(self, spec: TurnSpec) -> TurnProcess:
        argv = [CODEX_COMMAND, EXEC_SUBCOMMAND]
        if self._session_id is not None:
            argv.extend([RESUME_SUBCOMMAND, self._session_id])
        argv.append(JSON_FLAG)
        argv.extend([MODEL_FLAG, spec.rung.model])
        if spec.rung.effort is not None:
            argv.extend([CONFIG_FLAG, f'{REASONING_EFFORT_CONFIG}="{spec.rung.effort}"'])
        token_limit = self._token_limit()
        if token_limit is not None:
            argv.extend([CONFIG_FLAG, f"{AUTO_COMPACT_LIMIT_CONFIG}={token_limit}"])
        roots = _unique_paths((spec.run_dir, *self._git_dirs, *spec.add_dirs))
        argv.extend(sandbox_config_args(roots))
        argv.extend(self._args)
        argv.append(spec.prompt)
        parser = CodexTurnParser(
            session_id=self._session_id,
            initial_usage=self._usage,
            home=self._home,
            clock=self._clock,
            reset_time=self._reset_time,
        )
        self._parser = parser
        return TurnProcess(tuple(argv), spec.cwd, self._environment(spec), parser)

    def _token_limit(self) -> int | None:
        if self._context_window is None:
            return None
        return int(self._context_window * self._compact_at_pct / _PERCENT)

    def _remember_state(self, signals: Sequence[AgentSignal]) -> None:
        session_ids = [
            signal.session_id for signal in signals if isinstance(signal, SessionStarted)
        ]
        if session_ids:
            self._session_id = session_ids[-1]
        usages = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
        if usages:
            self._usage = usages[-1]


def context_window(
    home: Path,
    model: str,
    session_id: str | None = None,
    *,
    fallback_context_windows: Mapping[str, int] | None = None,
) -> int | None:
    """Return the observed or cached window, then a validated bundled-catalog window."""
    observed = _observed_window(_read_rollout(_rollout_path(home, session_id)), model)
    if observed is not None:
        return observed
    cached = _cached_context_window(home, model)
    if cached is not None:
        return cached
    return None if fallback_context_windows is None else fallback_context_windows.get(model)


def _observed_window(entries: Sequence[Mapping[str, Any]], model: str) -> int | None:
    current_model: object = None
    window: int | None = None
    window_model: object = None
    for entry in entries:
        payload = _mapping(entry.get(_PAYLOAD_FIELD))
        if payload is None:
            continue
        if entry.get(_TYPE_FIELD) == _TURN_CONTEXT_TYPE:
            current_model = payload.get(_MODEL_FIELD)
        elif payload.get(_TYPE_FIELD) == USAGE_EVENT_TYPE:
            info = _mapping(payload.get(_INFO_FIELD))
            value = info.get(_CONTEXT_WINDOW_INFO_FIELD) if info is not None else None
            if isinstance(value, int):
                window, window_model = value, current_model
    return window if window_model == model else None


def _cached_context_window(home: Path, model: str) -> int | None:
    path = home / MODELS_CACHE_FILENAME
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        return None
    raw_mapping = cast("Mapping[str, Any]", raw)
    models = raw_mapping.get(_MODELS_FIELD)
    if not isinstance(models, list):
        return None
    for item in cast("list[object]", models):
        entry = _mapping(item)
        if entry is None or entry.get(_MODEL_SLUG_FIELD) != model:
            continue
        value = entry.get(_MODEL_CONTEXT_WINDOW_FIELD)
        return value if isinstance(value, int) and value > 0 else None
    return None


def _rollout_path(home: Path, session_id: str | None) -> Path | None:
    if session_id is None:
        return None
    sessions = home / "sessions"
    matches = list(sessions.rglob(f"rollout-*-{session_id}.jsonl")) if sessions.is_dir() else []
    return max(matches, key=lambda path: path.stat().st_mtime_ns) if matches else None


def _read_rollout(path: Path | None) -> list[Mapping[str, Any]]:
    if path is None or not path.is_file():
        return []
    entries: list[Mapping[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, Mapping):
            entries.append(cast("Mapping[str, Any]", raw))
    return entries


def _compaction_count(entries: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for entry in entries if _rollout_type(entry) == _COMPACTED_TYPE)


def _compaction_signals(
    entries: Sequence[Mapping[str, Any]], previous_count: int
) -> list[Compacted]:
    compacted = [
        (index, entry)
        for index, entry in enumerate(entries)
        if _rollout_type(entry) == _COMPACTED_TYPE
    ]
    return [
        _compaction_signal(entries, index, entry) for index, entry in compacted[previous_count:]
    ]


def _compaction_signal(
    entries: Sequence[Mapping[str, Any]], index: int, entry: Mapping[str, Any]
) -> Compacted:
    payload = _mapping(entry.get(_PAYLOAD_FIELD)) or entry
    pre_tokens = _optional_integer(payload, _PRE_TOKENS_FIELD)
    post_tokens = _optional_integer(payload, _POST_TOKENS_FIELD)
    return Compacted(
        pre_tokens=(
            pre_tokens
            if pre_tokens is not None
            else _nearby_token_count(entries, index, before=True)
        ),
        post_tokens=(
            post_tokens
            if post_tokens is not None
            else _nearby_token_count(entries, index, before=False)
        ),
    )


def _nearby_token_count(
    entries: Sequence[Mapping[str, Any]], compacted_index: int, *, before: bool
) -> int | None:
    candidates = (
        reversed(entries[:compacted_index]) if before else iter(entries[compacted_index + 1 :])
    )
    for entry in candidates:
        if _rollout_type(entry) != USAGE_EVENT_TYPE:
            continue
        payload = _mapping(entry.get(_PAYLOAD_FIELD))
        info = _mapping(payload.get(_INFO_FIELD)) if payload is not None else None
        last_usage = _mapping(info.get(_LAST_USAGE_FIELD)) if info is not None else None
        value = _optional_integer(last_usage, _INPUT_TOKENS_FIELD) if last_usage else None
        if value is not None and (before or value > _ZERO_TOKENS):
            return value
    return None


def _rollout_type(entry: Mapping[str, Any]) -> object:
    if entry.get(_TYPE_FIELD) != _EVENT_MESSAGE_TYPE:
        return entry.get(_TYPE_FIELD)
    payload = _mapping(entry.get(_PAYLOAD_FIELD))
    return payload.get(_TYPE_FIELD) if payload is not None else None


def _quota_windows(
    path: Path | None,
    entries: Sequence[Mapping[str, Any]],
    home: Path,
    clock: Clock,
    reset_time: ResetTimeParser,
) -> list[QuotaWindow]:
    if path is None or not entries:
        return []
    probe = CodexQuotaProbe(home, clock, reset_time)
    return probe.parse(path.read_text(encoding="utf-8"), clock.now())


def _reported_usage(
    entries: Sequence[Mapping[str, Any]],
    turn_usage: Mapping[str, Any] | None,
    previous: Usage,
) -> Usage | None:
    latest = latest_token_count(entries)
    if latest is not None:
        return rollout_usage(entries)
    token_fields = (
        _INPUT_TOKENS_FIELD,
        _OUTPUT_TOKENS_FIELD,
        _CACHED_INPUT_TOKENS_FIELD,
    )
    if turn_usage is not None and any(field in turn_usage for field in token_fields):
        return _usage_from_turn(turn_usage, previous)
    return None


def _usage_from_turn(payload: Mapping[str, Any], previous: Usage) -> Usage:
    def token_value(key: str, fallback: int) -> int:
        value = payload.get(key)
        return int(value) if isinstance(value, int | float) else fallback

    return Usage(
        input_tokens=token_value(_INPUT_TOKENS_FIELD, previous.input_tokens),
        output_tokens=token_value(_OUTPUT_TOKENS_FIELD, previous.output_tokens),
        cache_read_tokens=token_value(_CACHED_INPUT_TOKENS_FIELD, previous.cache_read_tokens),
        cache_write_tokens=previous.cache_write_tokens,
        cost_usd=None,
        cost_basis="unknown",
        context_used_pct=previous.context_used_pct,
    )


def _error_details(
    payload: Mapping[str, Any],
) -> tuple[str, int | None, str, object | None]:
    nested = payload.get(_MESSAGE_FIELD)
    if isinstance(nested, str):
        try:
            decoded = json.loads(nested)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            payload = cast("Mapping[str, Any]", decoded)
    message = payload.get(_MESSAGE_FIELD)
    status_value = payload.get(_STATUS_FIELD)
    status = status_value if isinstance(status_value, int) else None
    code = payload.get(_CODE_FIELD)
    message_text = message if isinstance(message, str) else ""
    code_text = code if isinstance(code, str) else ""
    reset_value = payload.get(_RESET_AT_FIELD)
    return message_text or code_text, status, code_text, reset_value


class _Classification(NamedTuple):
    kind: FailureKind
    context_exceeded: bool = False


def _classify_error(message: str, status: int | None, code: str) -> _Classification | None:
    text = f"{code} {message}".lower()
    if (
        status == _PAYMENT_REQUIRED_STATUS
        or status in _AUTH_STATUSES
        or any(token in text for token in _AUTH_TEXT)
    ):
        return _Classification(FailureKind.AUTH)
    if code.lower() != _CONTEXT_LENGTH_EXCEEDED_CODE and any(
        token in text for token in _CAPACITY_TEXT
    ):
        return _Classification(FailureKind.UNAVAILABLE)
    if status == _RATE_LIMIT_STATUS or any(token in text for token in _QUOTA_TEXT):
        return _Classification(FailureKind.QUOTA)
    if (status is not None and _UNAVAILABLE_STATUS_MIN <= status <= _UNAVAILABLE_STATUS_MAX) or any(
        token in text for token in _UNAVAILABLE_TEXT
    ):
        return _Classification(FailureKind.UNAVAILABLE)
    if any(token in text for token in _CONTEXT_TEXT):
        return _Classification(FailureKind.OTHER, context_exceeded=True)
    return None


def _latest_reset(windows: Sequence[QuotaWindow]) -> datetime | None:
    return next((window.resets_at for window in windows if window.resets_at is not None), None)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _optional_integer(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _tool_name(item_type: str) -> str | None:
    return {
        _COMMAND_EXECUTION_TYPE: _COMMAND_TOOL_NAME,
        _FILE_CHANGE_TYPE: _FILE_CHANGE_TOOL_NAME,
        _MCP_TOOL_CALL_TYPE: _MCP_TOOL_NAME,
    }.get(item_type)


def _tool_detail(item: Mapping[str, Any]) -> str:
    value = item.get("command")
    if isinstance(value, str):
        return value
    return ""


def _command_exit_code(item: Mapping[str, Any]) -> int | None:
    value = item.get(_EXIT_CODE_FIELD)
    return value if type(value) is int else None


def _tool_outcome(status: object, exit_code: int | None) -> ToolOutcome | None:
    if status == _FAILED_STATUS:
        return ToolOutcome.FAILURE
    if exit_code is None:
        return None
    return ToolOutcome.SUCCESS if exit_code == _ZERO_EXIT_CODE else ToolOutcome.FAILURE


def _unique_paths(paths: Sequence[Path | None]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path is None:
            continue
        normalized = path.resolve()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path)
    return tuple(unique)


def _terminal(signals: Sequence[AgentSignal]) -> TurnEnded | TurnFailed | None:
    return next(
        (signal for signal in reversed(signals) if isinstance(signal, (TurnEnded, TurnFailed))),
        None,
    )
