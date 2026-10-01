"""Retention keeps finished history bounded and never prunes what is still needed (05 §2)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_engine
from shikiru.config.settings import RetentionConfig
from shikiru.domain.commands import Pause, Prioritize, Submit
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Status
from shikiru.engine.integration import PullRequestCloser
from shikiru.engine.retention import Retention
from shikiru.paths import run_dir, state_db_path, task_root
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database, format_timestamp
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.notification_cursor import NotificationCursor
from shikiru.store.retention import StateRetention
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.github import GitHub
from tests.support.clock import FakeClock
from tests.support.engine import build_registry, build_tmux, engine_settings
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_HORIZON_DAYS = 30
_SETTINGS = RetentionConfig(
    interval_seconds=60,
    finished_task_days=_HORIZON_DAYS,
    event_days=_HORIZON_DAYS * 3,
    command_days=_HORIZON_DAYS,
)
_PAST_HORIZON = timedelta(days=_HORIZON_DAYS + 1)
_WITHIN_HORIZON = timedelta(days=_HORIZON_DAYS - 1)
_EVIDENCE = "evidence.txt"


@dataclass
class _World:
    root: Path
    connection: Connection
    clock: FakeClock
    tasks: TaskStore
    events: EventLog

    def task(self, status: Status, *, age: timedelta) -> Task:
        task = seed_task(self.connection, self.clock)
        self.tasks.apply_transition(task.id, Transition.stay(task, step=None, status=status))
        self.connection.execute(
            "UPDATE tasks SET finished_at = ? WHERE id = ?",
            (format_timestamp(self.clock.now() - age), task.id),
        )
        self.events.append(EventKind.TASK_STARTED, message="started", task_id=task.id)
        evidence = run_dir(self.root, task.id) / _EVIDENCE
        evidence.parent.mkdir(parents=True)
        evidence.write_text("evidence")
        return task

    def has_evidence(self, task: Task) -> bool:
        return (run_dir(self.root, task.id) / _EVIDENCE).exists()

    def has_events(self, task: Task) -> bool:
        return bool(self.events.since(0, task_id=task.id))

    def retention(self) -> Retention:
        NotificationCursor(self.connection).write(self.events.latest_id())
        return Retention(
            store=StateRetention(self.connection),
            tasks=self.tasks,
            cursor=NotificationCursor(self.connection),
            project_root=self.root,
            has_open_pull_request=PullRequestCloser(
                github=GitHub(SubprocessRunner()), events=self.events, project_root=self.root
            ).has_open,
            clock=self.clock,
            settings=_SETTINGS,
        )

    async def prune(self) -> None:
        self.events.append(EventKind.ENGINE_STARTED, message="newest event")
        await self.retention().run_if_due()


@pytest.fixture
def world(connection: Connection, tmp_path: Path) -> _World:
    clock = FakeClock(TASK_CREATED_AT)
    return _World(
        tmp_path, connection, clock, TaskStore(connection, clock), EventLog(connection, clock)
    )


async def test_a_finished_task_past_the_horizon_loses_its_files_and_events(world: _World) -> None:
    done = world.task(Status.DONE, age=_PAST_HORIZON)
    cancelled = world.task(Status.CANCELLED, age=_PAST_HORIZON)

    await world.prune()

    for task in (done, cancelled):
        assert not world.has_evidence(task)
        assert not world.has_events(task)


def _open_escalation(world: _World, task: Task) -> None:
    EscalationStore(world.connection, world.clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="needs you",
        packet_path="packet.md",
        options=(),
    )


def _keep_worktree(world: _World, task: Task) -> None:
    task_root(world.root, task.id).mkdir(parents=True)


def _hold(world: _World, task: Task) -> None:
    world.tasks.set_holds(task.id, frozenset({Hold.INTERVENTION}))


def _open_pull_request(world: _World, task: Task) -> None:
    world.events.append(
        EventKind.PR_OPENED, message="opened", task_id=task.id, data={"url": "u", "commits": []}
    )


_NEEDED = {
    "within the horizon": (Status.DONE, _WITHIN_HORIZON, None),
    "still running": (Status.RUNNING, _PAST_HORIZON, None),
    "escalated": (Status.ESCALATED, _PAST_HORIZON, _open_escalation),
    "done with an open escalation": (Status.DONE, _PAST_HORIZON, _open_escalation),
    "done with a kept worktree": (Status.DONE, _PAST_HORIZON, _keep_worktree),
    "done under a hold": (Status.DONE, _PAST_HORIZON, _hold),
    "cancelled with its pull request open": (Status.CANCELLED, _PAST_HORIZON, _open_pull_request),
}


@pytest.mark.parametrize(("status", "age", "reason"), _NEEDED.values(), ids=_NEEDED.keys())
async def test_a_task_that_is_still_needed_keeps_its_files_and_events(
    world: _World, status: Status, age: timedelta, reason: Callable[[_World, Task], None] | None
) -> None:
    task = world.task(status, age=age)
    if reason is not None:
        reason(world, task)

    await world.prune()

    assert world.has_evidence(task)
    assert world.has_events(task)


async def test_a_cancelled_task_whose_pull_request_was_withdrawn_is_pruned(world: _World) -> None:
    task = world.task(Status.CANCELLED, age=_PAST_HORIZON)
    _open_pull_request(world, task)
    world.events.append(EventKind.PR_WITHDRAWN, message="withdrawn", task_id=task.id)

    await world.prune()

    assert not world.has_evidence(task)


async def test_an_escalation_resolved_since_no_longer_holds_its_task(world: _World) -> None:
    task = world.task(Status.DONE, age=_PAST_HORIZON)
    _open_escalation(world, task)
    world.connection.execute(
        "UPDATE escalations SET status = ? WHERE task_id = ?",
        (EscalationStatus.RESOLVED.value, task.id),
    )

    await world.prune()

    assert not world.has_evidence(task)


async def test_old_commands_and_engine_events_go_but_pending_work_and_faults_stay(
    world: _World,
) -> None:
    queue = CommandQueue(world.connection, world.clock)
    answered = queue.append(Prioritize(task_id="T-9", priority=1))
    queue.mark_done(answered.id)
    pending = queue.append(Prioritize(task_id="T-9", priority=2))
    old_engine = world.events.append(EventKind.ENGINE_STARTED, message="old start")
    old_fault = world.events.append(EventKind.ENGINE_FAULT, message="fault", data={"scope": "x"})
    world.clock.advance(_PAST_HORIZON.total_seconds() * 3)
    recent = queue.append(Prioritize(task_id="T-9", priority=3))
    queue.mark_done(recent.id)
    undelivered = world.events.append(EventKind.ESCALATION, message="not yet delivered")
    NotificationCursor(world.connection).write(old_fault.id)
    retention = Retention(
        store=StateRetention(world.connection),
        tasks=world.tasks,
        cursor=NotificationCursor(world.connection),
        project_root=world.root,
        has_open_pull_request=lambda _task_id: False,
        clock=world.clock,
        settings=_SETTINGS,
    )

    await retention.run_if_due()

    assert queue.status(answered.id) is None
    assert queue.status(pending.id) is not None
    assert queue.status(recent.id) is not None
    remaining = {event.id for event in world.events.since(0)}
    assert old_engine.id not in remaining
    assert {old_fault.id, undelivered.id} <= remaining


_TASKS = 5
_WAIT_SECONDS = 60.0
_POLL_SECONDS = 0.5
_TASK_DAYS = 1.0


async def test_a_long_lived_engine_keeps_its_run_directories_bounded(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    base = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT, max_parallel=2)
    settings = base.model_copy(
        update={
            "retention": RetentionConfig(
                interval_seconds=1,
                finished_task_days=_TASK_DAYS,
                event_days=_TASK_DAYS,
                command_days=_TASK_DAYS,
            )
        }
    )
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(state_db_path(tmp_path)).connect()
    queue = CommandQueue(connection, clock)
    tasks = TaskStore(connection, clock)
    running = asyncio.create_task(engine.run())

    async def submit(title: str) -> str:
        queued = queue.append(Submit(title=title, request=title))

        def acknowledged() -> bool:
            if clock.has_pending_sleep():
                clock.advance(_POLL_SECONDS)
            outcome = queue.status(queued.id)
            return outcome is not None and outcome.result is not None

        await wait_until(acknowledged, _WAIT_SECONDS)
        outcome = queue.status(queued.id)
        assert outcome is not None
        assert outcome.result is not None
        return str(outcome.result["task_id"])

    def status_of(task_id: str) -> Status:
        task = tasks.get(task_id)
        assert task is not None
        return task.status

    def settled(task_id: str, status: Status) -> bool:
        if clock.has_pending_sleep():
            clock.advance(_POLL_SECONDS)
        return status_of(task_id) is status and not task_root(tmp_path, task_id).exists()

    def started(task_id: str) -> bool:
        if clock.has_pending_sleep():
            clock.advance(_POLL_SECONDS)
        return run_dir(tmp_path, task_id).is_dir()

    try:
        paused = await submit("stays alive")
        await wait_until(lambda: started(paused), _WAIT_SECONDS)
        queue.append(Pause(task_id=paused))
        for number in range(_TASKS):
            finished = await submit(f"short {number}")
            await wait_until(lambda task_id=finished: settled(task_id, Status.DONE), _WAIT_SECONDS)
            clock.advance(timedelta(days=_TASK_DAYS * 2).total_seconds())
        await wait_until(lambda: len(_run_dirs(tmp_path)) <= 2, _WAIT_SECONDS)
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running
        connection.close()

    assert run_dir(tmp_path, paused).is_dir()
    assert len(_run_dirs(tmp_path)) <= 2


def _run_dirs(project_root: Path) -> list[Path]:
    return sorted(run_dir(project_root, "x").parent.iterdir())
