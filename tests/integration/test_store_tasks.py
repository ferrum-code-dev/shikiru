"""Integration tests for `TaskStore` and task id allocation (docs/design/05-data.md §2; 03 §1-2)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.domain.commands import Submit
from shikiru.domain.reports import NewTask
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import (
    Counters,
    Hold,
    RepoState,
    Role,
    Status,
    StatusReason,
    Step,
    Task,
    TaskOrigin,
)
from shikiru.errors import UsageError
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.store import seed_task

if TYPE_CHECKING:
    from tests.support.store import Connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 1, 1, tzinfo=UTC)


def _submit(
    connection: Connection, clock: FakeClock, title: str, depends_on: list[str] | None = None
) -> Task:
    return seed_task(connection, clock, title=title, depends_on=depends_on or [])


def test_submit_allocates_sequential_task_ids(connection: Connection) -> None:
    clock = FakeClock(START)
    queue = CommandQueue(connection, clock)

    ids: list[str] = []
    for _ in range(3):
        queued = queue.append(Submit(title="t", request="r"))
        assert isinstance(queued.command, Submit)
        assert queued.command.task_id is not None
        ids.append(queued.command.task_id)

    assert ids == ["T-1", "T-2", "T-3"]


def test_unqueued_submit_cannot_be_inserted_as_a_task(connection: Connection) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)

    with pytest.raises(ValueError, match="no allocated task id"):
        store.insert_from_submit(Submit(title="t", request="r"))


async def test_concurrent_command_append_allocates_unique_task_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    Database(db_path).connect().close()
    runner = SubprocessRunner()

    def argv(count: int) -> list[str]:
        return [
            sys.executable,
            "-m",
            "tests.support.store_submit_process",
            str(db_path),
            str(count),
        ]

    results = await asyncio.gather(
        runner.run(argv(20), timeout=45, cwd=PROJECT_ROOT, check=True),
        runner.run(argv(20), timeout=45, cwd=PROJECT_ROOT, check=True),
    )

    ids = [line for result in results for line in result.stdout.splitlines() if line]

    assert len(ids) == 40
    assert len(set(ids)) == 40


def test_transition_round_trips_step_status_counters_rungs_and_overrides(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    task = _submit(connection, clock, "t")

    transition = Transition(
        step=Step.IMPLEMENT,
        status=Status.RUNNING,
        iteration=2,
        counters=Counters(consults=1, rejections_on_rung=1),
        rung_index={Role.LEADER: 1},
        overrides={Role.IMPLEMENTER: Rung(agent="codex", model="sol", effort="high")},
        status_reason=StatusReason.SUPERSEDED,
    )
    store.apply_transition(task.id, transition)

    reloaded = store.get(task.id)
    assert reloaded is not None
    assert reloaded.step is Step.IMPLEMENT
    assert reloaded.status is Status.RUNNING
    assert reloaded.iteration == 2
    assert reloaded.counters == transition.counters
    assert reloaded.rung_index == transition.rung_index
    assert reloaded.overrides == transition.overrides
    assert reloaded.started_at == clock.now()
    assert reloaded.status_reason is StatusReason.SUPERSEDED


def test_apply_transition_sets_finished_at_once_for_a_terminal_status(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    task = _submit(connection, clock, "t")
    done = Transition(
        step=None, status=Status.DONE, iteration=0, counters=Counters(), rung_index={}, overrides={}
    )

    store.apply_transition(task.id, done)
    first_reload = store.get(task.id)
    assert first_reload is not None
    first_finished_at = first_reload.finished_at
    clock.advance(60)
    store.apply_transition(task.id, done)

    second_reload = store.get(task.id)
    assert second_reload is not None
    assert second_reload.finished_at == first_finished_at


def test_submit_rung_overrides_for_leader_and_implementer_reach_the_db(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    leader_rung = Rung(agent="claude", model="opus", effort="high")
    implementer_rung = Rung(agent="codex", model="sol", effort="high")

    task = seed_task(connection, clock, leader=leader_rung, implementer=implementer_rung)

    assert task.overrides == {Role.LEADER: leader_rung, Role.IMPLEMENTER: implementer_rung}


def test_scheduling_fields_round_trip(connection: Connection) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    task = _submit(connection, clock, "t")

    store.set_holds(task.id, frozenset({Hold.PAUSED}))
    store.set_priority(task.id, 7)
    store.set_wait_reason(task.id, WaitReason.DEPENDENCY)
    store.set_slot(task.id, 2)
    store.set_window(task.id, "@3")
    store.set_panes(task.id, {Role.LEADER: "%1", Role.IMPLEMENTER: "%2"})
    store.set_repo(task.id, RepoState(base="main", base_sha="abc123", branch="shikiru/T-1"))
    store.set_requeued(task.id)
    store.set_summary(task.id, "All done")

    reloaded = store.get(task.id)
    assert reloaded is not None
    assert reloaded.holds == frozenset({Hold.PAUSED})
    assert reloaded.priority == 7
    assert reloaded.wait_reason == WaitReason.DEPENDENCY.value
    assert reloaded.slot == 2
    assert reloaded.window_id == "@3"
    assert reloaded.panes == {Role.LEADER: "%1", Role.IMPLEMENTER: "%2"}
    assert reloaded.repo == RepoState(base="main", base_sha="abc123", branch="shikiru/T-1")
    assert reloaded.requeued_at == clock.now()
    assert reloaded.summary == "All done"

    store.set_wait_reason(task.id, None)
    cleared = store.get(task.id)
    assert cleared is not None
    assert cleared.wait_reason is None


def test_list_active_and_all_preserve_status_partition(connection: Connection) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    queued = _submit(connection, clock, "queued")
    running = _submit(connection, clock, "running")
    store.apply_transition(
        running.id,
        Transition(
            step=Step.IMPLEMENT,
            status=Status.RUNNING,
            iteration=1,
            counters=Counters(),
            rung_index={},
            overrides={},
        ),
    )

    assert [task.id for task in store.list_active()] == [running.id]
    all_tasks = store.list_all()
    assert {task.id for task in all_tasks} == {queued.id, running.id}
    assert [task.id for task in all_tasks if task.status is Status.QUEUED] == [queued.id]


def test_scope_change_rejects_missing_task_dependency_before_mutating(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    parent = _submit(connection, clock, "parent")
    dependent = _submit(connection, clock, "dependent", depends_on=[parent.id])
    transition = Transition(
        step=None,
        status=Status.DONE,
        iteration=0,
        counters=Counters(),
        rung_index={},
        overrides={},
        status_reason=StatusReason.SUPERSEDED,
        repoint_dependents=True,
        enqueue_new_tasks=True,
    )

    with pytest.raises(UsageError):
        store.apply_scope_change(
            parent.id,
            transition,
            [
                NewTask(title="first", request="first"),
                NewTask(title="second", request="second", depends_on=["T-999"]),
            ],
            origin=TaskOrigin.LEADER,
        )

    unchanged_parent = store.get(parent.id)
    unchanged_dependent = store.get(dependent.id)
    assert unchanged_parent is not None
    assert unchanged_dependent is not None
    assert unchanged_parent.status is Status.QUEUED
    assert unchanged_dependent.depends_on == [parent.id]
    assert len(store.list_all()) == 2


def test_scope_change_resolves_forward_sibling_dependencies_atomically(
    connection: Connection,
) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    parent = _submit(connection, clock, "parent")
    external_dependency = _submit(connection, clock, "external dependency")
    dependent = _submit(connection, clock, "dependent", depends_on=[parent.id])
    unrelated = _submit(connection, clock, "unrelated")
    transition = Transition(
        step=None,
        status=Status.DONE,
        iteration=0,
        counters=Counters(),
        rung_index={},
        overrides={},
        status_reason=StatusReason.SUPERSEDED,
        repoint_dependents=True,
        enqueue_new_tasks=True,
    )

    children = store.apply_scope_change(
        parent.id,
        transition,
        [
            NewTask(
                title="first",
                request="first",
                priority=5,
                depends_on=[external_dependency.id, "#2"],
            ),
            NewTask(title="second", request="second"),
        ],
        origin=TaskOrigin.LEADER,
    )

    assert children[0].parent_id == parent.id
    assert children[0].origin is TaskOrigin.LEADER
    assert children[0].priority == 5
    assert set(children[0].depends_on) == {external_dependency.id, children[1].id}
    assert children[1].parent_id == parent.id
    assert children[1].origin is TaskOrigin.LEADER
    reloaded_dependent = store.get(dependent.id)
    reloaded_unrelated = store.get(unrelated.id)
    assert reloaded_dependent is not None
    assert reloaded_unrelated is not None
    assert set(reloaded_dependent.depends_on) == {children[0].id, children[1].id}
    assert reloaded_unrelated.depends_on == []
