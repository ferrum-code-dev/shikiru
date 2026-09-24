"""`SessionManager.ensure()` against a real tmux pane and the fake agent (03 §4.1)."""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from fake_agent.agent import REPL_COMMAND, SESSION_ID_ENV_VAR

from shikiru.agents.contracts import (
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionKeys,
    SessionStarted,
)
from shikiru.config.settings import AgentConfig
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.domain.runs import NO_USAGE, RunStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Role
from shikiru.engine.sessions import INJECT_CONFIRM_SECONDS
from shikiru.errors import CommandFailed
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_RUNG, build_registry, fake_engine_settings, set_script
from tests.support.factories import TASK_CREATED_AT, make_settings
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
NEVER_STARTING_AGENT_NAME = "never-starts"
NEVER_STARTING_RUNG = Rung(agent=NEVER_STARTING_AGENT_NAME, model="x", effort=None)
RAISING_AGENT_NAME = "raises"
RAISING_RUNG = Rung(agent=RAISING_AGENT_NAME, model="x", effort=None)
ATTACH_RAISING_AGENT_NAME = "attach-raises"
ATTACH_RAISING_RUNG = Rung(agent=ATTACH_RAISING_AGENT_NAME, model="x", effort=None)
QUEUED_AGENT_NAME = "queued"
QUEUED_RUNG = Rung(agent=QUEUED_AGENT_NAME, model="x", effort=None)
DEFERRED_START_AGENT_NAME = "deferred-start"
DEFERRED_START_RUNG = Rung(agent=DEFERRED_START_AGENT_NAME, model="x", effort=None)

_WAIT_TIMEOUT = 15.0


def _live_run(runs: RunStore, task_id: str, role: Role) -> Run:
    live = runs.get_live(task_id, role)
    assert live is not None
    return live


async def _started_run(runs: RunStore, task_id: str, role: Role) -> Run:
    await wait_until(
        lambda: _live_run(runs, task_id, role).session_id is not None, timeout=_WAIT_TIMEOUT
    )
    return _live_run(runs, task_id, role)


def _has_agent_state(runs: RunStore, task_id: str, role: Role) -> bool:
    live = runs.get_live(task_id, role)
    return live is not None and live.agent_state != {}


async def _pane_is_missing(engine_env: EngineEnv, pane_id: str) -> bool:
    return not await engine_env.tmux.pane_exists(pane_id)


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


class _NeverStartingAgent(_CrashingAgent):
    """An `InteractiveAgent` whose pane stays alive without emitting `SessionStarted`."""

    name = NEVER_STARTING_AGENT_NAME

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        await pane.respawn(("sleep", "60"), spec.env, spec.cwd)
        return _NeverSignalsSession()


class _RaisingAgent(_CrashingAgent):
    """An `InteractiveAgent` whose plugin launch fails before returning a session."""

    name = RAISING_AGENT_NAME

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        del spec, pane
        message = "plugin launch failed"
        raise RuntimeError(message)


class _AttachRaisingAgent(_CrashingAgent):
    """An `InteractiveAgent` whose attach fails before returning a session."""

    name = ATTACH_RAISING_AGENT_NAME

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, object]
    ) -> AgentSession:
        del spec, pane, state
        message = "plugin attach failed"
        raise RuntimeError(message)


class _QueuedSession:
    def __init__(self) -> None:
        self._signals: asyncio.Queue[AgentSignal] = asyncio.Queue()

    def push(self, signal: AgentSignal) -> None:
        self._signals.put_nowait(signal)

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            signal = await self._signals.get()
            yield signal
            if isinstance(signal, SessionEnded):
                return

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, object]:
        return {}


class _QueuedAgent(_CrashingAgent):
    name = QUEUED_AGENT_NAME

    def __init__(self) -> None:
        self.session: _QueuedSession | None = None

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        await pane.respawn(("sleep", "60"), spec.env, spec.cwd)
        self.session = _QueuedSession()
        self.session.push(SessionStarted(session_id="queued-session"))
        return self.session


