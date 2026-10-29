from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from shikiru.agents.contracts import FailureKind, TurnEnded, TurnFailed
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.domain.recovery import (
    BACKOFF_SCHEDULE,
    Backoff,
    Escalate,
    Failed,
    NextRung,
    RecoveryPolicy,
    TurnCounters,
    decide,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from shikiru.plugins.agents.codex.turn import CodexTurnParser
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT

if TYPE_CHECKING:
    from pathlib import Path

Provider = Literal["claude", "codex"]
pytestmark = pytest.mark.parametrize("provider", ["claude", "codex"])
_CAPACITY_MESSAGE = "The model is at capacity. Please try again later."


def _parser(provider: Provider, tmp_path: Path) -> ClaudeTurnParser | CodexTurnParser:
    clock = FakeClock(TASK_CREATED_AT)
    reset_time = ResetTimeParser(clock)
    if provider == "claude":
        return ClaudeTurnParser(
            session_id="capacity-test-session",
            model="test-model",
            initial_usage=None,
            clock=clock,
            reset_time=reset_time,
        )
    return CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=tmp_path,
        clock=clock,
        reset_time=reset_time,
    )


def _failure(
    provider: Provider,
    tmp_path: Path,
    *,
    message: str | None,
    status: int | None = None,
    code: str | None = None,
    stderr: str = "",
) -> TurnFailed:
    parser = _parser(provider, tmp_path)
    if message is not None or code is not None:
        if provider == "claude":
            parser.feed(
                json.dumps(
                    {
                        "type": "result",
                        "is_error": True,
                        "api_error_status": status,
                        "result": message,
                    }
                )
            )
            if code is not None:
                stderr = f"[claude-code:{code}]\n{stderr}"
        else:
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
    return failure


@pytest.mark.parametrize(
    ("message", "status", "code", "stderr"),
    [
        (_CAPACITY_MESSAGE, None, None, ""),
        ("The model test-model is AT CAPACITY.", None, None, ""),
        (_CAPACITY_MESSAGE, 429, None, ""),
        (_CAPACITY_MESSAGE, 503, None, ""),
        (None, 429, "model_capacity", ""),
        (None, None, "model_at_capacity", ""),
        ("Too many requests", 429, None, _CAPACITY_MESSAGE),
        (None, None, None, _CAPACITY_MESSAGE),
    ],
    ids=["text", "model-name", "429", "503", "code", "tag", "stderr-detail", "stderr-only"],
)
def test_capacity_is_unavailable_without_an_account_quota_reset(
    provider: Provider,
    tmp_path: Path,
    *,
    message: str | None,
    status: int | None,
    code: str | None,
    stderr: str,
) -> None:
    failure = _failure(provider, tmp_path, message=message, status=status, code=code, stderr=stderr)

    assert failure.kind is FailureKind.UNAVAILABLE
    assert failure.resets_at is None
    assert failure.message


@pytest.mark.parametrize("status", [401, 402, 403])
def test_capacity_text_does_not_override_authentication_or_payment_errors(
    provider: Provider, tmp_path: Path, status: int
) -> None:
    failure = _failure(provider, tmp_path, message=_CAPACITY_MESSAGE, status=status)

    assert failure.kind is FailureKind.AUTH


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("You've hit your usage limit", 429, FailureKind.QUOTA),
        ("context capacity exceeded", None, FailureKind.UNKNOWN),
    ],
)
def test_other_limit_errors_keep_their_classification(
    provider: Provider, tmp_path: Path, message: str, status: int | None, expected: FailureKind
) -> None:
    assert _failure(provider, tmp_path, message=message, status=status).kind is expected


@pytest.mark.parametrize("has_next_rung", [False, True])
def test_capacity_retries_then_advances_even_to_a_model_on_the_same_agent(
    provider: Provider, tmp_path: Path, *, has_next_rung: bool
) -> None:
    failure = _failure(provider, tmp_path, message=_CAPACITY_MESSAGE, status=429)
    result = Failed(failure.kind, failure.resets_at, failure.message)
    policy = RecoveryPolicy(
        now=TASK_CREATED_AT,
        max_quota_wait=timedelta(hours=8),
        has_next_rung=has_next_rung,
        has_next_rung_different_agent=False,
    )

    for attempt, delay in enumerate(BACKOFF_SCHEDULE):
        assert decide(result, TurnCounters(unavailable_retries=attempt), policy) == Backoff(delay)
    expected = (
        NextRung(different_agent=False)
        if has_next_rung
        else Escalate(EscalationReason.LADDER_EXHAUSTED)
    )
    assert (
        decide(result, TurnCounters(unavailable_retries=len(BACKOFF_SCHEDULE)), policy) == expected
    )


def test_capacity_text_in_a_successful_response_does_not_trigger_recovery(
    provider: Provider, tmp_path: Path
) -> None:
    parser = _parser(provider, tmp_path)
    payload = (
        {"type": "result", "subtype": "success", "result": _CAPACITY_MESSAGE}
        if provider == "claude"
        else {"type": "turn.completed", "text": _CAPACITY_MESSAGE}
    )

    signals = [*parser.feed(json.dumps(payload)), *parser.finish(0, "")]

    assert signals == [TurnEnded(_CAPACITY_MESSAGE)]
