"""Muse Code `exec --json` turn parser."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    PERCENT,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    AgentSignal,
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
    Usage,
    UsageReported,
    is_exhausted,
)
from shikiru.agents.sdk.turn import EMPTY_OUTPUT
from shikiru.plugins.agents.muse.options import (
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RECORD_STREAM_KEY,
    RUN_OUTPUT_DELTA_TYPE,
    RUN_TERMINAL_COMPLETED_TYPE,
    RUN_TERMINAL_FAILED_TYPE,
    RUN_USAGE_TYPE,
    STREAM_ID_KEY,
    STREAM_KIND_KEY,
    STREAM_KIND_SESSION,
    TASK_PROPOSED_TYPE,
    TOOL_RESULT_TYPE,
    TOOL_TASK_KIND_PREFIX,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

_TEXT_KEY = "text"
_REASON_KEY = "reason"
_EVENT_KEY = "event"
_TASK_KIND_KEY = "task_kind"
_CORRELATION_KEY = "correlation_facts"
_TOOL_NAME_KEY = "tool_name"
_OUTCOME_KEY = "outcome"
_EXIT_CODE_KEY = "exit_code"
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


class MuseTurnParser:
    """Map Muse `exec --json` records into agent signals, holding the terminal until finish."""

    def __init__(
        self,
        clock: Clock,
        reset_time: ResetTimeParser,
        initial_usage: Usage | None = None,
    ) -> None:
        self._clock = clock
        self._reset_time = reset_time
        self._initial_usage = initial_usage or Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None)
        self._session_started = False
        self._pending_tools: list[str] = []
        self._windows: tuple[QuotaWindow, ...] = ()
        self._terminal: TurnEnded | _FailedRun | None = None
        self._malformed = False
        self._handlers: dict[str, Callable[[Mapping[str, Any]], list[AgentSignal]]] = {
            RUN_OUTPUT_DELTA_TYPE: self._output_delta,
            TASK_PROPOSED_TYPE: self._task_proposed,
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

    def finish(
        self, exit_code: int, stderr: str, *, interrupted: bool = False
    ) -> list[AgentSignal]:
        unfinished: list[AgentSignal] = [
            ToolFinished(name, None, None) for name in self._pending_tools
        ]
        if self._malformed and not interrupted:
            return [*unfinished, TurnFailed(FailureKind.UNKNOWN, _MALFORMED_MESSAGE, None)]
        return [*unfinished, *self._terminal_signals(exit_code, stderr, interrupted=interrupted)]

    def _session_signals(self, record: Mapping[str, Any]) -> list[AgentSignal]:
        stream = _mapping(record.get(RECORD_STREAM_KEY))
        if self._session_started or stream.get(STREAM_KIND_KEY) != STREAM_KIND_SESSION:
            return []
        session_id = _text(stream, STREAM_ID_KEY)
        self._session_started = True
        return [SessionStarted(session_id)]

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
        self._pending_tools.append(name)
        return [ToolStarted(name, name)]

    def _tool_result(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        facts = _mapping(payload.get(_CORRELATION_KEY))
        name = _text(facts, _TOOL_NAME_KEY)
        exit_code = _tool_exit_code(_text(payload, _TEXT_KEY))
        signals: list[AgentSignal] = []
        if name in self._pending_tools:
            self._pending_tools.remove(name)
        else:
            signals.append(ToolStarted(name, name))
        signals.append(
            ToolFinished(name, _tool_outcome(facts.get(_OUTCOME_KEY), exit_code), exit_code)
        )
        return signals

    def _usage(self, payload: Mapping[str, Any]) -> list[AgentSignal]:
        totals = _turn_totals(_models(payload.get(_MODELS_KEY)))
        windows = _allowance_windows(payload.get(_ALLOWANCE_KEY))
        signals: list[AgentSignal] = [UsageReported(self._cumulative_usage(totals))]
        if windows:
            self._windows = windows
            signals.append(QuotaReported(windows))
        return signals

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

    def _cumulative_usage(self, totals: _TurnTotals) -> Usage:
        base = self._initial_usage
        return Usage(
            input_tokens=base.input_tokens + totals.input_tokens,
            output_tokens=base.output_tokens + totals.output_tokens,
            cache_read_tokens=base.cache_read_tokens + totals.cached_tokens,
            cache_write_tokens=base.cache_write_tokens,
            cost_usd=None,
            cost_basis=COST_BASIS_UNKNOWN,
            context_used_pct=base.context_used_pct,
        )


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
    try:
        details = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(details, Mapping):
        return None
    exit_code = cast("Mapping[str, Any]", details).get(_EXIT_CODE_KEY)
    return exit_code if type(exit_code) is int else None