class _DeferredStartAgent(_CrashingAgent):
    name = DEFERRED_START_AGENT_NAME

    def __init__(self) -> None:
        self.session = _QueuedSession()

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        await pane.respawn(("sleep", "60"), spec.env, spec.cwd)
        return self.session


async def test_ensure_relaunches_with_resume_after_pane_death(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "crash"}])

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    first_run = await _started_run(engine_env.runs, task.id, Role.IMPLEMENTER)
    first_session_id = first_run.session_id
    assert first_session_id is not None
    assert first_run.resume_command == f"{SESSION_ID_ENV_VAR}={first_session_id} {REPL_COMMAND}"
    assert engine_env.manager.launched_fresh(task.id, Role.IMPLEMENTER)

    pane_id = task.panes[Role.IMPLEMENTER]
    await engine_env.pane(pane_id).paste_line("go")
    await wait_until(lambda: engine_env.tmux.is_pane_dead(pane_id), timeout=_WAIT_TIMEOUT)

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    second_run = await _started_run(engine_env.runs, task.id, Role.IMPLEMENTER)
    second_session_id = second_run.session_id
    assert second_session_id == first_session_id
    assert second_run.resume_command == f"{SESSION_ID_ENV_VAR}={first_session_id} {REPL_COMMAND}"
    assert len(engine_env.runs.for_task(task.id)) == 2
    assert not engine_env.manager.launched_fresh(task.id, Role.IMPLEMENTER)


async def test_ensure_attaches_after_engine_restart(engine_env: EngineEnv) -> None:
    task = await engine_env.build_task()

    await engine_env.manager.ensure(task, Role.IMPLEMENTER)
    started_session_id = (await _started_run(engine_env.runs, task.id, Role.IMPLEMENTER)).session_id
    assert started_session_id is not None
    pane_id = task.panes[Role.IMPLEMENTER]
    pane_pid_before_restart = await engine_env.tmux.pane_pid(pane_id)
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
    assert runs_for_task[0].resume_command == (
        f"{SESSION_ID_ENV_VAR}={started_session_id} {REPL_COMMAND}"
    )
    assert await engine_env.tmux.pane_pid(pane_id) == pane_pid_before_restart
    assert not second_manager.launched_fresh(task.id, Role.IMPLEMENTER)


async def test_ensure_relaunches_with_resume_on_rung_change(engine_env: EngineEnv) -> None:
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, FAKE_RUNG_2], agents={"fake": AgentConfig()}
    )
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()

    await manager.ensure(task, Role.IMPLEMENTER)
    first_session_id = (await _started_run(engine_env.runs, task.id, Role.IMPLEMENTER)).session_id
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

    second_session_id = (await _started_run(engine_env.runs, task.id, Role.IMPLEMENTER)).session_id
    assert second_session_id == first_session_id
    assert not manager.launched_fresh(task.id, Role.IMPLEMENTER)

    runs_for_task = engine_env.runs.for_task(task.id)
    assert len(runs_for_task) == 2
    assert runs_for_task[0].model == FAKE_RUNG_2.model
    assert runs_for_task[1].model == FAKE_RUNG.model


async def test_restart_with_same_agent_rung_change_relaunches_with_resume(
    engine_env: EngineEnv,
) -> None:
    settings = make_settings(
        implementer_ladder=[FAKE_RUNG, FAKE_RUNG_2], agents={"fake": AgentConfig()}
    )
    first_manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()

    await first_manager.ensure(task, Role.IMPLEMENTER)
    session_id = _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id
    await first_manager.shutdown()

    rung_index = dict(task.rung_index)
    rung_index[Role.IMPLEMENTER] = 1
    transition = dataclasses.replace(Transition.stay(task, step=task.step), rung_index=rung_index)
    engine_env.tasks.apply_transition(task.id, transition)
    updated = engine_env.tasks.get(task.id)
    assert updated is not None

    second_manager = engine_env.build_manager(settings)
    await second_manager.ensure(updated, Role.IMPLEMENTER)

    runs_for_task = engine_env.runs.for_task(task.id)
    assert len(runs_for_task) == 2
    assert runs_for_task[0].model == FAKE_RUNG_2.model
    assert runs_for_task[0].session_id == session_id


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


