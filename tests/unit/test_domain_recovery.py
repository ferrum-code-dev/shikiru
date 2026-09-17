from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import FailureKind
from shikiru.domain.recovery import (
    BACKOFF_SCHEDULE,
    QUOTA_MARGIN,
    QUOTA_PROBE,
    Backoff,
    ClassifyScreen,
    Completed,
    Consult,
    DecisionRequired,
    Escalate,
    Failed,
    Handoff,
    KeepWaiting,
    NextRung,
    PaneDied,
    ProtocolError,
    RecoveryPolicy,
    Relaunch,
    RetryTurn,
    ScreenClassification,
    SendKeys,
    Stalled,
    TurnCounters,
    WaitForQuota,
    decide,
    decide_stall,
)
from shikiru.domain.tasks import EscalationReason, Role

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


def test_quota_failure_with_a_known_reset_waits_until_reset_plus_margin() -> None:
    resets_at = _NOW + timedelta(minutes=10)

    action = decide(Failed(FailureKind.QUOTA, resets_at, ""), TurnCounters(), _policy())

    assert action == WaitForQuota(until=resets_at + QUOTA_MARGIN)


def test_quota_failure_with_an_unknown_reset_probes_after_quota_probe() -> None:
    action = decide(Failed(FailureKind.QUOTA, None, ""), TurnCounters(), _policy())

    assert action == WaitForQuota(until=_NOW + QUOTA_PROBE)


def test_quota_failure_beyond_max_wait_moves_to_a_different_agent() -> None:
    resets_at = _NOW + timedelta(hours=9)
    policy = _policy(has_next_rung_different_agent=True)

    action = decide(Failed(FailureKind.QUOTA, resets_at, ""), TurnCounters(), policy)

    assert action == NextRung(different_agent=True)


def test_quota_failure_beyond_max_wait_with_no_other_agent_exhausts_quota() -> None:
    resets_at = _NOW + timedelta(hours=9)

    action = decide(Failed(FailureKind.QUOTA, resets_at, ""), TurnCounters(), _policy())

    assert action == Escalate(EscalationReason.QUOTA_EXHAUSTED)


@pytest.mark.parametrize("attempt", range(len(BACKOFF_SCHEDULE)))
def test_unavailable_failure_backs_off_on_the_configured_schedule(attempt: int) -> None:
    counters = TurnCounters(unavailable_retries=attempt)

    action = decide(Failed(FailureKind.UNAVAILABLE, None, ""), counters, _policy())

    assert action == Backoff(delay=BACKOFF_SCHEDULE[attempt])


def test_unavailable_failure_after_the_schedule_moves_to_the_next_rung() -> None:
    counters = TurnCounters(unavailable_retries=len(BACKOFF_SCHEDULE))
    policy = _policy(has_next_rung=True)

    action = decide(Failed(FailureKind.UNAVAILABLE, None, ""), counters, policy)

    assert action == NextRung(different_agent=False)


def test_unavailable_failure_with_no_more_rungs_exhausts_the_ladder() -> None:
    counters = TurnCounters(unavailable_retries=len(BACKOFF_SCHEDULE))

    action = decide(Failed(FailureKind.UNAVAILABLE, None, ""), counters, _policy())

    assert action == Escalate(EscalationReason.LADDER_EXHAUSTED)


def test_context_failure_hands_off_once() -> None:
    action = decide(Failed(FailureKind.CONTEXT, None, ""), TurnCounters(), _policy())

    assert action == Handoff()


def test_context_failure_after_a_handoff_raises_agent_error() -> None:
    counters = TurnCounters(context_handoffs=1)

    action = decide(Failed(FailureKind.CONTEXT, None, ""), counters, _policy())

    assert action == Escalate(EscalationReason.AGENT_ERROR)


def test_auth_failure_raises_agent_auth() -> None:
    action = decide(Failed(FailureKind.AUTH, None, ""), TurnCounters(), _policy())

    assert action == Escalate(EscalationReason.AGENT_AUTH)


