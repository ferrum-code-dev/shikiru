"""Muse Code `exec --json` turn parser."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    PERCENT,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    RUNTIME_DEFAULT_MODEL,
    TOOL_DETAIL_MAX_CHARS,
    AgentSignal,
    EvidenceFault,
    EvidenceRecovered,
    FailureKind,
    Message,
    QuotaReported,
    QuotaWindow,
    SessionStarted,
    ToolDetailReported,
    ToolFinished,
    ToolOutcome,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    UsageReported,
    is_exhausted,
)
from shikiru.agents.sdk.turn import (
    EMPTY_OUTPUT,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
    write_prompt_if_missing,
)
from shikiru.plugins.agents.muse.options import (
    EXEC_SUBCOMMAND,
    HARD_THRESHOLD_FLAG,
    HARD_THRESHOLD_MARGIN,
    JSON_FLAG,
    MODEL_FLAG,
    MUSE_COMMAND,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RECORD_STREAM_KEY,
    RUN_OUTPUT_DELTA_TYPE,
    RUN_TERMINAL_COMPLETED_TYPE,
    RUN_TERMINAL_FAILED_TYPE,
    RUN_USAGE_TYPE,
    SESSION_ID_FLAG,
    SOFT_THRESHOLD_FLAG,
    STREAM_ID_KEY,
    STREAM_KIND_KEY,
    STREAM_KIND_SESSION,
    TASK_OUTPUT_TYPE,
    TASK_PROPOSED_TYPE,
    TASK_SCHEDULED_TYPE,
    TASK_STARTED_TYPE,
    TOOL_IDEMPOTENCY_KEY_PREFIX,
    TOOL_RESULT_TYPE,
    TOOL_TASK_KIND_PREFIX,
)
from shikiru.plugins.agents.muse.session_log import (
    SessionLogError,
    SessionLogProgress,
    mark_of,
    muse_home,
    progress_since,
    read_session_log,
    start_mark,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shikiru.agents.contracts import TurnSpec
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock
    from shikiru.plugins.agents.muse.session_log import SessionLogMark

_MISSING_LOG_MESSAGE = "Muse session log is missing; compactions and context usage are unknown"
_UNAVAILABLE_AT_START_MESSAGE = "Muse session log was missing or unreadable before the turn started"
_NO_PROGRESS = SessionLogProgress(compactions=[], latest_prompt_tokens=None)
_TEXT_KEY = "text"
_REASON_KEY = "reason"
_EVENT_KEY = "event"
_TASK_KIND_KEY = "task_kind"
_CORRELATION_KEY = "correlation_facts"
_TOOL_NAME_KEY = "tool_name"
_OUTCOME_KEY = "outcome"
_EXIT_CODE_KEY = "exit_code"
_TASK_ID_KEY = "task_id"
_CHUNK_KEY = "chunk"
_BACKGROUND_TARGET_KEY = "background_target"
_TOOL_CALL_ID_KEY = "tool_call_id"
_IDEMPOTENCY_KEY_KEY = "idempotency_key"
_CALL_ID_KEY = "call_id"
_DETAIL_KEYS = ("command", "description")
_MODELS_KEY = "models"
_INPUT_TOKENS_KEY = "input_tokens"
_OUTPUT_TOKENS_KEY = "output_tokens"
_CACHED_TOKENS_KEY = "cached_tokens"
_ALLOWANCE_KEY = "allowance"
_AS_OF_KEY = "as_of_ms"
_WINDOW_KEY = "window"
_WEEKLY_KEY = "weekly"
_USED_PERCENT_KEY = "used_percent"
_RESETS_AT_KEY = "resets_at_ms"
_TOOL_OUTCOME_SUCCESS = "success"
_MILLISECONDS_PER_SECOND = 1000
_MALFORMED_MESSAGE = "Muse emitted malformed JSONL"
_NO_TERMINAL_MESSAGE = "Muse turn ended without a terminal record"
_AUTH_TEXTS = ("rejected", "missing meta credentials", "not logged in")
_RATE_LIMIT_STATUS_PATTERN = re.compile(r"\b429\b")
_QUOTA_TEXTS = ("quota", "usage limit")
_OTHER_TEXTS = ("does not exist or you lack access", "did not reach a terminal state")
_UNAVAILABLE_TEXTS = ("overloaded", "temporarily unavailable", "service unavailable")
_CONTEXT_TEXTS = (
    "context length",
    "context window",
    "context_length_exceeded",
    "context exceeded",
    "too many tokens",
)
_SERVER_ERROR_STATUS_PATTERN = re.compile(
    r"\b(?:HTTP|status)(?:\s+code)?[\s:=/]*5\d{2}\b"
    r"|\b5\d{2}\s+(?:internal server error|bad gateway|service unavailable|gateway timeout)\b",
    re.IGNORECASE,
)
_RESETS_AT_PATTERN = re.compile(
    r"resets at\s+(?P<instant>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:\d{2})?)",
    re.IGNORECASE,
)
_LONGEST_QUOTA_WINDOW = timedelta(days=7)


class _MalformedRecordError(Exception):
    pass


class _TurnTotals(NamedTuple):
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class _FailedRun(NamedTuple):
    reason: str


@dataclass
class _PendingTool:
    task_id: str
    name: str
    detail: str = ""
    call_id: str | None = None


@dataclass(frozen=True)
class ContextWindow:
    """A model's context window in tokens, or the reason it is unknown.

    `unknown_reason` is `None` when no window is tracked at all, which reports no fault.
    """

    tokens: int | None
    unknown_reason: str | None = None


@dataclass(frozen=True)
class SessionWatch:
    """Where a turn process finds its session log, and what the log held before the process ran.

    A `None` mark means the log was unreadable before the process ran, so the turn cannot tell
    what it added: it reports an evidence fault and keeps its own result.
    """

    home: Path
    session_id: str | None
    context_window: ContextWindow
    mark: SessionLogMark | None


class MuseTurnParser:
    """Map Muse `exec --json` records into agent signals, holding the terminal until finish."""

    def __init__(
        self,
        clock: Clock,
        reset_time: ResetTimeParser,
        watch: SessionWatch,
        initial_usage: Usage | None = None,
    ) -> None:
        self._clock = clock
        self._reset_time = reset_time
        self._watch = watch
        self._initial_usage = initial_usage or Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None)
        self._session_id: str | None = None
        self._totals: _TurnTotals | None = None
        self._pending_tools: list[_PendingTool] = []
        self._windows: tuple[QuotaWindow, ...] = ()
        self._terminal: TurnEnded | _FailedRun | None = None
        self._malformed = False
        self._handlers: dict[str, Callable[[Mapping[str, Any]], list[AgentSignal]]] = {
            RUN_OUTPUT_DELTA_TYPE: self._output_delta,
            TASK_PROPOSED_TYPE: self._task_proposed,
            TASK_SCHEDULED_TYPE: self._task_scheduled,
            TASK_STARTED_TYPE: self._task_started,
            TASK_OUTPUT_TYPE: self._task_output,
            TOOL_RESULT_TYPE: self._tool_result,
            RUN_USAGE_TYPE: self._usage,
            RUN_TERMINAL_COMPLETED_TYPE: self._completed,
            RUN_TERMINAL_FAILED_TYPE: self._failed,
        }

    def feed(self, line: str) -> list[AgentSignal]:
        if not line.strip():
            return []
        try:
            record = _mapping(json.loads(line))
            session = self._session_signals(record)
        except json.JSONDecodeError, _MalformedRecordError:
            self._malformed = True
            return []
        try:
            payload = self._payload_signals(record)
        except _MalformedRecordError:
            self._malformed = True
            return session
        return [*session, *payload]

    @property
    def context_exceeded(self) -> bool:
        """Return whether this process ended with a failure caused by a full context window."""
        return isinstance(self._terminal, _FailedRun) and _contains(
            self._terminal.reason.lower(), _CONTEXT_TEXTS
        )

    def finish(
        self, exit_code: int, stderr: str, *, interrupted: bool = False
    ) -> list[AgentSignal]:
        unfinished: list[AgentSignal] = [
            ToolFinished(tool.name, None, None) for tool in self._pending_tools
        ]
        if self._malformed and not interrupted:
            return [*unfinished, _malformed_failure()]
        return [
            *unfinished,
            *self._reports(),
            *self._terminal_signals(exit_code, stderr, interrupted=interrupted),
        ]

    def _session_signals(self, record: Mapping[str, Any]) -> list[AgentSignal]:
        stream = _mapping(record.get(RECORD_STREAM_KEY))
        if self._session_id is not None or stream.get(STREAM_KIND_KEY) != STREAM_KIND_SESSION:
            return []
        self._session_id = _text(stream, STREAM_ID_KEY)
        return [SessionStarted(self._session_id)]

    def _payload_signals(self, record: Mapping[str, Any]) -> list[AgentSignal]:
        payload = _mapping(record.get(RECORD_PAYLOAD_KEY))
        payload_type = record.get(RECORD_PAYLOAD_TYPE_KEY)
        handler = self._handlers.get(payload_type) if isinstance(payload_type, str) else None
        return [] if handler is None else handler(payload)

    def _output_delta(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        return [Message(_text(payload, _TEXT_KEY))]

    def _task_proposed(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        task_kind = _text(_mapping(payload.get(_EVENT_KEY)), _TASK_KIND_KEY)
        if not task_kind.startswith(TOOL_TASK_KIND_PREFIX):
            return []
        name = task_kind.removeprefix(TOOL_TASK_KIND_PREFIX)
        self._pending_tools.append(_PendingTool(_text(payload, _TASK_ID_KEY), name))
        return [ToolStarted(name, "")]

    def _task_started(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        pending = next(
            (tool for tool in self._pending_tools if tool.task_id == payload.get(_TASK_ID_KEY)),
            None,
        )
        if pending is None:
            return []
        event = _mapping(payload.get(_EVENT_KEY))
        background_target = event.get(_BACKGROUND_TARGET_KEY)
        if background_target is None:
            return []
        call_id = _optional_text(_mapping(background_target), _TOOL_CALL_ID_KEY)
        if call_id and pending.call_id is None:
            pending.call_id = call_id
        return []

    def _task_scheduled(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        pending = next(
            (tool for tool in self._pending_tools if tool.task_id == payload.get(_TASK_ID_KEY)),
            None,
        )
        if pending is None:
            return []
        event = _mapping(payload.get(_EVENT_KEY))
        idempotency_key = _optional_text(event, _IDEMPOTENCY_KEY_KEY)
        if idempotency_key is None or not idempotency_key.startswith(TOOL_IDEMPOTENCY_KEY_PREFIX):
            return []
        call_id = idempotency_key.removeprefix(TOOL_IDEMPOTENCY_KEY_PREFIX)
        if call_id and pending.call_id is None:
            pending.call_id = call_id
        return []

    def _task_output(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        pending = next(
            (tool for tool in self._pending_tools if tool.task_id == payload.get(_TASK_ID_KEY)),
            None,
        )
        if pending is None or pending.detail:
            return []
        chunk = _text(_mapping(payload.get(_EVENT_KEY)), _CHUNK_KEY)
        pending.detail = _tool_detail(chunk)
        return [ToolDetailReported(pending.name, pending.detail)] if pending.detail else []

    def _tool_result(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        facts_value = payload.get(_CORRELATION_KEY)
        if facts_value is None:
            text = _text(payload, _TEXT_KEY)
            call_id = _optional_text(payload, _CALL_ID_KEY)
            pending = self._pending_tool_for_call_id(call_id)
            if pending is not None:
                if pending.detail:
                    return []
                pending.detail = _tool_detail(text)
                return [ToolDetailReported(pending.name, pending.detail)] if pending.detail else []
            detail = _tool_detail(text)
            return [ToolDetailReported(None, detail)] if detail else []
        facts = _mapping(facts_value)
        name = _text(facts, _TOOL_NAME_KEY)
        text = _text(payload, _TEXT_KEY)
        exit_code = _tool_exit_code(text)
        pending = self._take_pending(name)
        signals: list[AgentSignal] = []
        if pending is None:
            signals.append(ToolStarted(name, ""))
            detail = _tool_detail(text)
        else:
            detail = "" if pending.detail else _tool_detail(text)
        signals.append(
            ToolFinished(
                name,
                _tool_outcome(facts.get(_OUTCOME_KEY), exit_code),
                exit_code,
                detail,
            )
        )
        return signals

    def _take_pending(self, name: str) -> _PendingTool | None:
        index = next(
            (position for position, tool in enumerate(self._pending_tools) if tool.name == name),
            None,
        )
        return None if index is None else self._pending_tools.pop(index)

    def _pending_tool_for_call_id(self, call_id: str | None) -> _PendingTool | None:
        if not call_id:
            return None
        matches = [tool for tool in self._pending_tools if tool.call_id == call_id]
        return matches[0] if len(matches) == 1 else None

    def _usage(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._totals = _turn_totals(_models(payload.get(_MODELS_KEY)))
        windows = _allowance_windows(payload.get(_ALLOWANCE_KEY))
        if not windows:
            return []
        self._windows = windows
        return [QuotaReported(windows)]

    def _reports(self) -> list[AgentSignal]:
        if self._watch.mark is None:
            try:
                records = (
                    None
                    if self._watch.session_id is None
                    else read_session_log(self._watch.home, self._watch.session_id)
                )
                recovery_mark = None if records is None else mark_of(records)
            except SessionLogError:
                recovery_mark = None
            signals: list[AgentSignal] = [EvidenceFault(_UNAVAILABLE_AT_START_MESSAGE)]
            if recovery_mark is not None:
                signals.append(EvidenceRecovered())
            return [*signals, *self._usage_reports(_NO_PROGRESS)]
        try:
            progress = self._progress()
        except SessionLogError as error:
            return [EvidenceFault(str(error)), *self._usage_reports(_NO_PROGRESS)]
        recovered = [EvidenceRecovered()] if self._session_id is not None else []
        return [*recovered, *progress.compactions, *self._usage_reports(progress)]

    def _usage_reports(self, progress: SessionLogProgress) -> list[AgentSignal]:
        if self._totals is None:
            return []
        usage = self._cumulative_usage(self._totals, progress.latest_prompt_tokens)
        return [*self._window_faults(), UsageReported(usage)]

    def _window_faults(self) -> list[AgentSignal]:
        reason = self._watch.context_window.unknown_reason
        return [] if reason is None else [EvidenceFault(reason)]

    def _progress(self) -> SessionLogProgress:
        if self._session_id is None:
            return _NO_PROGRESS
        mark = self._watch.mark
        if mark is None:
            raise SessionLogError(_UNAVAILABLE_AT_START_MESSAGE)
        records = read_session_log(self._watch.home, self._session_id)
        if records is None:
            raise SessionLogError(_MISSING_LOG_MESSAGE)
        return progress_since(records, mark)

    def _completed(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._terminal = TurnEnded(_text(payload, _TEXT_KEY))
        return []

    def _failed(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        self._terminal = _FailedRun(_text(payload, _REASON_KEY))
        return []

    def _terminal_signals(
        self, exit_code: int, stderr: str, *, interrupted: bool
    ) -> list[AgentSignal]:
        if isinstance(self._terminal, TurnEnded):
            return [self._terminal]
        if isinstance(self._terminal, _FailedRun):
            return [] if interrupted else [self._classify(self._terminal.reason)]
        if interrupted:
            return []
        tail = stderr or EMPTY_OUTPUT
        return [
            TurnFailed(
                FailureKind.UNKNOWN,
                f"{_NO_TERMINAL_MESSAGE}; process exited with status {exit_code}; "
                f"stderr tail: {tail}",
                None,
            )
        ]

    def _classify(self, reason: str) -> TurnFailed:
        return classify_failure(reason, self._windows, self._clock.now(), self._reset_time)

    def _cumulative_usage(self, totals: _TurnTotals, latest_prompt_tokens: int | None) -> Usage:
        base = self._initial_usage
        return Usage(
            input_tokens=base.input_tokens + totals.input_tokens - totals.cached_tokens,
            output_tokens=base.output_tokens + totals.output_tokens,
            cache_read_tokens=base.cache_read_tokens + totals.cached_tokens,
            cache_write_tokens=base.cache_write_tokens,
            cost_usd=None,
            cost_basis=COST_BASIS_UNKNOWN,
            context_used_pct=context_pct(
                latest_prompt_tokens, self._watch.context_window.tokens, base.context_used_pct
            ),
        )


class MuseTurnSequence(TurnProcessSequence):
    """Build Muse's `exec --json` process and one retry after a context-window failure."""

    def __init__(
        self,
        *,
        args: Sequence[str],
        base_environment: Mapping[str, str],
        clock: Clock,
        reset_time: ResetTimeParser,
        user_home: Path,
        context_window: ContextWindow,
        compact_at_pct: float,
    ) -> None:
        self._args = tuple(args)
        self._base_environment = base_environment
        self._clock = clock
        self._reset_time = reset_time
        self._user_home = user_home
        self._context_window = context_window
        self._compact_at_pct = compact_at_pct
        self._session_id: str | None = None
        self._usage: Usage | None = None
        self._parser: MuseTurnParser | None = None
        self._retried = False

    def start(self, spec: TurnSpec) -> TurnProcess:
        self._session_id = spec.session_id
        self._usage = spec.usage
        self._retried = False
        return self._process(spec)

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        self._remember_state(previous.signals)
        if self._retried or self._parser is None or not self._parser.context_exceeded:
            return None
        self._retried = True
        return self._process(spec)

    def _process(self, spec: TurnSpec) -> TurnProcess:
        write_prompt_if_missing(spec.turn_dir / PROMPT_FILENAME, spec.prompt)
        environment = self._environment(spec)
        home = muse_home(environment, self._user_home)
        watch = SessionWatch(
            home=home,
            session_id=self._session_id,
            context_window=self._context_window,
            mark=start_mark(home, self._session_id),
        )
        parser = MuseTurnParser(self._clock, self._reset_time, watch, self._usage)
        self._parser = parser
        return TurnProcess(
            argv=self._argv(spec),
            cwd=spec.cwd,
            env=environment,
            parser=parser,
        )

    def _argv(self, spec: TurnSpec) -> tuple[str, ...]:
        argv = [MUSE_COMMAND, EXEC_SUBCOMMAND, JSON_FLAG]
        if self._session_id is not None:
            argv.extend((SESSION_ID_FLAG, self._session_id))
        if spec.rung.model != RUNTIME_DEFAULT_MODEL:
            argv.extend((MODEL_FLAG, spec.rung.model))
        if spec.rung.effort is not None:
            argv.extend((REASONING_EFFORT_FLAG, spec.rung.effort))
        soft, hard = compaction_thresholds(self._compact_at_pct)
        argv.extend((SOFT_THRESHOLD_FLAG, str(soft), HARD_THRESHOLD_FLAG, str(hard)))
        argv.extend(self._args)
        argv.extend((PROMPT_FILE_FLAG, str(spec.turn_dir / PROMPT_FILENAME)))
        return tuple(argv)

    def _environment(self, spec: TurnSpec) -> dict[str, str]:
        return {
            **self._base_environment,
            **spec.env,
            NO_AUTO_UPDATE_ENV_VAR: NO_AUTO_UPDATE_VALUE,
        }

    def _remember_state(self, signals: Sequence[AgentSignal]) -> None:
        for signal in signals:
            if isinstance(signal, SessionStarted):
                self._session_id = signal.session_id
            elif isinstance(signal, UsageReported):
                self._usage = signal.usage