async def test_exit_task_closes_untracked_persisted_live_runs(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    manager = engine_env.build_manager(fake_engine_settings(), clock=clock)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    await engine_env.pane(task.panes[Role.IMPLEMENTER]).kill()

    exiting = asyncio.create_task(manager.exit_task(task))
    await wait_until(exiting.done, timeout=_WAIT_TIMEOUT)
    await exiting

    assert engine_env.runs.for_task(task.id)[0].id == run.id
    assert engine_env.runs.for_task(task.id)[0].status is RunStatus.CLOSED
    assert clock.now() == TASK_CREATED_AT


async def test_pane_death_right_after_launch_reaches_the_next_subscriber(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    registry.register(CRASHING_AGENT_NAME, lambda _context: _CrashingAgent())
    settings = make_settings(
        implementer_ladder=[CRASHING_RUNG], agents={CRASHING_AGENT_NAME: AgentConfig()}
    )
    manager = engine_env.build_manager(settings, registry=registry)
    task = await engine_env.build_task()

    await manager.ensure(task, Role.IMPLEMENTER)
    await wait_until(
        lambda: engine_env.tmux.is_pane_dead(task.panes[Role.IMPLEMENTER]), timeout=_WAIT_TIMEOUT
    )
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)

    assert isinstance(await asyncio.wait_for(queue.get(), timeout=_WAIT_TIMEOUT), PaneDied)
    await manager.shutdown()


async def test_launch_timeout_is_delivered_as_a_stalled_turn(engine_env: EngineEnv) -> None:
    registry = build_registry()
    registry.register(NEVER_STARTING_AGENT_NAME, lambda _context: _NeverStartingAgent())
    settings = make_settings(
        implementer_ladder=[NEVER_STARTING_RUNG],
        agents={NEVER_STARTING_AGENT_NAME: AgentConfig()},
    )
    timeouts = settings.orchestration.timeouts.model_copy(update={"launch": 1})
    orchestration = settings.orchestration.model_copy(update={"timeouts": timeouts})
    settings = settings.model_copy(update={"orchestration": orchestration})
    clock = FakeClock(TASK_CREATED_AT)
    manager = engine_env.build_manager(settings, clock=clock, registry=registry)
    task = await engine_env.build_task()

    await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    await wait_until(clock.has_pending_sleep, timeout=_WAIT_TIMEOUT)
    clock.advance(2)

    signal = await asyncio.wait_for(queue.get(), timeout=_WAIT_TIMEOUT)
    assert isinstance(signal, Stalled)
    assert _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).status is RunStatus.LIVE

    await manager.shutdown()
    await engine_env.pane(task.panes[Role.IMPLEMENTER]).kill()


