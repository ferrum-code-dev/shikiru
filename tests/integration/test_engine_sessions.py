"""`SessionManager.ensure()` against a real tmux pane and the fake agent (03 §4.1)."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.clock import SystemClock
from shikiru.domain.runs import RunStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role
from shikiru.process import SubprocessRunner
from shikiru.tmux.pane import TmuxPane
from tests.support.engine import (
    build_session_manager,
    build_stores,
    build_task_roots,
    build_tmux,
    create_running_task,
    fake_agent_settings,
    new_agent_pane,
)
from tests.support.factories import make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.store.runs import RunStore
    from shikiru.tmux.client import Tmux
    from shikiru.workspace.roots import TaskRootBuilder
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
FAKE_RUNG_2 = Rung(agent="fake", model="fake-2", effort=None)

_WAIT_TIMEOUT = 15.0


async def _set_script(tmux_server: TmuxServer, steps: list[dict[str, object]]) -> None:
    await tmux_server.run("set-environment", "-g", "SHIKIRU_FAKE_SCRIPT", json.dumps(steps))


def _live_run(runs: RunStore, task_id: str, role: Role) -> Run:
    live = runs.get_live(task_id, role)
    assert live is not None
    return live


def _has_agent_state(runs: RunStore, task_id: str, role: Role) -> bool:
    live = runs.get_live(task_id, role)
    return live is not None and live.agent_state != {}


async def _build_task(
    tmux: Tmux, connection: Connection, tmp_path: Path, task_roots: TaskRootBuilder
) -> Task:
    pane_id = await new_agent_pane(tmux, tmp_path)
    task = await create_running_task(connection, SystemClock(), title="t", pane_id=pane_id)
    task_roots.task_root(task.id).mkdir(parents=True)
    return task


async def test_ensure_relaunches_with_resume_after_pane_death(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    tasks, runs = build_stores(connection, SystemClock())
    manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )
    task = await _build_task(tmux, connection, tmp_path, task_roots)
    await _set_script(tmux_server, [{"kind": "crash"}])

    try:
        await manager.ensure(task, Role.IMPLEMENTER)
        first_session_id = _live_run(runs, task.id, Role.IMPLEMENTER).session_id
        assert first_session_id is not None
        assert manager.launched_fresh(task.id, Role.IMPLEMENTER)

        pane_id = task.panes[Role.IMPLEMENTER]
        pane = TmuxPane(tmux, pane_id, SystemClock())
        await pane.paste_line("go")
        await wait_until(lambda: tmux.is_pane_dead(pane_id), timeout=_WAIT_TIMEOUT)

        await manager.ensure(task, Role.IMPLEMENTER)
        second_session_id = _live_run(runs, task.id, Role.IMPLEMENTER).session_id
        assert second_session_id == first_session_id
        assert len(runs.for_task(task.id)) == 2
        assert not manager.launched_fresh(task.id, Role.IMPLEMENTER)
    finally:
        await manager.shutdown()


async def test_ensure_attaches_after_engine_restart(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    tasks, runs = build_stores(connection, SystemClock())
    first_manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )
    task = await _build_task(tmux, connection, tmp_path, task_roots)

    try:
        await first_manager.ensure(task, Role.IMPLEMENTER)
        started_session_id = _live_run(runs, task.id, Role.IMPLEMENTER).session_id
        await wait_until(
            lambda: _has_agent_state(runs, task.id, Role.IMPLEMENTER), timeout=_WAIT_TIMEOUT
        )
    finally:
        await first_manager.shutdown()

    second_manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )
    try:
        await second_manager.ensure(task, Role.IMPLEMENTER)

        runs_for_task = runs.for_task(task.id)
        assert len(runs_for_task) == 1
        assert runs_for_task[0].session_id == started_session_id
        assert not second_manager.launched_fresh(task.id, Role.IMPLEMENTER)
    finally:
        await second_manager.shutdown()


async def test_ensure_relaunches_with_resume_on_rung_change(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, FAKE_RUNG_2], agents=fake_agent_settings()
    )
    tasks, runs = build_stores(connection, SystemClock())
    manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )
    task = await _build_task(tmux, connection, tmp_path, task_roots)

    try:
        await manager.ensure(task, Role.IMPLEMENTER)
        first_session_id = _live_run(runs, task.id, Role.IMPLEMENTER).session_id

        rung_index = dict(task.rung_index)
        rung_index[Role.IMPLEMENTER] = 1
        transition = dataclasses.replace(
            Transition.stay(task, step=task.step), rung_index=rung_index
        )
        tasks.apply_transition(task.id, transition)
        task = tasks.get(task.id)
        assert task is not None

        await manager.ensure(task, Role.IMPLEMENTER)
        second_session_id = _live_run(runs, task.id, Role.IMPLEMENTER).session_id
        assert second_session_id == first_session_id
        assert not manager.launched_fresh(task.id, Role.IMPLEMENTER)

        runs_for_task = runs.for_task(task.id)
        assert len(runs_for_task) == 2
        assert runs_for_task[0].model == FAKE_RUNG_2.model
        assert runs_for_task[1].model == FAKE_RUNG.model
    finally:
        await manager.shutdown()


async def test_exit_task_sends_exit_input_and_closes_the_run(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents=fake_agent_settings())
    tasks, runs = build_stores(connection, SystemClock())
    manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )
    task = await _build_task(tmux, connection, tmp_path, task_roots)

    await manager.ensure(task, Role.IMPLEMENTER)
    run_id = _live_run(runs, task.id, Role.IMPLEMENTER).id
    pane_id = task.panes[Role.IMPLEMENTER]

    await manager.exit_task(task)

    assert await tmux.is_pane_dead(pane_id)
    exited_run = runs.for_task(task.id)[0]
    assert exited_run.id == run_id
    assert exited_run.status == RunStatus.CLOSED