def context_pct(
    prompt_tokens: int | None, window_tokens: int | None, previous: float | None
) -> float | None:
    """Return the share of the context window the latest prompt estimate fills.

    `previous` stands only when the window is known but this process has no estimate; an
    unknown window reports no share, so a previous turn's share is never carried over.
    """
    if window_tokens is None:
        return None
    if prompt_tokens is None:
        return previous
    return min(prompt_tokens / window_tokens * PERCENT, PERCENT)


def compaction_thresholds(compact_at_pct: float) -> tuple[float, float]:
    """Return Muse's soft and hard compaction thresholds as fractions of the context window."""
    soft = compact_at_pct / PERCENT
    return soft, soft + HARD_THRESHOLD_MARGIN


def classify_failure(
    reason: str,
    windows: Sequence[QuotaWindow],
    now: datetime,
    reset_time: ResetTimeParser,
) -> TurnFailed:
    """Classify a Muse failure text against the failed turn's allowance windows.

    A quota failure's reset is the latest future instant, within the longest quota window, of
    the exhausted windows' resets and of an ISO timestamp the text gives after `resets at`.
    """
    lowered = reason.lower()
    if _contains(lowered, _AUTH_TEXTS):
        return TurnFailed(FailureKind.AUTH, reason, None)
    if _is_quota_failure(reason, lowered, windows):
        return TurnFailed(FailureKind.QUOTA, reason, _quota_reset(reason, windows, now, reset_time))
    if _contains(lowered, _OTHER_TEXTS):
        return TurnFailed(FailureKind.OTHER, reason, None)
    if _contains(lowered, _UNAVAILABLE_TEXTS) or _SERVER_ERROR_STATUS_PATTERN.search(reason):
        return TurnFailed(FailureKind.UNAVAILABLE, reason, None)
    return TurnFailed(FailureKind.UNKNOWN, reason, None)


