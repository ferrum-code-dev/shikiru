"""Orphaned turn processes are ended once per engine life (03 §7)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import AgentConfig, ProjectConfig
from shikiru.domain.tasks import Role
from shikiru.engine.layout_guard import LayoutGuard
from shikiru.engine.reconcile import Reconciler
from shikiru.engine.titles import TaskTitles
from shikiru.engine.turn_processes import TurnProcesses
from shikiru.paths import run_dir
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import OperationalDatabaseError
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import exact_target
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.layout import LayoutBuilder
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.engine import build_fault_reporter, build_tmux
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.waiting import wait_until_advancing

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.tasks import Task
    from tests.support.process_tree import ProcessTree
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10.0
_ADVANCE_SECONDS = 1.0
_STOP_GRACE_SECONDS = 0.5
_TASK_ID = "T-1"
_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)


class _TasksFailingOnce(TaskStore):
    def __init__(self, connection: Connection, clock: Clock, *, fail_on_read: int) -> None:
        super().__init__(connection, clock)
        self._reads = 0
        self._fail_on_read = fail_on_read

    def list_active(self) -> list[Task]:
        self._reads += 1
        if self._reads == self._fail_on_read:
            message = "database is locked"
            raise OperationalDatabaseError(message)
        return super().list_active()


async def _noop(*_: object) -> None:
    return None


async def _reconciler(
    connection: Connection,
    clock: FakeClock,
    tmux_server: TmuxServer,
    project: Path,
    *,
    fail_on_read: int,
) -> Reconciler:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig()},
    ).model_copy(update={"project": ProjectConfig(name="test", trackers=[])})
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", project, home_command=("/bin/sh",))
    tasks = _TasksFailingOnce(connection, clock, fail_on_read=fail_on_read)
    events = EventLog(connection, clock)
    faults = build_fault_reporter(events, clock, settings)
    decorator = PaneDecorator(tmux)
    runs = RunStore(connection, clock)
    layout = LayoutGuard(
        tmux=tmux,
        view=SessionView(tmux),
        builder=LayoutBuilder(tmux),
        decorator=decorator,
        session_target=exact_target(session_name("test")),
        tasks=tasks,
        settings=settings,
        project_root=project,
        events=events,
        runs=runs,
        commands=CommandQueue(connection, clock),
        faults=faults,
        refresh_titles=TaskTitles(decorator, runs, settings).refresh,
    )
    return Reconciler(
        tasks=tasks,
        layout=layout,
        processes=TurnProcesses(runner=SubprocessRunner(), clock=clock, project_root=project),
        resume_task=_noop,
        finish_task=_noop,
        restore_escalation=_noop,
        restore_pull_request_closes=lambda: None,
        faults=faults,
        project_root=project,
    )


async def _until_done(reconciling: asyncio.Task[None], clock: FakeClock) -> None:
    def finished() -> bool:
        return reconciling.done()

    await wait_until_advancing(clock, finished, _WAIT_SECONDS, step_seconds=_ADVANCE_SECONDS)
    await reconciling


async def test_a_reconcile_retried_after_a_late_fault_never_ends_a_turn_started_since(
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
    process_tree: ProcessTree,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    runner = SubprocessRunner()
    reconciler = await _reconciler(connection, clock, tmux_server, tmp_path, fail_on_read=2)
    with pytest.raises(OperationalDatabaseError):
        await _until_done(asyncio.create_task(reconciler.reconcile()), clock)
    turn = await runner.spawn(process_tree.argv)
    process_tree.parent_pid = turn.pid
    descendant = runner.identify(await process_tree.descendant_started())
    assert descendant is not None
    TurnProcesses(runner=runner, clock=clock, project_root=tmp_path).record(
        run_dir(tmp_path, _TASK_ID), Role.IMPLEMENTER, turn.pid
    )

    await _until_done(asyncio.create_task(reconciler.reconcile()), clock)

    assert runner.is_running(descendant)
    await turn.stop(grace=_STOP_GRACE_SECONDS)
