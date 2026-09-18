"""Recovery decisions for every non-completed turn result (docs/design/03-workflow.md §7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from pydantic import Field

from shikiru.agents.contracts import FailureKind
from shikiru.domain.tasks import DomainModel, EscalationReason, Role

if TYPE_CHECKING:
    from shikiru.domain.reports import Report

BACKOFF_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=5),
)
MAX_RELAUNCHES_PER_TURN = 2
MAX_STALL_RECOVERIES_PER_TURN = 2
CONTEXT_HANDOFFS_PER_TURN = 1
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
    """A turn the agent reported as failed, with the kind its plugin mapped the error to."""

    kind: FailureKind
    resets_at: datetime | None
    message: str


@dataclass(frozen=True)
class PaneDied:
    """The agent's pane process is gone."""


@dataclass(frozen=True)
class Stalled:
    """No activity within the configured timeout; `screen` is the captured pane text."""

    screen: str


@dataclass(frozen=True)
class ProtocolError:
    """The turn ended without a report even after nudges and a Helper rebuild attempt."""


TurnResult = Completed | Failed | PaneDied | Stalled | ProtocolError


@dataclass(frozen=True)
class TurnCounters:
    """In-memory counters for the current turn only; a restart resets them (03 §1.1)."""

    relaunches: int = 0
    stall_recoveries: int = 0
    unavailable_retries: int = 0
    other_retries: int = 0
    context_handoffs: int = 0


@dataclass(frozen=True)
class RecoveryPolicy:
    """What `decide` needs beyond the result and counters: time and ladder availability."""

    now: datetime
    max_quota_wait: timedelta
    has_next_rung: bool
    has_next_rung_different_agent: bool


@dataclass(frozen=True)
class WaitForQuota:
    """Block the agent in `agent_blocks` until `until`, then re-inject `continue`."""

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
class Relaunch:
    """Relaunch the session with resume on the same rung."""


@dataclass(frozen=True)
class Handoff:
    """Start a new session on the same rung with a `handoff` turn."""


@dataclass(frozen=True)
class RetryTurn:
    """Re-run the current turn on the live session."""


@dataclass(frozen=True)
class ClassifyScreen:
    """Ask the Helper to classify the stalled screen before deciding further."""


@dataclass(frozen=True)
class KeepWaiting:
    """The agent is still working; take no action."""


@dataclass(frozen=True)
class SendKeys:
    """Send `keys` into the pane; every key was verified to be in `keys.safe`."""

    keys: tuple[str, ...]


@dataclass(frozen=True)
class Consult:
    """Route the Implementer's stalled question through a consult turn."""


@dataclass(frozen=True)
class Escalate:
    """Raise an escalation with `reason`."""

    reason: EscalationReason


Action = (
    WaitForQuota
    | Backoff
    | NextRung
    | Relaunch
    | Handoff
    | RetryTurn
    | ClassifyScreen
    | KeepWaiting
    | SendKeys
    | Consult
    | Escalate
)


class ScreenState(StrEnum):
    """What a stalled screen shows, as `Helper.classify_screen` names it (04 §6, 03 §7)."""

    WORKING = auto()
    DIALOG = auto()
    QUESTION = auto()
    QUOTA = auto()
    ERROR = auto()


class ScreenClassification(DomainModel):
    """The Helper's classification of a stalled agent screen (04 §6, 03 §7)."""

    state: ScreenState
    keys: list[str] = Field(default_factory=list)
    text: str = ""
    resets_at: datetime | None = None


def quota_block_until(resets_at: datetime | None, now: datetime) -> datetime:
    """Return the agent-block deadline for a quota failure (03 §7)."""
    return (resets_at + QUOTA_MARGIN) if resets_at is not None else (now + QUOTA_PROBE)


def _quota_action(resets_at: datetime | None, policy: RecoveryPolicy) -> Action:
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


def _decide_context(counters: TurnCounters) -> Action:
    if counters.context_handoffs < CONTEXT_HANDOFFS_PER_TURN:
        return Handoff()
    return Escalate(EscalationReason.AGENT_ERROR)


def _decide_other(counters: TurnCounters) -> Action:
    if counters.other_retries < OTHER_RETRIES_PER_TURN:
        return RetryTurn()
    return Escalate(EscalationReason.AGENT_ERROR)


def _decide_pane_died(counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    if counters.relaunches < MAX_RELAUNCHES_PER_TURN:
        return Relaunch()
    if policy.has_next_rung:
        return NextRung(different_agent=False)
    return Escalate(EscalationReason.AGENT_CRASH)


def _decide_stalled(counters: TurnCounters) -> Action:
    if counters.stall_recoveries >= MAX_STALL_RECOVERIES_PER_TURN:
        return Escalate(EscalationReason.AGENT_STUCK)
    return ClassifyScreen()


def _decide_failed(failed: Failed, counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    match failed.kind:
        case FailureKind.QUOTA:
            return _quota_action(failed.resets_at, policy)
        case FailureKind.UNAVAILABLE:
            return _decide_unavailable(counters, policy)
        case FailureKind.CONTEXT:
            return _decide_context(counters)
        case FailureKind.AUTH:
            return Escalate(EscalationReason.AGENT_AUTH)
        case FailureKind.OTHER:
            return _decide_other(counters)


def decide(result: TurnResult, counters: TurnCounters, policy: RecoveryPolicy) -> Action:
    """Map a non-`Completed` turn result to the action recovery should take (03 §7)."""
    match result:
        case Failed():
            return _decide_failed(result, counters, policy)
        case PaneDied():
            return _decide_pane_died(counters, policy)
        case Stalled():
            return _decide_stalled(counters)
        case ProtocolError():
            return Escalate(EscalationReason.AGENT_PROTOCOL)
        case Completed():
            message = "recovery.decide is only for non-Completed turn results"
            raise ValueError(message)


def decide_stall(
    classification: ScreenClassification,
    role: Role,
    keys_safe: frozenset[str],
    counters: TurnCounters,
    policy: RecoveryPolicy,
) -> Action:
    """Map a classified stalled screen to the action recovery should take (03 §7)."""
    match classification.state:
        case ScreenState.WORKING:
            return KeepWaiting()
        case ScreenState.DIALOG:
            if classification.keys and set(classification.keys) <= keys_safe:
                return SendKeys(tuple(classification.keys))
            return Escalate(EscalationReason.AGENT_STUCK)
        case ScreenState.QUESTION:
            return (
                Consult()
                if role is Role.IMPLEMENTER
                else Escalate(EscalationReason.DECISION_REQUIRED)
            )
        case ScreenState.QUOTA:
            return _quota_action(classification.resets_at, policy)
        case ScreenState.ERROR:
            return _decide_other(counters)