def _malformed_failure() -> TurnFailed:
    return TurnFailed(FailureKind.UNKNOWN, _MALFORMED_MESSAGE, None)


def _is_quota_failure(reason: str, lowered: str, windows: Sequence[QuotaWindow]) -> bool:
    rate_limited = _RATE_LIMIT_STATUS_PATTERN.search(reason) is not None
    return (rate_limited and _contains(lowered, _QUOTA_TEXTS)) or any(
        is_exhausted(window) for window in windows
    )


def _quota_reset(
    reason: str, windows: Sequence[QuotaWindow], now: datetime, reset_time: ResetTimeParser
) -> datetime | None:
    candidates = [window.resets_at for window in windows if is_exhausted(window)]
    candidates.append(_reset_from_text(reason, reset_time))
    horizon = now + _LONGEST_QUOTA_WINDOW
    plausible = [
        candidate
        for candidate in candidates
        if candidate is not None and now < candidate <= horizon
    ]
    return max(plausible, default=None)


def _reset_from_text(reason: str, reset_time: ResetTimeParser) -> datetime | None:
    match = _RESETS_AT_PATTERN.search(reason)
    return None if match is None else reset_time.parse(match.group("instant"))


def _contains(value: str, fragments: Sequence[str]) -> bool:
    return any(fragment in value for fragment in fragments)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _MalformedRecordError
    return cast("Mapping[str, Any]", value)


