"""Muse Code `exec --json` turn parser behaviour over the recorded fixtures."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
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
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
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
)
from shikiru.plugins.agents.muse.session_log import NO_SESSION_LOG_MARK
from shikiru.plugins.agents.muse.turn import (
    ContextWindow,
    MuseTurnParser,
    SessionWatch,
    context_pct,
)
from tests.support.clock import LONG_AGO, FakeClock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import AgentSignal

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
_MILLISECONDS = 1000
_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_SIGTERM = 143
_BASH = "bash"
_FIRST_SESSION_ID = "00000000-0000-7000-9000-000000000002"
_TOOL_SESSION_ID = "00000000-0000-7000-9000-00000000005e"
_AS_OF_MS = 1794502385942
_NO_LOG_HOME = Path("muse-home-without-sessions")
_CONTEXT_WINDOW = 1000
_FIVE_HOUR_RESET_MS = 1794514840000
_SEVEN_DAY_RESET_MS = 1794787200000
_PRIOR_USAGE = Usage(1_000, 5, 7, 3, None, COST_BASIS_UNKNOWN, 40.0)


def _fixture_lines(name: str) -> list[str]:
    return (_FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()


def _fixture_stderr(name: str) -> str:
    return (_FIXTURES / f"{name}.stderr.log").read_text(encoding="utf-8")


def _first_record(lines: Sequence[str], payload_type: str) -> dict[str, Any]:
    return next(
        record
        for record in map(json.loads, lines)
        if record.get(RECORD_PAYLOAD_TYPE_KEY) == payload_type
    )


def _with_fields(line: str, **fields: object) -> str:
    record = json.loads(line)
    return json.dumps({**record, RECORD_PAYLOAD_KEY: {**record[RECORD_PAYLOAD_KEY], **fields}})


def _rewrite(lines: Sequence[str], payload_type: str, **fields: object) -> list[str]:
    return [
        _with_fields(line, **fields)
        if json.loads(line).get(RECORD_PAYLOAD_TYPE_KEY) == payload_type
        else line
        for line in lines
    ]


def _tool_result_line(text: str, outcome: str) -> str:
    record = _first_record(_fixture_lines("turn_tool"), TOOL_RESULT_TYPE)
    return _with_fields(
        json.dumps(record),
        text=text,
        correlation_facts={"tool_name": _BASH, "outcome": outcome},
    )


def _unknown_tool_line() -> str:
    return _fixture_lines("tool_result_unknown_tool")[0]


def _terminal_completed_line() -> str:
    return next(
        line
        for line in _fixture_lines("turn_first")
        if json.loads(line).get(RECORD_PAYLOAD_TYPE_KEY) == RUN_TERMINAL_COMPLETED_TYPE
    )


def _bash_proposal_line() -> str:
    return next(
        line
        for line in _fixture_lines("turn_tool")
        if json.loads(line).get(RECORD_PAYLOAD_TYPE_KEY) == TASK_PROPOSED_TYPE
        and json.loads(line)[RECORD_PAYLOAD_KEY]["event"].get("task_kind") == "tool.bash"
    )


def _instant(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / _MILLISECONDS, tz=UTC)


def _no_log_watch() -> SessionWatch:
    return SessionWatch(
        home=_NO_LOG_HOME,
        session_id=None,
        context_window=ContextWindow(tokens=_CONTEXT_WINDOW),
        mark=NO_SESSION_LOG_MARK,
    )


def _signals(
    lines: Sequence[str],
    exit_code: int = _EXIT_OK,
    stderr: str = "",
    *,
    initial_usage: Usage | None = None,
    interrupted: bool = False,
) -> list[AgentSignal]:
    clock = FakeClock(_NOW)
    parser = MuseTurnParser(clock, ResetTimeParser(clock), _no_log_watch(), initial_usage)
    fed = [signal for line in lines for signal in parser.feed(line)]
    return [*fed, *parser.finish(exit_code, stderr, interrupted=interrupted)]


def _of_type(signals: Sequence[AgentSignal], *types: type) -> list[AgentSignal]:
    return [signal for signal in signals if isinstance(signal, types)]


def _failures(signals: Sequence[AgentSignal]) -> list[TurnFailed]:
    return [signal for signal in signals if isinstance(signal, TurnFailed)]


@pytest.mark.parametrize(
    ("fixture", "session_id"),
    [
        ("turn_first", _FIRST_SESSION_ID),
        ("turn_resume", _FIRST_SESSION_ID),
        ("turn_tool", _TOOL_SESSION_ID),
    ],
    ids=("first-turn", "resumed-turn-keeps-id", "tool-turn"),
)
def test_session_is_reported_once_with_its_stream_id(fixture: str, session_id: str) -> None:
    starts = _of_type(_signals(_fixture_lines(fixture)), SessionStarted)

    assert starts == [SessionStarted(session_id)]


@pytest.mark.parametrize(
    ("fixture", "texts"),
    [("turn_first", ("OK",)), ("turn_tool", ("01fa8a", "2"))],
    ids=("single-delta", "split-deltas"),
)
def test_output_deltas_are_forwarded_in_order(fixture: str, texts: tuple[str, ...]) -> None:
    messages = _of_type(_signals(_fixture_lines(fixture)), Message)

    assert messages == [Message(text) for text in texts]


@pytest.mark.parametrize(
    ("fixture", "text"),
    [
        ("turn_first", "OK"),
        ("turn_resume", "OK"),
        ("turn_tool", "01fa8a2"),
        ("turn_headless_result", '{"answer":"OK"}'),
    ],
    ids=("first-turn", "resumed-turn", "tool-turn", "headless-result"),
)
def test_completed_terminal_ends_the_turn_with_its_text(fixture: str, text: str) -> None:
    ends = _of_type(_signals(_fixture_lines(fixture)), TurnEnded)

    assert ends == [TurnEnded(text)]


def test_tool_proposal_starts_the_tool_and_result_finishes_it() -> None:
    tool_signals = _of_type(_signals(_fixture_lines("turn_tool")), ToolStarted, ToolFinished)

    assert tool_signals == [ToolStarted(_BASH, _BASH), ToolFinished(_BASH, ToolOutcome.SUCCESS, 0)]


@pytest.mark.parametrize(
    ("text", "outcome", "expected_outcome", "expected_exit_code"),
    [
        (json.dumps({"exit_code": 0}), "success", ToolOutcome.SUCCESS, 0),
        (json.dumps({"exit_code": 1}), "success", ToolOutcome.FAILURE, 1),
        (json.dumps({"exit_code": 0}), "error", None, 0),
        ("not json", "success", ToolOutcome.SUCCESS, None),
        (json.dumps({"output": "x"}), "error", None, None),
    ],
    ids=(
        "zero-exit-success",
        "nonzero-exit-beats-success",
        "zero-exit-other-outcome-unknown",
        "non-json-text-success",
        "missing-exit-unknown-outcome",
    ),
)
def test_tool_result_outcome_prefers_a_nonzero_exit_code(
    text: str,
    outcome: str,
    expected_outcome: ToolOutcome | None,
    expected_exit_code: int | None,
) -> None:
    tool_signals = _of_type(_signals([_tool_result_line(text, outcome)]), ToolStarted, ToolFinished)

    assert tool_signals == [
        ToolStarted(_BASH, _BASH),
        ToolFinished(_BASH, expected_outcome, expected_exit_code),
    ]


def test_tool_result_without_correlation_facts_ends_turn_with_terminal() -> None:
    signals = _signals([_unknown_tool_line(), _terminal_completed_line()])

    assert _of_type(signals, TurnEnded) == [TurnEnded("OK")]
    assert _failures(signals) == []
    assert _of_type(signals, ToolStarted, ToolFinished) == []


def test_tool_result_without_correlation_facts_leaves_pending_tool_to_finish() -> None:
    signals = _signals(
        [_bash_proposal_line(), _unknown_tool_line(), _terminal_completed_line()],
    )

    assert _of_type(signals, ToolStarted, ToolFinished) == [
        ToolStarted(_BASH, _BASH),
        ToolFinished(_BASH, None, None),
    ]
    assert _of_type(signals, TurnEnded) == [TurnEnded("OK")]
    assert _failures(signals) == []


def test_tool_result_with_null_correlation_facts_ends_turn_with_terminal() -> None:
    signals = _signals(
        [_with_fields(_unknown_tool_line(), correlation_facts=None), _terminal_completed_line()],
    )

    assert _of_type(signals, TurnEnded) == [TurnEnded("OK")]
    assert _failures(signals) == []
    assert _of_type(signals, ToolStarted, ToolFinished) == []


@pytest.mark.parametrize(
    "correlation_facts",
    [
        "bash",
        [],
        {},
        {"outcome": "success"},
        {"tool_name": 42, "outcome": "success"},
    ],
    ids=(
        "non-mapping-string",
        "non-mapping-list",
        "empty-mapping",
        "missing-tool-name",
        "non-string-tool-name",
    ),
)
def test_broken_correlation_facts_is_still_malformed(correlation_facts: object) -> None:
    signals = _signals([_with_fields(_unknown_tool_line(), correlation_facts=correlation_facts)])

    assert [failure.kind for failure in _failures(signals)] == [FailureKind.UNKNOWN]
    assert _of_type(signals, TurnEnded) == []


def test_tool_result_without_text_is_still_malformed() -> None:
    record = json.loads(_unknown_tool_line())
    del record[RECORD_PAYLOAD_KEY]["text"]

    signals = _signals([json.dumps(record), _terminal_completed_line()])

    assert [failure.kind for failure in _failures(signals)] == [FailureKind.UNKNOWN]
    assert _of_type(signals, TurnEnded) == []


def test_tool_result_without_correlation_or_text_is_still_malformed() -> None:
    line = _with_fields(_unknown_tool_line(), text=123)

    signals = _signals([line])

    assert [failure.kind for failure in _failures(signals)] == [FailureKind.UNKNOWN]
    assert _of_type(signals, TurnEnded) == []


def test_running_tool_is_closed_without_outcome_when_the_turn_ends() -> None:
    lines = _fixture_lines("turn_tool")
    result_index = next(
        index
        for index, line in enumerate(lines)
        if json.loads(line).get(RECORD_PAYLOAD_TYPE_KEY) == TOOL_RESULT_TYPE
    )

    signals = _signals(lines[:result_index], _EXIT_FAILED)

    assert _of_type(signals, ToolStarted, ToolFinished) == [
        ToolStarted(_BASH, _BASH),
        ToolFinished(_BASH, None, None),
    ]
    assert [failure.kind for failure in _failures(signals)] == [FailureKind.UNKNOWN]


@pytest.mark.parametrize(
    ("fixture", "initial", "expected"),
    [
        ("turn_first", None, Usage(32_358, 18, 0, 0, None, COST_BASIS_UNKNOWN, None)),
        ("turn_resume", None, Usage(32_415, 12, 16_244, 0, None, COST_BASIS_UNKNOWN, None)),
        (
            "turn_resume",
            _PRIOR_USAGE,
            Usage(33_415, 17, 16_251, 3, None, COST_BASIS_UNKNOWN, 40.0),
        ),
        ("turn_auth_rejected", None, Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None)),
    ],
    ids=("first-turn", "resumed-turn", "resumed-turn-adds-prior-usage", "no-models"),
)
def test_turn_usage_is_summed_over_models_and_added_to_prior_usage(
    fixture: str, initial: Usage | None, expected: Usage
) -> None:
    usage = _of_type(_signals(_fixture_lines(fixture), initial_usage=initial), UsageReported)

    assert usage == [UsageReported(expected)]


_FIXTURE_ALLOWANCE = _first_record(_fixture_lines("turn_first"), RUN_USAGE_TYPE)[
    RECORD_PAYLOAD_KEY
]["allowance"]
_CAPPED_ALLOWANCE = {
    **_FIXTURE_ALLOWANCE,
    "window": {**_FIXTURE_ALLOWANCE["window"], "used_percent": 150},
}
_OBSERVED_AT = _instant(_AS_OF_MS)
_FIVE_HOUR = QuotaWindow(QUOTA_WINDOW_FIVE_HOUR, 8.0, _instant(_FIVE_HOUR_RESET_MS), _OBSERVED_AT)
_SEVEN_DAY = QuotaWindow(QUOTA_WINDOW_SEVEN_DAY, 2.0, _instant(_SEVEN_DAY_RESET_MS), _OBSERVED_AT)


@pytest.mark.parametrize(
    ("allowance", "windows"),
    [
        (_FIXTURE_ALLOWANCE, (_FIVE_HOUR, _SEVEN_DAY)),
        (_CAPPED_ALLOWANCE, (replace(_FIVE_HOUR, used_pct=100.0), _SEVEN_DAY)),
        (None, None),
    ],
    ids=("reported-windows", "percentage-capped-at-100", "no-allowance"),
)
def test_allowance_windows_are_reported_as_five_hour_and_seven_day(
    allowance: object, windows: tuple[QuotaWindow, ...] | None
) -> None:
    lines = _rewrite(_fixture_lines("turn_first"), RUN_USAGE_TYPE, allowance=allowance)

    reports = _of_type(_signals(lines), QuotaReported)

    assert reports == ([QuotaReported(windows)] if windows is not None else [])


@pytest.mark.parametrize(
    ("fixture", "reason", "kind"),
    [
        ("turn_auth_rejected", None, FailureKind.AUTH),
        ("turn_unknown_model", None, FailureKind.OTHER),
        ("turn_step_cap", None, FailureKind.OTHER),
        ("turn_auth_rejected", "stream reset by peer", FailureKind.UNKNOWN),
    ],
    ids=("rejected-key", "unknown-model", "step-cap", "unrecognised-text"),
)
def test_failed_terminal_is_classified_from_its_reason(
    fixture: str, reason: str | None, kind: FailureKind
) -> None:
    lines = _fixture_lines(fixture)
    if reason is not None:
        lines = _rewrite(lines, RUN_TERMINAL_FAILED_TYPE, reason=reason)

    [failure] = _failures(_signals(lines, _EXIT_FAILED, _fixture_stderr(fixture)))

    assert failure.kind is kind


_QUOTA_OBSERVED_AT = datetime(2026, 10, 13, 12, tzinfo=UTC)
_RECONSTRUCTED_QUOTA_RESET = datetime(2026, 10, 13, 22, 31, 16, tzinfo=UTC)


def test_reconstructed_exec_quota_failure_carries_the_reset_it_names() -> None:
    lines = _fixture_lines("turn_quota_failure_reconstructed")
    reason = _first_record(lines, RUN_TERMINAL_FAILED_TYPE)[RECORD_PAYLOAD_KEY]["reason"]
    clock = FakeClock(_QUOTA_OBSERVED_AT)
    parser = MuseTurnParser(clock, ResetTimeParser(clock), _no_log_watch())
    signals = [signal for line in lines for signal in parser.feed(line)]
    signals.extend(parser.finish(_EXIT_FAILED, ""))

    assert _failures(signals) == [TurnFailed(FailureKind.QUOTA, reason, _RECONSTRUCTED_QUOTA_RESET)]


_MISSING_CREDENTIALS_STDERR = _fixture_stderr("turn_missing_credentials")
_SIGTERM_STDERR = _fixture_stderr("turn_stopped")


@pytest.mark.parametrize(
    ("fixture", "exit_code", "stderr", "interrupted", "kind"),
    [
        (
            "turn_missing_credentials",
            _EXIT_FAILED,
            _MISSING_CREDENTIALS_STDERR,
            False,
            FailureKind.UNKNOWN,
        ),
        ("turn_stopped", _EXIT_SIGTERM, _SIGTERM_STDERR, True, None),
        ("turn_stopped", _EXIT_SIGTERM, _SIGTERM_STDERR, False, FailureKind.UNKNOWN),
        ("turn_stopped", _EXIT_FAILED, "some unrecognised crash", False, FailureKind.UNKNOWN),
    ],
    ids=(
        "missing-credentials-no-record",
        "sigterm-interrupted",
        "sigterm-not-interrupted",
        "unrecognised-stderr",
    ),
)
def test_turn_without_terminal_record_fails_only_when_not_interrupted(
    fixture: str,
    exit_code: int,
    stderr: str,
    interrupted: bool,
    kind: FailureKind | None,
) -> None:
    signals = _signals(_fixture_lines(fixture), exit_code, stderr, interrupted=interrupted)
    failures = _failures(signals)

    assert [failure.kind for failure in failures] == ([kind] if kind is not None else [])
    assert all(stderr in failure.message for failure in failures)
    assert _of_type(signals, TurnEnded) == []


_TURN_FIRST = _fixture_lines("turn_first")
_TRUNCATED_LINE = _TURN_FIRST[-1][: len(_TURN_FIRST[-1]) // 2]


@pytest.mark.parametrize(
    ("lines", "interrupted", "kind"),
    [
        ([*_TURN_FIRST, ""], False, None),
        ([*_TURN_FIRST, _TRUNCATED_LINE], False, FailureKind.UNKNOWN),
        ([*_TURN_FIRST, "[]"], False, FailureKind.UNKNOWN),
        (
            _rewrite(_TURN_FIRST, RUN_USAGE_TYPE, models="muse-ember-1.0"),
            False,
            FailureKind.UNKNOWN,
        ),
        ([*_TURN_FIRST, _TRUNCATED_LINE], True, None),
    ],
    ids=(
        "blank-line-ignored",
        "truncated-record",
        "non-object-record",
        "non-list-models",
        "truncated-record-after-sigterm",
    ),
)
def test_malformed_records_fail_the_turn_unless_interrupted(
    lines: list[str], interrupted: bool, kind: FailureKind | None
) -> None:
    failures = _failures(_signals(lines, interrupted=interrupted))

    assert [failure.kind for failure in failures] == ([kind] if kind is not None else [])


@pytest.mark.parametrize(
    ("prompt_tokens", "window_tokens", "previous", "expected"),
    [
        (None, _CONTEXT_WINDOW, 40.0, 40.0),
        (250, _CONTEXT_WINDOW, None, 25.0),
        (1500, _CONTEXT_WINDOW, 40.0, 100.0),
        (250, None, 40.0, None),
        (None, None, 40.0, None),
    ],
    ids=(
        "no-estimate-keeps-previous",
        "estimate-is-share-of-window",
        "over-window-is-capped",
        "unknown-window-drops-previous",
        "unknown-window-without-estimate-drops-previous",
    ),
)
def test_context_usage_is_the_latest_estimate_over_the_window(
    prompt_tokens: int | None,
    window_tokens: int | None,
    previous: float | None,
    expected: float | None,
) -> None:
    assert context_pct(prompt_tokens, window_tokens, previous) == expected


def test_records_outside_the_mapping_produce_no_signal() -> None:
    clock = FakeClock(_NOW)
    parser = MuseTurnParser(clock, ResetTimeParser(clock), _no_log_watch())
    output = _first_record(_fixture_lines("turn_first"), RUN_OUTPUT_DELTA_TYPE)
    parser.feed(_fixture_lines("turn_first")[0])

    signals = parser.feed(json.dumps({**output, RECORD_PAYLOAD_TYPE_KEY: "run.future.kind"}))

    assert signals == []


_INVALID_PERCENTAGES = (10**400, float("inf"), float("-inf"), float("nan"))
_INVALID_PERCENTAGE_IDS = ("integer-overflow", "positive-infinity", "negative-infinity", "nan")


@pytest.mark.parametrize("used_percent", _INVALID_PERCENTAGES, ids=_INVALID_PERCENTAGE_IDS)
def test_invalid_allowance_percentage_fails_turn_as_malformed(used_percent: Any) -> None:
    clock = FakeClock(LONG_AGO)
    parser = MuseTurnParser(clock, ResetTimeParser(clock), _no_log_watch())
    line = json.dumps(
        {
            RECORD_STREAM_KEY: {
                STREAM_KIND_KEY: STREAM_KIND_SESSION,
                STREAM_ID_KEY: _FIRST_SESSION_ID,
            },
            RECORD_PAYLOAD_TYPE_KEY: RUN_USAGE_TYPE,
            RECORD_PAYLOAD_KEY: {
                "models": [],
                "allowance": {
                    "as_of_ms": 0,
                    "window": {"used_percent": used_percent},
                },
            },
        }
    )

    assert parser.feed(line) == [SessionStarted(_FIRST_SESSION_ID)]
    assert [failure.kind for failure in _failures(parser.finish(_EXIT_OK, ""))] == [
        FailureKind.UNKNOWN
    ]
