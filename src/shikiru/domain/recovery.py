"""Recovery decisions for every non-completed turn result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import FailureKind
from shikiru.domain.tasks import EscalationReason

if TYPE_CHECKING:
    from shikiru.domain.reports import Report

BACKOFF_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=5),
)
MAX_STALL_RECOVERIES_PER_TURN = 2
OTHER_RETRIES_PER_TURN = 1
QUOTA_MARGIN = timedelta(seconds=60)
QUOTA_PROBE = timedelta(minutes=15)


@dataclass(frozen=True)
class Completed:
    """A turn that produced a valid report, possibly rebuilt by the Helper."""

    report: Report
    rebuilt: bool = False


@dataclass(frozen=True)
class Failed:
    """A turn the agent reported as failed, with its normalized failure kind."""

    kind: FailureKind
    resets_at: datetime | None
    message: str
    unclassified: bool = False


@dataclass(frozen=True)
class Stalled:
    """No stream signal arrived within the configured timeout."""


@dataclass(frozen=True)
class ProtocolError:
    """The turn ended without a report even after nudges and a Helper rebuild attempt."""


TurnResult = Completed | Failed | Stalled | ProtocolError


@dataclass(frozen=True)
class TurnCounters:
    """In-memory counters for the current turn only; a restart resets them."""

    stall_recoveries: int = 0
    unavailable_retries: int = 0
    other_retries: int = 0


@dataclass(frozen=True)
class RecoveryPolicy:
    """What `decide` needs beyond the result and counters: time and ladder availability."""

    now: datetime
    max_quota_wait: timedelta
    has_next_rung: bool
    has_next_rung_different_agent: bool
    quota_wait_exhausted: bool = False


@dataclass(frozen=True)
class WaitForQuota:
    """Block the agent until `until`, then resume the step on the same session."""

    until: datetime


@dataclass(frozen=True)
class Backoff:
    """Retry the same rung after `delay`."""

    delay: timedelta


@dataclass(frozen=True)
class NextRung:
    """Move to the next rung, optionally requiring a different agent."""

    different_agent: bool


@dataclass(frozen=True)
class ResumeTurn:
    """Resume the same agent session with the continue prompt."""


@dataclass(frozen=True)
class RetryTurn:
    """Re-run the current turn on the live session."""


@dataclass(frozen=True)
class Escalate:
    """Raise an escalation with `reason`."""

    reason: EscalationReason


Action = WaitForQuota | Backoff | NextRung | ResumeTurn | RetryTurn | Escalate


def quota_block_until(resets_at: datetime | None, now: datetime) -> datetime:
    """Return the agent-block deadline for a quota failure."""
    return (resets_at + QUOTA_MARGIN) if resets_at is not None else (now + QUOTA_PROBE)


def _quota_action(resets_at: datetime | None, policy: RecoveryPolicy) -> Action:
    if policy.quota_wait_exhausted:
        if policy.has_next_rung_different_agent:
            return NextRung(different_agent=True)
        return Escalate(EscalationReason.QUOTA_EXHAUSTED)
    until = quota_block_until(resets_at, policy.now)
    if until - policy.now > policy.max_quota_wait:
        if policy.has_next_rung_different_agent:
            return NextRung(different_agent=True)
        return Escalate(EscalationReason.QUOTA_EXHAUSTED)
    return WaitForQuota(until=until)


def _decide_unavailable(counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    if counters.unavailable_retries < len(BACKOFF_SCHEDULE):
        return Backoff(delay=BACKOFF_SCHEDULE[counters.unavailable_retries])
    if policy.has_next_rung:
        return NextRung(different_agent=False)
    return Escalate(EscalationReason.LADDER_EXHAUSTED)


def _decide_failure(result: Failed, counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    action: Action
    if result.unclassified:
        action = Escalate(EscalationReason.AGENT_ERROR)
    elif result.kind is FailureKind.QUOTA:
        action = _quota_action(result.resets_at, policy)
    elif result.kind is FailureKind.UNAVAILABLE:
        action = _decide_unavailable(counters, policy)
    elif result.kind is FailureKind.AUTH:
        action = Escalate(EscalationReason.AGENT_AUTH)
    elif result.kind is FailureKind.OTHER and counters.other_retries < OTHER_RETRIES_PER_TURN:
        action = RetryTurn()
    else:
        action = Escalate(EscalationReason.AGENT_ERROR)
    return action


def decide(result: TurnResult, counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    """Map a non-`Completed` turn result to the action recovery should take."""
    if isinstance(result, Failed):
        return _decide_failure(result, counters, policy)
    if isinstance(result, Stalled):
        if counters.stall_recoveries < MAX_STALL_RECOVERIES_PER_TURN:
            return ResumeTurn()
        return Escalate(EscalationReason.AGENT_STUCK)
    if isinstance(result, ProtocolError):
        return Escalate(EscalationReason.AGENT_PROTOCOL)
    message = "recovery.decide is only for non-Completed turn results"
    raise ValueError(message)