def _models(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _MalformedRecordError
    return cast("list[object]", value)


def _text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise _MalformedRecordError
    return value


def _optional_text(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _MalformedRecordError
    return value


def _count(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if type(value) is not int:
        raise _MalformedRecordError
    return value


def _optional_count(record: Mapping[str, Any], key: str) -> int | None:
    return None if record.get(key) is None else _count(record, key)


def _percent(record: Mapping[str, Any], key: str) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _MalformedRecordError
    try:
        percentage = float(value)
    except OverflowError as error:
        raise _MalformedRecordError from error
    if not isfinite(percentage):
        raise _MalformedRecordError
    return min(percentage, PERCENT)


def _from_milliseconds(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / _MILLISECONDS_PER_SECOND, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise _MalformedRecordError from error


def _turn_totals(models: Sequence[object]) -> _TurnTotals:
    totals = _TurnTotals(0, 0, 0)
    for model in models:
        entry = _mapping(model)
        totals = _TurnTotals(
            totals.input_tokens + _count(entry, _INPUT_TOKENS_KEY),
            totals.output_tokens + _count(entry, _OUTPUT_TOKENS_KEY),
            totals.cached_tokens + _count(entry, _CACHED_TOKENS_KEY),
        )
    return totals


def _allowance_windows(allowance: object) -> tuple[QuotaWindow, ...]:
    if allowance is None:
        return ()
    record = _mapping(allowance)
    observed_at = _from_milliseconds(_count(record, _AS_OF_KEY))
    windows: list[QuotaWindow] = []
    for key, name in (
        (_WINDOW_KEY, QUOTA_WINDOW_FIVE_HOUR),
        (_WEEKLY_KEY, QUOTA_WINDOW_SEVEN_DAY),
    ):
        window = record.get(key)
        if window is None:
            continue
        values = _mapping(window)
        resets_at = _optional_count(values, _RESETS_AT_KEY)
        windows.append(
            QuotaWindow(
                name=name,
                used_pct=_percent(values, _USED_PERCENT_KEY),
                resets_at=None if resets_at is None else _from_milliseconds(resets_at),
                observed_at=observed_at,
            )
        )
    return tuple(windows)


def _tool_outcome(outcome: object, exit_code: int | None) -> ToolOutcome | None:
    if exit_code is not None and exit_code != 0:
        return ToolOutcome.FAILURE
    return ToolOutcome.SUCCESS if outcome == _TOOL_OUTCOME_SUCCESS else None


def _tool_exit_code(text: str) -> int | None:
    details = _json_mapping(text)
    if details is None:
        return None
    exit_code = details.get(_EXIT_CODE_KEY)
    return exit_code if type(exit_code) is int else None


def _tool_detail(text: str) -> str:
    details = _json_mapping(text)
    source = text if details is None else _first_text(details, _DETAIL_KEYS)
    lines = source.strip().splitlines()
    return lines[0][:TOOL_DETAIL_MAX_CHARS] if lines else ""


def _json_mapping(text: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _first_text(record: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str):
            return value
    return ""