def test_other_failure_retries_once() -> None:
    action = decide(Failed(FailureKind.OTHER, None, ""), TurnCounters(), _policy())

    assert action == RetryTurn()


def test_other_failure_after_a_retry_raises_agent_error() -> None:
    counters = TurnCounters(other_retries=1)

    action = decide(Failed(FailureKind.OTHER, None, ""), counters, _policy())

    assert action == Escalate(EscalationReason.AGENT_ERROR)


def test_pane_died_relaunches_up_to_the_per_turn_limit() -> None:
    action = decide(PaneDied(), TurnCounters(relaunches=1), _policy())

    assert action == Relaunch()


def test_pane_died_after_the_limit_moves_to_the_next_rung() -> None:
    counters = TurnCounters(relaunches=2)
    policy = _policy(has_next_rung=True)

    action = decide(PaneDied(), counters, policy)

    assert action == NextRung(different_agent=False)


def test_pane_died_after_the_limit_with_no_more_rungs_crashes() -> None:
    counters = TurnCounters(relaunches=2)

    action = decide(PaneDied(), counters, _policy())

    assert action == Escalate(EscalationReason.AGENT_CRASH)


def test_stalled_asks_the_helper_to_classify_the_screen() -> None:
    action = decide(Stalled("some screen"), TurnCounters(), _policy())

    assert action == ClassifyScreen()


def test_stalled_beyond_the_per_turn_limit_gets_stuck() -> None:
    counters = TurnCounters(stall_recoveries=2)

    action = decide(Stalled("some screen"), counters, _policy())

    assert action == Escalate(EscalationReason.AGENT_STUCK)


def test_protocol_error_raises_agent_protocol() -> None:
    action = decide(ProtocolError(), TurnCounters(), _policy())

    assert action == Escalate(EscalationReason.AGENT_PROTOCOL)


def test_working_classification_keeps_waiting() -> None:
    classification = ScreenClassification(category="working")

    action = decide_stall(classification, Role.IMPLEMENTER, frozenset(), TurnCounters(), _policy())

    assert action == KeepWaiting()


def test_dialog_classification_sends_keys_that_are_all_safe() -> None:
    classification = ScreenClassification(category="dialog", keys=("Enter",))

    action = decide_stall(
        classification, Role.IMPLEMENTER, frozenset({"Enter", "Escape"}), TurnCounters(), _policy()
    )

    assert action == SendKeys(("Enter",))


def test_dialog_classification_with_an_unsafe_key_gets_stuck() -> None:
    classification = ScreenClassification(category="dialog", keys=("rm -rf /",))

    action = decide_stall(
        classification, Role.IMPLEMENTER, frozenset({"Enter"}), TurnCounters(), _policy()
    )

    assert action == Escalate(EscalationReason.AGENT_STUCK)


def test_question_classification_consults_for_the_implementer() -> None:
    classification = ScreenClassification(category="question")

    action = decide_stall(classification, Role.IMPLEMENTER, frozenset(), TurnCounters(), _policy())

    assert action == Consult()


def test_question_classification_escalates_for_the_leader() -> None:
    classification = ScreenClassification(category="question")

    action = decide_stall(classification, Role.LEADER, frozenset(), TurnCounters(), _policy())

    assert action == DecisionRequired()


def test_quota_classification_behaves_like_a_quota_failure() -> None:
    classification = ScreenClassification(category="quota", resets_at=None)

    action = decide_stall(classification, Role.LEADER, frozenset(), TurnCounters(), _policy())

    assert action == WaitForQuota(until=_NOW + QUOTA_PROBE)


def test_error_classification_behaves_like_an_other_failure() -> None:
    classification = ScreenClassification(category="error")

    action = decide_stall(classification, Role.LEADER, frozenset(), TurnCounters(), _policy())

    assert action == RetryTurn()


def test_decide_rejects_a_completed_result() -> None:
    with pytest.raises(ValueError, match="non-Completed"):
        decide(Completed(report=object()), TurnCounters(), _policy())
