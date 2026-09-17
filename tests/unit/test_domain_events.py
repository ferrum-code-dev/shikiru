from __future__ import annotations

from shikiru.domain.events import EventKind

_EXPECTED_KINDS = {
    "task_queued",
    "task_started",
    "step_started",
    "turn_started",
    "turn_completed",
    "turn_failed",
    "nudge",
    "helper_action",
    "rung_changed",
    "quota_blocked",
    "quota_cleared",
    "verdict",
    "scope_change",
    "hold_set",
    "hold_cleared",
    "intervention",
    "operator_message",
    "escalation",
    "escalation_resolved",
    "integration_started",
    "pr_opened",
    "task_done",
    "task_cancelled",
    "pane_restored",
    "engine_started",
    "engine_stopped",
}


def test_event_kind_matches_the_documented_set() -> None:
    assert {kind.value for kind in EventKind} == _EXPECTED_KINDS
