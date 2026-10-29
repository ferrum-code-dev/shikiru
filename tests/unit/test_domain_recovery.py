from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import FailureKind
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    QUOTA_PROBE,
    Backoff,
    Completed,
    Escalate,
    Failed,
    NextRung,
    ProtocolError,
    RecoveryPolicy,
    ResumeTurn,
    RetryTurn,
    Stalled,
    TurnCounters,
    WaitForQuota,
    decide,
)
from shikiru.domain.tasks import EscalationReason
from tests.support.factories import make_settings, make_verdict

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_DEFAULT_RETRIES = make_settings().orchestration.retries


def _policy(
    *,
    now: datetime = _NOW,
    max_quota_wait: timedelta = timedelta(hours=8),
    has_next_rung: bool = False,
    has_next_rung_different_agent: bool = False,
    unavailable_initial_seconds: float = _DEFAULT_RETRIES.unavailable_initial_seconds,
    unavailable_multiplier: float = _DEFAULT_RETRIES.unavailable_multiplier,
    unavailable_max_seconds: float = _DEFAULT_RETRIES.unavailable_max_seconds,
    unavailable_max_retries: int = _DEFAULT_RETRIES.unavailable_max_retries,
) -> RecoveryPolicy:
    return RecoveryPolicy(
        now=now,
        max_quota_wait=max_quota_wait,
        has_next_rung=has_next_rung,
        has_next_rung_different_agent=has_next_rung_different_agent,
        unavailable_initial_seconds=unavailable_initial_seconds,
        unavailable_multiplier=unavailable_multiplier,
        unavailable_max_seconds=unavailable_max_seconds,
        unavailable_max_retries=unavailable_max_retries,
    )


@pytest.mark.parametrize(
    ("kind", "resets_at", "policy", "counters", "expected"),
    [
        (
            FailureKind.QUOTA,
            _NOW + timedelta(minutes=10),
            _policy(),
            TurnCounters(),
            WaitForQuota(_NOW + timedelta(minutes=10) + QUOTA_MARGIN),
        ),
        (
            FailureKind.QUOTA,
            None,
            _policy(),
            TurnCounters(),
            WaitForQuota(_NOW + QUOTA_PROBE),
        ),
        (
            FailureKind.QUOTA,
            _NOW + timedelta(hours=9),
            _policy(has_next_rung_different_agent=True),
            TurnCounters(),
            NextRung(different_agent=True),
        ),
        (
            FailureKind.QUOTA,
            _NOW + timedelta(hours=9),
            _policy(),
            TurnCounters(),
            Escalate(EscalationReason.QUOTA_EXHAUSTED),
        ),
        (
            FailureKind.UNAVAILABLE,
            None,
            _policy(),
            TurnCounters(),
            Backoff(timedelta(seconds=30)),
        ),
        (
            FailureKind.UNAVAILABLE,
            None,
            _policy(has_next_rung=True),
            TurnCounters(unavailable_retries=3),
            NextRung(different_agent=False),
        ),
        (
            FailureKind.AUTH,
            None,
            _policy(),
            TurnCounters(),
            Escalate(EscalationReason.AGENT_AUTH),
        ),
        (
            FailureKind.OTHER,
            None,
            _policy(),
            TurnCounters(),
            RetryTurn(),
        ),
        (
            FailureKind.OTHER,
            None,
            _policy(),
            TurnCounters(other_retries=1),
            Escalate(EscalationReason.AGENT_ERROR),
        ),
        (
            FailureKind.OTHER,
            None,
            _policy(),
            TurnCounters(),
            Escalate(EscalationReason.AGENT_ERROR),
        ),
    ],
    ids=[
        "quota-reset-margin",
        "quota-unknown-reset",
        "quota-next-agent",
        "quota-exhausted",
        "unavailable-backoff",
        "unavailable-next-rung",
        "auth-escalates",
        "other-retries",
        "other-retry-exhausted",
        "unclassified-fails-fast",
    ],
)
def test_failure_recovery_actions(
    kind: FailureKind,
    resets_at: datetime | None,
    policy: RecoveryPolicy,
    counters: TurnCounters,
    expected: object,
) -> None:
    failure = Failed(
        kind, resets_at, "", unclassified=kind is FailureKind.OTHER and expected != RetryTurn()
    )

    assert decide(failure, counters, policy) == expected


@pytest.mark.parametrize(
    ("counters", "expected"),
    [
        (TurnCounters(stall_recoveries=0), ResumeTurn()),
        (TurnCounters(stall_recoveries=2), Escalate(EscalationReason.AGENT_STUCK)),
    ],
)
def test_stall_resumes_then_escalates_after_the_limit(
    counters: TurnCounters, expected: object
) -> None:
    assert decide(Stalled(), counters, _policy()) == expected


def test_protocol_error_escalates() -> None:
    assert decide(ProtocolError(), TurnCounters(), _policy()) == Escalate(
        EscalationReason.AGENT_PROTOCOL
    )


def test_default_unavailable_backoff_preserves_existing_three_delays() -> None:
    failure = Failed(FailureKind.UNAVAILABLE, None, "model at capacity")
    policy = _policy(has_next_rung=True)

    assert [
        decide(failure, TurnCounters(unavailable_retries=attempt), policy) for attempt in range(3)
    ] == [
        Backoff(timedelta(seconds=30)),
        Backoff(timedelta(seconds=120)),
        Backoff(timedelta(seconds=300)),
    ]
    assert decide(failure, TurnCounters(unavailable_retries=3), policy) == NextRung(
        different_agent=False
    )


def test_custom_unavailable_backoff_uses_exponential_delays_capped_at_maximum() -> None:
    failure = Failed(FailureKind.UNAVAILABLE, None, "model at capacity")
    policy = _policy(
        has_next_rung=True,
        unavailable_initial_seconds=2,
        unavailable_multiplier=3,
        unavailable_max_seconds=10,
        unavailable_max_retries=4,
    )

    assert [
        decide(failure, TurnCounters(unavailable_retries=attempt), policy) for attempt in range(4)
    ] == [
        Backoff(timedelta(seconds=2)),
        Backoff(timedelta(seconds=6)),
        Backoff(timedelta(seconds=10)),
        Backoff(timedelta(seconds=10)),
    ]
    assert decide(failure, TurnCounters(unavailable_retries=4), policy) == NextRung(
        different_agent=False
    )


def test_zero_unavailable_retries_moves_to_the_next_rung_immediately() -> None:
    failure = Failed(FailureKind.UNAVAILABLE, None, "model at capacity")
    policy = _policy(has_next_rung=True, unavailable_max_retries=0)

    assert decide(failure, TurnCounters(), policy) == NextRung(different_agent=False)


def test_very_large_finite_multiplier_caps_without_overflow() -> None:
    failure = Failed(FailureKind.UNAVAILABLE, None, "model at capacity")
    policy = _policy(
        has_next_rung=True,
        unavailable_initial_seconds=1,
        unavailable_multiplier=1e308,
        unavailable_max_seconds=10,
        unavailable_max_retries=2,
    )

    assert decide(failure, TurnCounters(unavailable_retries=1), policy) == Backoff(
        timedelta(seconds=10)
    )


def test_decide_rejects_a_completed_result() -> None:
    with pytest.raises(ValueError, match="non-Completed"):
        decide(Completed(report=make_verdict()), TurnCounters(), _policy())
