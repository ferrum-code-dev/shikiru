"""Codex capacity checks: user-supplied structured output and synthetic precedence cases."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import pytest

from shikiru.agents.contracts import FailureKind, TurnEnded, TurnFailed, Usage, UsageReported
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.domain.recovery import (
    Backoff,
    Escalate,
    Failed,
    NextRung,
    RecoveryPolicy,
    TurnCounters,
    decide,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.plugins.agents.codex.turn import CodexTurnParser
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings

_USER_CAPACITY_MESSAGE = "Selected model is at capacity. Please try a different model."
_CAPACITY_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "agents" / "codex" / "turn_model_capacity.jsonl"
)


class _StructuredFailureCase(NamedTuple):
    message: str
    status: int | None
    stderr: str
    expected: FailureKind
    context_exceeded: bool
    code: str | None = None


def _parser(tmp_path: Path) -> CodexTurnParser:
    clock = FakeClock(TASK_CREATED_AT)
    return CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )


def _failure(
    tmp_path: Path,
    *,
    message: str | None,
    status: int | None = None,
    code: str | None = None,
    stderr: str = "",
) -> tuple[CodexTurnParser, TurnFailed]:
    parser = _parser(tmp_path)
    if message is not None or status is not None or code is not None:
        parser.feed(
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": message, "status": status, "code": code},
                }
            )
        )
    [failure] = parser.finish(1, stderr)
    assert isinstance(failure, TurnFailed)
    return parser, failure


def _policy(*, has_next_rung: bool) -> RecoveryPolicy:
    retries = make_settings().orchestration.retries
    return RecoveryPolicy(
        now=TASK_CREATED_AT,
        max_quota_wait=timedelta(hours=8),
        has_next_rung=has_next_rung,
        has_next_rung_different_agent=False,
        unavailable_initial_seconds=retries.unavailable_initial_seconds,
        unavailable_multiplier=retries.unavailable_multiplier,
        unavailable_max_seconds=retries.unavailable_max_seconds,
        unavailable_max_retries=retries.unavailable_max_retries,
    )


@pytest.mark.parametrize(
    ("message", "status"),
    [
        (_USER_CAPACITY_MESSAGE, 429),
        ("Selected model is AT CAPACITY. Please try a different model.", None),
    ],
    ids=["synthetic-http-429", "synthetic-case-variation"],
)
def test_synthetic_capacity_precedence_is_unavailable_without_an_account_quota_reset(
    tmp_path: Path, message: str, status: int | None
) -> None:
    _, failure = _failure(tmp_path, message=message, status=status)

    assert failure.kind is FailureKind.UNAVAILABLE
    assert failure.resets_at is None


def test_synthetic_terminal_stderr_capacity_is_unavailable_without_structured_failure(
    tmp_path: Path,
) -> None:
    _, failure = _failure(tmp_path, message=None, stderr=_USER_CAPACITY_MESSAGE)

    assert failure.kind is FailureKind.UNAVAILABLE
    assert failure.resets_at is None


@pytest.mark.parametrize("status", [401, 402, 403])
def test_synthetic_authentication_and_payment_statuses_keep_precedence_over_capacity_text(
    tmp_path: Path, status: int
) -> None:
    _, failure = _failure(tmp_path, message=_USER_CAPACITY_MESSAGE, status=status)

    assert failure.kind is FailureKind.AUTH


@pytest.mark.parametrize(
    "case",
    [
        _StructuredFailureCase(
            "opaque structured failure",
            None,
            "overloaded; invalid api key; rate limit; context length exceeded; at capacity",
            FailureKind.UNKNOWN,
            False,
        ),
        _StructuredFailureCase(
            "context_length_exceeded",
            None,
            "service overloaded; the model is at capacity",
            FailureKind.OTHER,
            True,
        ),
        _StructuredFailureCase(
            message="context window is at capacity",
            status=None,
            stderr="",
            expected=FailureKind.OTHER,
            context_exceeded=True,
            code="context_length_exceeded",
        ),
        _StructuredFailureCase(
            "You've hit your usage limit",
            429,
            "invalid api key; the model is at capacity",
            FailureKind.QUOTA,
            False,
        ),
        _StructuredFailureCase(
            "upstream service error",
            503,
            "invalid api key; the model is at capacity",
            FailureKind.UNAVAILABLE,
            False,
        ),
        _StructuredFailureCase(
            "Invalid API key",
            401,
            "the model is at capacity; usage limit",
            FailureKind.AUTH,
            False,
        ),
    ],
    ids=[
        "unknown",
        "context",
        "explicit-context-code-with-capacity-phrase",
        "account-quota",
        "server-unavailable",
        "auth",
    ],
)
def test_synthetic_stderr_noise_does_not_reclassify_structured_failures(
    tmp_path: Path, case: _StructuredFailureCase
) -> None:
    parser, failure = _failure(
        tmp_path,
        message=case.message,
        status=case.status,
        code=case.code,
        stderr=case.stderr,
    )

    assert failure.kind is case.expected
    assert parser.context_exceeded is case.context_exceeded


def test_user_supplied_capacity_records_follow_unavailable_retry_policy(tmp_path: Path) -> None:
    parser = _parser(tmp_path)
    lines = _CAPACITY_FIXTURE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parser.feed(line)
    signals = parser.finish(1, "")
    assert signals == [TurnFailed(FailureKind.UNAVAILABLE, _USER_CAPACITY_MESSAGE, None)]
    [failure] = signals
    assert isinstance(failure, TurnFailed)
    assert parser.context_exceeded is False
    result = Failed(failure.kind, failure.resets_at, failure.message)
    policy = _policy(has_next_rung=True)

    expected_delays = [timedelta(seconds=30), timedelta(seconds=120), timedelta(seconds=300)]
    for attempt, delay in enumerate(expected_delays):
        assert decide(result, TurnCounters(unavailable_retries=attempt), policy) == Backoff(delay)
    assert decide(
        result, TurnCounters(unavailable_retries=policy.unavailable_max_retries), policy
    ) == NextRung(different_agent=False)


def test_synthetic_capacity_escalates_when_no_next_rung_remains(tmp_path: Path) -> None:
    _, failure = _failure(tmp_path, message=_USER_CAPACITY_MESSAGE)
    result = Failed(failure.kind, failure.resets_at, failure.message)
    policy = _policy(has_next_rung=False)

    assert decide(
        result, TurnCounters(unavailable_retries=policy.unavailable_max_retries), policy
    ) == Escalate(EscalationReason.LADDER_EXHAUSTED)


def test_synthetic_successful_response_with_capacity_text_does_not_trigger_recovery(
    tmp_path: Path,
) -> None:
    parser = _parser(tmp_path)
    parser.feed(json.dumps({"type": "turn.completed", "text": _USER_CAPACITY_MESSAGE}))

    assert parser.finish(0, "") == [
        UsageReported(Usage(None, None, None, None, None, "unknown", None)),
        TurnEnded(_USER_CAPACITY_MESSAGE),
    ]
