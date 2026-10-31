from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.engine.events import emit_turn_started
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore

if TYPE_CHECKING:
    from datetime import datetime

    from shikiru.agents.contracts import Rung
    from shikiru.domain.events import Event
    from shikiru.domain.tasks import Role, Task
    from tests.support.clock import FakeClock
    from tests.support.store import Connection

QUOTA_EVENT_DELAY_SECONDS = 1


def record_turn_start(
    connection: Connection,
    clock: FakeClock,
    task: Task,
    role: Role,
    rung: Rung,
) -> None:
    assert task.step is not None
    emit_turn_started(EventLog(connection, clock), task, task.step, role, rung)


def record_quota_wait(
    connection: Connection,
    clock: FakeClock,
    task: Task,
    *,
    role: Role,
    rung: Rung,
    until: datetime,
) -> tuple[Event, Event]:
    record_turn_start(connection, clock, task, role, rung)
    assert task.step is not None
    events = EventLog(connection, clock)
    failure = events.append(
        EventKind.TURN_FAILED,
        task_id=task.id,
        message="quota",
        data={"kind": "quota", "step": task.step.value, "resets_at": None},
    )
    clock.advance(QUOTA_EVENT_DELAY_SECONDS)
    quota_block = events.append(
        EventKind.QUOTA_BLOCKED,
        task_id=task.id,
        message="quota wait",
        data={"agent": rung.agent, "until": until.isoformat()},
    )
    QuotaStore(connection).block(rung.agent, until, "quota")
    return failure, quota_block
