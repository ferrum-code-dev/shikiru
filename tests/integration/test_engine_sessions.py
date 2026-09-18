"""`SessionManager.ensure()` against a real tmux pane and the fake agent (03 §4.1)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, SessionEnded, SessionKeys
from shikiru.config.settings import AgentConfig
from shikiru.domain.runs import NO_USAGE, RunStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role
from shikiru.engine.sessions import SessionLaunchFailedError
from tests.support.engine import FAKE_RUNG, build_registry, set_script
from tests.support.factories import make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from shikiru.agents.contracts import (
        AgentSession,
        AgentSignal,
        CheckResult,
        HeadlessAgent,
        LaunchSpec,
        PaneHandle,
        QuotaWindow,
        Usage,
    )
    from shikiru.domain.runs import Run
    from shikiru.store.runs import RunStore
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

FAKE_RUNG_2 = Rung(agent="fake", model="fake-2", effort=None)
CRASHING_AGENT_NAME = "crashes"
CRASHING_RUNG = Rung(agent=CRASHING_AGENT_NAME, model="x", effort=None)

_WAIT_TIMEOUT = 15.0


def _live_run(runs: RunStore, task_id: str, role: Role) -> Run:
    live = runs.get_live(task_id, role)
    assert live is not None
    return live


def _has_agent_state(runs: RunStore, task_id: str, role: Role) -> bool:
    live = runs.get_live(task_id, role)
    return live is not None and live.agent_state != {}


class _NeverSignalsSession:
    """An `AgentSession` double whose launch never reaches `SessionStarted` (04 §1)."""

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            await asyncio.Event().wait()
            yield SessionEnded()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, object]:
        return {}


class _CrashingAgent:
    """An `InteractiveAgent` whose launch kills the pane before ever starting a session."""

    name = CRASHING_AGENT_NAME
    keys = SessionKeys(interrupt=(), exit_input="exit", safe=())

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        await pane.respawn(("true",), spec.env, spec.cwd)
        return _NeverSignalsSession()

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, object]
    ) -> AgentSession:
        raise NotImplementedError

    async def quota(self) -> list[QuotaWindow]:
        return []

    async def doctor(self) -> list[CheckResult]:
        return []

    def resume_command(self, session_id: str, cwd: Path) -> str | None:
        del session_id, cwd
        return None

    def headless(self) -> HeadlessAgent | None:
        return None


async def test_ensure_relaunches_with_resume_after_pane_death(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "crash"}])

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    first_session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    assert first_session_id is not None
    assert engine_env.manager.launched_fresh(task.id, Role.IMPLEMENTER)

    pane_id = task.panes[Role.IMPLEMENTER]
    await engine_env.pane(pane_id).paste_line("go")
    await wait_until(lambda: engine_env.tmux.is_pane_dead(pane_id), timeout=_WAIT_TIMEOUT)

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    second_session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    assert second_session_id == first_session_id
    assert len(engine_env.runs.for_task(task.id)) == 2
    assert not engine_env.manager.launched_fresh(task.id, Role.IMPLEMENTER)


async def test_ensure_attaches_after_engine_restart(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    started_session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    await wait_until(
        lambda: _has_agent_state(engine_env.runs, task.id, Role.IMPLEMENTER), timeout=_WAIT_TIMEOUT
    )
    await engine_env.manager.shutdown()

    settings = make_settings(implementer_ladder=[FAKE_RUNG], agents={"fake": AgentConfig()})
    second_manager = engine_env.build_manager(settings)
    await second_manager.ensure(task, Role.IMPLEMENTER)

    runs_for_task = engine_env.runs.for_task(task.id)
    assert len(runs_for_task) == 1
    assert runs_for_task[0].session_id == started_session_id
    assert not second_manager.launched_fresh(task.id, Role.IMPLEMENTER)


async def test_ensure_relaunches_with_resume_on_rung_change(engine_env: EngineEnv) -> None:
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, FAKE_RUNG_2], agents={"fake": AgentConfig()}
    )
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()

    await manager.ensure(task, Role.IMPLEMENTER)
    first_session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    old_session_queue = manager.subscribe(task.id, Role.IMPLEMENTER)

    rung_index = dict(task.rung_index)
    rung_index[Role.IMPLEMENTER] = 1
    transition = dataclasses.replace(Transition.stay(task, step=task.step), rung_index=rung_index)
    engine_env.tasks.apply_transition(task.id, transition)
    task = engine_env.tasks.get(task.id)
    assert task is not None

    await manager.ensure(task, Role.IMPLEMENTER)

    await wait_until(lambda: not old_session_queue.empty(), timeout=_WAIT_TIMEOUT)
    old_signal = old_session_queue.get_nowait()
    assert isinstance(old_signal, SessionEnded)

    second_session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    assert second_session_id == first_session_id
    assert not manager.launched_fresh(task.id, Role.IMPLEMENTER)

    runs_for_task = engine_env.runs.for_task(task.id)
    assert len(runs_for_task) == 2
    assert runs_for_task[0].model == FAKE_RUNG_2.model
    assert runs_for_task[1].model == FAKE_RUNG.model


async def test_exit_task_sends_exit_input_and_closes_the_run(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    run_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).id
    pane_id = task.panes[Role.IMPLEMENTER]

    await engine_env.manager.exit_task(task)

    assert await engine_env.tmux.is_pane_dead(pane_id)
    exited_run = engine_env.runs.for_task(task.id)[0]
    assert exited_run.id == run_id
    assert exited_run.status == RunStatus.CLOSED


async def test_ensure_exits_the_run_and_leaks_no_task_when_launch_fails(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    registry.register(CRASHING_AGENT_NAME, lambda _context: _CrashingAgent())
    settings = make_settings(
        implementer_ladder=[CRASHING_RUNG], agents={CRASHING_AGENT_NAME: AgentConfig()}
    )
    manager = engine_env.build_manager(settings, registry=registry)
    task = await engine_env.build_task()

    tasks_before = asyncio.all_tasks()
    with pytest.raises(SessionLaunchFailedError):
        await manager.ensure(task, Role.IMPLEMENTER)
    assert asyncio.all_tasks() <= tasks_before

    assert engine_env.runs.get_live(task.id, Role.IMPLEMENTER) is None
    first_run = engine_env.runs.for_task(task.id)[0]
    assert first_run.status == RunStatus.EXITED

    with pytest.raises(SessionLaunchFailedError):
        await manager.ensure(task, Role.IMPLEMENTER)
    assert len(engine_env.runs.for_task(task.id)) == 2
    assert engine_env.runs.get_live(task.id, Role.IMPLEMENTER) is None
