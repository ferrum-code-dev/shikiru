from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import FailureKind
from shikiru.domain.recovery import (
    BACKOFF_SCHEDULE,
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
from tests.support.factories import make_verdict

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(
    *,
    now: datetime = _NOW,
    max_quota_wait: timedelta = timedelta(hours=8),
    has_next_rung: bool = False,
    has_next_rung_different_agent: bool = False,
) -> RecoveryPolicy:
    return RecoveryPolicy(
        now=now,
        max_quota_wait=max_quota_wait,
        has_next_rung=has_next_rung,
        has_next_rung_different_agent=has_next_rung_different_agent,
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
            Backoff(BACKOFF_SCHEDULE[0]),
        ),
        (
            FailureKind.UNAVAILABLE,
            None,
            _policy(has_next_rung=True),
            TurnCounters(unavailable_retries=len(BACKOFF_SCHEDULE)),
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


def test_decide_rejects_a_completed_result() -> None:
    with pytest.raises(ValueError, match="non-Completed"):
        decide(Completed(report=make_verdict()), TurnCounters(), _policy())
