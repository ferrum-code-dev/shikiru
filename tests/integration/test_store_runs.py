"""Integration tests for `RunStore` (docs/design/05-data.md §2; 03 §4.1-4.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung, Usage
from shikiru.domain.commands import Submit
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Role
from shikiru.store.commands import CommandQueue
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
RUNG = Rung(agent="codex", model="sol", effort="high")


def _make_store(connection: Connection) -> tuple[RunStore, str]:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(task_id="", title="t", request="r"))
    assert isinstance(queued.command, Submit)
    task = TaskStore(connection, clock).insert_from_submit(queued.command)
    return RunStore(connection, clock), task.id


def test_get_live_returns_none_until_a_run_is_started(connection: Connection) -> None:
    store, task_id = _make_store(connection)

    assert store.get_live(task_id, Role.IMPLEMENTER) is None

    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    assert store.get_live(task_id, Role.IMPLEMENTER) == run


def test_exit_removes_a_run_from_get_live_but_keeps_it_in_history(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.exit(run.id)

    assert store.get_live(task_id, Role.IMPLEMENTER) is None
    [only_run] = store.for_task(task_id)
    assert only_run.status is RunStatus.EXITED
    assert only_run.ended_at is not None


def test_set_session_accepts_a_session_that_cannot_be_resumed(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.set_session(run.id, None, None)

    reloaded = store.get_live(task_id, Role.IMPLEMENTER)
    assert reloaded is not None
    assert reloaded.session_id is None
    assert reloaded.resume_command is None


def test_session_agent_state_message_and_usage_round_trip(connection: Connection) -> None:
    store, task_id = _make_store(connection)
    run = store.start(task_id, Role.IMPLEMENTER, RUNG)

    store.set_session(run.id, "session-1", "codex resume session-1")
    store.set_agent_state(run.id, {"turns": 2})
    store.set_last_message(run.id, "Working on it")
    store.record_usage(
        run.id,
        Usage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.42,
            cost_basis="billed",
            context_used_pct=None,
        ),
    )

    reloaded = store.get_live(task_id, Role.IMPLEMENTER)
    assert reloaded is not None
    assert reloaded.session_id == "session-1"
    assert reloaded.resume_command == "codex resume session-1"
    assert reloaded.agent_state == {"turns": 2}
    assert reloaded.last_message == "Working on it"
    assert reloaded.usage.input_tokens == 100
    assert reloaded.usage.output_tokens == 50
    assert reloaded.usage.cost_usd == 0.42
