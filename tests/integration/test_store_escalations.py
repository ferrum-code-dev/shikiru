"""Integration tests for `EscalationStore` (docs/design/05-data.md §2; 03 §8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.escalation import options_for
from shikiru.domain.tasks import EscalationReason
from shikiru.store.escalations import EscalationStore
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

    assert [item.id for item in store.list_open()] == [escalation.id]

    store.resolve(escalation.id, "answered by operator")

    assert store.list_open() == []
    resolved = store.get(escalation.id)
    assert resolved is not None
    assert resolved.resolution == "answered by operator"
    assert resolved.resolved_at is not None


def test_get_of_an_unknown_escalation_is_none(connection: Connection) -> None:
    store, _ = _make_store(connection)

    assert store.get("E-does-not-exist") is None
