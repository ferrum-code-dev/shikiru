"""Integration tests for `TaskStore` and task id allocation (docs/design/05-data.md §2; 03 §1-2)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from shikiru.agents.contracts import Rung
from shikiru.domain.commands import Submit
from shikiru.domain.reports import NewTask
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Counters, Hold, RepoState, Role, Status, Step, Task, TaskOrigin
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 1, 1, tzinfo=UTC)


def _make_store(tmp_path: Path) -> tuple[TaskStore, CommandQueue, FakeClock]:
    connection = Database(tmp_path / "state.db").open_writer()
    clock = FakeClock(START)
    return TaskStore(connection, clock), CommandQueue(connection, clock), clock


def _submit(
    store: TaskStore, queue: CommandQueue, title: str, depends_on: list[str] | None = None
) -> Task:
    queued = queue.append(
        Submit(task_id="", title=title, request=title, depends_on=depends_on or [])
    )
    assert isinstance(queued.command, Submit)
    return store.insert_from_submit(queued.command)


def test_submit_allocates_sequential_task_ids(tmp_path: Path) -> None:
    _, queue, _ = _make_store(tmp_path)

    ids: list[str] = []
    for _ in range(3):
        queued = queue.append(Submit(task_id="", title="t", request="r"))
        assert isinstance(queued.command, Submit)
        ids.append(queued.command.task_id)

    assert ids == ["T-1", "T-2", "T-3"]


async def test_concurrent_command_append_allocates_unique_task_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    Database(db_path).open_writer()
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
        runner.run(argv(20), timeout=15, cwd=PROJECT_ROOT, check=True),
        runner.run(argv(20), timeout=15, cwd=PROJECT_ROOT, check=True),
    )

    ids = [line for result in results for line in result.stdout.splitlines() if line]

    assert len(ids) == 40
    assert len(set(ids)) == 40


def test_transition_round_trips_step_status_counters_rungs_and_overrides(tmp_path: Path) -> None:
    store, queue, clock = _make_store(tmp_path)
    task = _submit(store, queue, "t")

    transition = Transition(
        step=Step.IMPLEMENT,
        status=Status.RUNNING,
        iteration=2,
        counters=Counters(consults=1, rejections_on_rung=1),
        rung_index={Role.LEADER: 1},
        overrides={Role.IMPLEMENTER: Rung(agent="codex", model="sol", effort="high")},
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


def test_apply_transition_sets_finished_at_once_for_a_terminal_status(tmp_path: Path) -> None:
    store, queue, clock = _make_store(tmp_path)
    task = _submit(store, queue, "t")
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


def test_insert_child_allocates_a_new_task_id_with_parent_and_origin(tmp_path: Path) -> None:
    store, queue, _ = _make_store(tmp_path)
    parent = _submit(store, queue, "parent")
    dep = _submit(store, queue, "dep")

    child = store.insert_child(
        NewTask(title="Subtask", request="Do the subtask", priority=5),
        depends_on=[dep.id],
        parent_id=parent.id,
        origin=TaskOrigin.LEADER,
    )

    assert child.id == "T-3"
    assert child.parent_id == parent.id
    assert child.origin is TaskOrigin.LEADER
    assert child.priority == 5
    assert child.depends_on == [dep.id]
    assert child.status is Status.QUEUED


def test_scheduling_fields_round_trip(tmp_path: Path) -> None:
    store, queue, clock = _make_store(tmp_path)
    task = _submit(store, queue, "t")

    store.set_holds(task.id, frozenset({Hold.PAUSED}))
    store.set_priority(task.id, 7)
    store.set_wait_reason(task.id, WaitReason.DEPENDENCY)
    store.set_slot(task.id, 2)
    store.set_window(task.id, "@3")
    store.set_panes(task.id, {Role.LEADER: "%1", Role.IMPLEMENTER: "%2"})
    store.set_repos(
        task.id, [RepoState(path=".", base="main", base_sha="abc123", branch="shikiru/T-1")]
    )
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
    assert reloaded.repos == [
        RepoState(path=".", base="main", base_sha="abc123", branch="shikiru/T-1")
    ]
    assert reloaded.requeued_at == clock.now()
    assert reloaded.summary == "All done"

    store.set_wait_reason(task.id, None)
    cleared = store.get(task.id)
    assert cleared is not None
    assert cleared.wait_reason is None


def test_list_queued_active_and_all_partition_by_status(tmp_path: Path) -> None:
    store, queue, _ = _make_store(tmp_path)
    queued = _submit(store, queue, "queued")
    running = _submit(store, queue, "running")
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

    assert [task.id for task in store.list_queued()] == [queued.id]
    assert [task.id for task in store.list_active()] == [running.id]
    assert {task.id for task in store.list_all()} == {queued.id, running.id}


def test_repoint_dependents_moves_every_dependency_onto_the_new_tasks(tmp_path: Path) -> None:
    store, queue, _ = _make_store(tmp_path)

    old = _submit(store, queue, "old")
    dependent = _submit(store, queue, "dependent", depends_on=[old.id])
    unrelated = _submit(store, queue, "unrelated")
    new_a = _submit(store, queue, "new-a")
    new_b = _submit(store, queue, "new-b")

    store.repoint_dependents(old.id, [new_a.id, new_b.id])

    reloaded_dependent = store.get(dependent.id)
    reloaded_unrelated = store.get(unrelated.id)
    assert reloaded_dependent is not None
    assert reloaded_unrelated is not None
    assert set(reloaded_dependent.depends_on) == {new_a.id, new_b.id}
    assert reloaded_unrelated.depends_on == []
