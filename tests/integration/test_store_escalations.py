"""Integration tests for `EscalationStore` (docs/design/05-data.md §2; 03 §8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import options_for
from shikiru.domain.tasks import EscalationReason
from shikiru.store.escalations import EscalationStore, fetch_open_escalations
from tests.support.clock import FakeClock
from tests.support.store import seed_task

if TYPE_CHECKING:
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_store(connection: Connection) -> tuple[EscalationStore, str]:
    clock = FakeClock(START)
    task = seed_task(connection, clock)
    return EscalationStore(connection, clock), task.id


def _open(store: EscalationStore, task_id: str):
    reason = EscalationReason.DECISION_REQUIRED
    return store.open(
        task_id,
        step=None,
        reason=reason,
        headline="Need a decision",
        packet_path="runs/T-1/escalations/E-1.md",
        options=options_for(reason),
    )


def test_open_allocates_sequential_e_n_ids(connection: Connection) -> None:
    store, task_id = _make_store(connection)

    first = _open(store, task_id)
    second = _open(store, task_id)

    assert first.id == "E-1"
    assert second.id == "E-2"


def test_resolve_moves_an_escalation_out_of_the_open_list(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    escalation = _open(store, task_id)

    assert [item.id for item in fetch_open_escalations(connection)] == [escalation.id]

    store.resolve(escalation.id, "answered by operator")

    assert fetch_open_escalations(connection) == []
    resolved = store.get(escalation.id)
    assert resolved is not None
    assert resolved.resolution == "answered by operator"
    assert resolved.resolved_at is not None


def test_get_of_an_unknown_escalation_is_none(connection: Connection) -> None:
    store, _ = _make_store(connection)

    assert store.get("E-does-not-exist") is None


def test_latest_answer_uses_the_newest_resolved_answer(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    first = _open(store, task_id)

    assert store.latest_answer(task_id) is None
    store.resolve(first.id, f"{ResolutionKind.ANSWER.value} first guidance")
    retry = _open(store, task_id)
    store.resolve(retry.id, ResolutionKind.RETRY.value)
    assert store.latest_answer(task_id) is None
    latest = _open(store, task_id)
    store.resolve(latest.id, f"{ResolutionKind.ANSWER.value} latest guidance")

    assert store.latest_answer(task_id) == "latest guidance"
