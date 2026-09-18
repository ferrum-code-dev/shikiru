"""`SessionMonitor`: stall detection, launch timeout and intervention scoping (03 §4.2, §9)."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import Rung, SessionEnded
from shikiru.clock import SystemClock
from shikiru.domain.runs import NO_USAGE
from shikiru.domain.tasks import Hold, Role
from shikiru.engine.monitor import SessionMonitor, Stalled
from shikiru.process import SubprocessRunner
from shikiru.tmux.pane import TmuxPane
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_session_manager,
    build_stores,
    build_task_roots,
    build_tmux,
    create_running_task,
    new_agent_pane,
)
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from shikiru.agents.contracts import AgentSignal, Usage
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)

_SIGNAL_TIMEOUT = 15.0


async def _set_script(tmux_server: TmuxServer, steps: list[dict[str, object]]) -> None:
    await tmux_server.run("set-environment", "-g", "SHIKIRU_FAKE_SCRIPT", json.dumps(steps))


class _SilentSession:
    """An `AgentSession` double that never emits a signal (04 §1)."""

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            await asyncio.Event().wait()
            yield SessionEnded()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        return NO_USAGE

    def snapshot(self) -> Mapping[str, Any]:
        return {}


class _AlwaysAlivePane:
    """A `PaneHandle` double whose process never dies (04 §1)."""

    async def respawn(self, argv: object, env: object, cwd: object) -> None:
        del argv, env, cwd

    async def capture_visible(self) -> str:
        return "stub screen"

    async def is_alive(self) -> bool:
        return True


async def test_stall_is_published_after_the_configured_timeout(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(implementer_ladder=[FAKE_RUNG])
    tasks, runs = build_stores(connection, clock)
    manager = build_session_manager(
        tmux=tmux, tasks=tasks, runs=runs, project_root=tmp_path, settings=settings, clock=clock
    )
    pane_id = await new_agent_pane(tmux, tmp_path)
    task = await create_running_task(connection, clock, title="t", pane_id=pane_id)
    task_roots.task_root(task.id).mkdir(parents=True)
    await _set_script(tmux_server, [{"kind": "stall"}])

    await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)

    pane = TmuxPane(tmux, pane_id, SystemClock())
    await pane.paste_line("go")

    await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
    clock.advance(settings.orchestration.timeouts.stall + 1)

    async def _stall_seen() -> bool:
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        return isinstance(signal, Stalled)

    await wait_until(_stall_seen, timeout=_SIGNAL_TIMEOUT)


async def test_launch_timeout_publishes_stalled(connection: Connection, tmp_path: Path) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tasks, runs = build_stores(connection, clock)
    task = await create_running_task(connection, clock, title="t", pane_id="%0")
    run = runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    settings = make_settings(implementer_ladder=[FAKE_RUNG])
    monitor = SessionMonitor(
        _SilentSession(),
        _AlwaysAlivePane(),
        task_id=task.id,
        run_id=run.id,
        run_dir=tmp_path / "run",
        tasks=tasks,
        runs=runs,
        clock=clock,
        timeouts=settings.orchestration.timeouts,
        poll_seconds=settings.orchestration.poll_seconds,
        expects_started=True,
    )
    queue = monitor.subscribe()
    run_task = asyncio.ensure_future(monitor.run())
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(settings.orchestration.timeouts.launch + 1)
        signal = await asyncio.wait_for(queue.get(), timeout=_SIGNAL_TIMEOUT)
        assert isinstance(signal, Stalled)
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def test_intervention_hold_from_a_typed_prompt_is_scoped_to_its_own_task(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    task_roots = build_task_roots(SubprocessRunner(), tmp_path)
    settings = make_settings(implementer_ladder=[FAKE_RUNG])
    tasks, runs = build_stores(connection, SystemClock())
    manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
    )

    pane_a = await new_agent_pane(tmux, tmp_path)
    task_a = await create_running_task(connection, SystemClock(), title="a", pane_id=pane_a)
    task_roots.task_root(task_a.id).mkdir(parents=True)

    pane_b = await new_agent_pane(tmux, tmp_path)
    task_b = await create_running_task(connection, SystemClock(), title="b", pane_id=pane_b)
    task_roots.task_root(task_b.id).mkdir(parents=True)

    await _set_script(tmux_server, [{"kind": "no_report"}])
    await manager.ensure(task_a, Role.IMPLEMENTER)
    await manager.ensure(task_b, Role.IMPLEMENTER)

    pane = TmuxPane(tmux, pane_a, SystemClock())
    await pane.paste_line("hello from the operator")

    def _has_intervention(task_id: str) -> bool:
        task = tasks.get(task_id)
        return task is not None and Hold.INTERVENTION in task.holds

    await wait_until(lambda: _has_intervention(task_a.id), timeout=_SIGNAL_TIMEOUT)
    assert not _has_intervention(task_b.id)