async def test_session_id_is_recorded_when_started_arrives_after_launch(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    agent = _DeferredStartAgent()
    registry.register(DEFERRED_START_AGENT_NAME, lambda _context: agent)
    settings = make_settings(
        implementer_ladder=[DEFERRED_START_RUNG],
        agents={DEFERRED_START_AGENT_NAME: AgentConfig()},
    )
    manager = engine_env.build_manager(settings, registry=registry)
    task = await engine_env.build_task()

    await manager.ensure(task, Role.IMPLEMENTER)
    assert _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id is None
    agent.session.push(SessionStarted(session_id="deferred-session"))

    await wait_until(
        lambda: (
            _live_run(engine_env.runs, task.id, Role.IMPLEMENTER).session_id == "deferred-session"
        ),
        timeout=_WAIT_TIMEOUT,
    )
    await manager.shutdown()
    await engine_env.pane(task.panes[Role.IMPLEMENTER]).kill()


async def test_plugin_launch_exception_exits_run_and_kills_unmonitored_pane(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    registry.register(RAISING_AGENT_NAME, lambda _context: _RaisingAgent())
    settings = make_settings(
        implementer_ladder=[RAISING_RUNG], agents={RAISING_AGENT_NAME: AgentConfig()}
    )
    manager = engine_env.build_manager(settings, registry=registry)
    task = await engine_env.build_task()

    with pytest.raises(RuntimeError, match="plugin launch failed"):
        await manager.ensure(task, Role.IMPLEMENTER)

    assert engine_env.runs.get_live(task.id, Role.IMPLEMENTER) is None
    assert engine_env.runs.for_task(task.id)[0].status is RunStatus.EXITED
    await wait_until(
        lambda: _pane_is_missing(engine_env, task.panes[Role.IMPLEMENTER]), timeout=_WAIT_TIMEOUT
    )


async def test_plugin_attach_exception_exits_run_and_kills_unmonitored_pane(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    registry.register(ATTACH_RAISING_AGENT_NAME, lambda _context: _AttachRaisingAgent())
    settings = make_settings(
        implementer_ladder=[ATTACH_RAISING_RUNG],
        agents={ATTACH_RAISING_AGENT_NAME: AgentConfig()},
    )
    manager = engine_env.build_manager(settings, registry=registry)
    task = await engine_env.build_task()
    run = engine_env.runs.start(task.id, Role.IMPLEMENTER, ATTACH_RAISING_RUNG)

    with pytest.raises(RuntimeError, match="plugin attach failed"):
        await manager.ensure(task, Role.IMPLEMENTER)

    assert engine_env.runs.get_live(task.id, Role.IMPLEMENTER) is None
    assert engine_env.runs.for_task(task.id)[0].id == run.id
    assert engine_env.runs.for_task(task.id)[0].status is RunStatus.EXITED
    await wait_until(
        lambda: _pane_is_missing(engine_env, task.panes[Role.IMPLEMENTER]), timeout=_WAIT_TIMEOUT
    )


async def test_injected_line_stays_non_intervention_until_late_prompt_submission(
    engine_env: EngineEnv,
) -> None:
    registry = build_registry()
    agent = _QueuedAgent()
    registry.register(QUEUED_AGENT_NAME, lambda _context: agent)
    settings = make_settings(
        implementer_ladder=[QUEUED_RUNG], agents={QUEUED_AGENT_NAME: AgentConfig()}
    )
    clock = FakeClock(TASK_CREATED_AT)
    manager = engine_env.build_manager(settings, clock=clock, registry=registry)
    task = await engine_env.build_task()
    await manager.ensure(task, Role.IMPLEMENTER)

    injection = asyncio.create_task(manager.inject_line(task, Role.IMPLEMENTER, "continue"))
    try:
        await wait_until(
            lambda: "continue" in manager.injected_lines(task.id, Role.IMPLEMENTER),
            timeout=_WAIT_TIMEOUT,
        )

        def bounded_injection_done() -> bool:
            if not injection.done():
                clock.advance(INJECT_CONFIRM_SECONDS + 1)
            return injection.done()

        await wait_until(bounded_injection_done, timeout=_WAIT_TIMEOUT)
        assert injection.done()
        assert isinstance(injection.result(), Stalled)
        assert "continue" in manager.injected_lines(task.id, Role.IMPLEMENTER)
        assert agent.session is not None
        agent.session.push(PromptSubmitted(text="continue"))
        await wait_until(
            lambda: "continue" not in manager.injected_lines(task.id, Role.IMPLEMENTER),
            timeout=_WAIT_TIMEOUT,
        )

        updated = engine_env.tasks.get(task.id)
        assert updated is not None
        assert Hold.INTERVENTION not in updated.holds
    finally:
        if not injection.done():
            injection.cancel()
        with suppress(asyncio.CancelledError):
            await injection
        await manager.shutdown()
        with suppress(CommandFailed):
            await engine_env.pane(task.panes[Role.IMPLEMENTER]).kill()
