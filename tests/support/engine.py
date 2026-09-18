"""Real-tmux, real-SQLite scaffolding shared by `engine` integration tests."""

from __future__ import annotations

import dataclasses
import uuid
from typing import TYPE_CHECKING

from fake_agent.agent import create as create_fake_agent

from shikiru.agents.registry import AgentRegistry
from shikiru.clock import SystemClock
from shikiru.config.settings import AgentConfig
from shikiru.domain.commands import Submit
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.engine.agents import AgentPool
from shikiru.engine.sessions import SessionManager
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.pane import TmuxPane
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_AGENT_NAME = "fake"


def build_registry() -> AgentRegistry:
    """An `AgentRegistry` with the `fake` agent registered directly (no entry point needed)."""
    registry = AgentRegistry()
    registry.register(FAKE_AGENT_NAME, create_fake_agent)
    return registry


def fake_agent_settings() -> dict[str, AgentConfig]:
    """The `[agents.fake]` table every `AgentPool.get("fake")` call needs to find."""
    return {FAKE_AGENT_NAME: AgentConfig()}


def build_task_roots(runner: SubprocessRunner, project_root: Path) -> TaskRootBuilder:
    """A `TaskRootBuilder` for `project_root`; only its pure `task_root()` lookup is used."""
    return TaskRootBuilder(Git(runner), project_root)


def build_tmux(tmux_server: TmuxServer) -> Tmux:
    """A `Tmux` client scoped to `tmux_server`'s private socket."""
    return Tmux(tmux_server.runner, socket=tmux_server.socket_name)


def build_decorator(tmux: Tmux) -> PaneDecorator:
    """A `PaneDecorator` over `tmux`."""
    return PaneDecorator(tmux)


_REMAIN_ON_EXIT_OPTION = "remain-on-exit"
_REMAIN_ON_EXIT_ON = "on"


async def new_agent_pane(tmux: Tmux, cwd: Path) -> str:
    """Create a window with `remain-on-exit on`, mirroring a real task window (06 §5.1).

    Without it, tmux closes a dead agent pane's window instead of leaving it for
    `SessionManager` (and, later, `LayoutGuard`) to observe and act on.
    """
    name = f"engine-test-{uuid.uuid4().hex[:8]}"
    pane_id = await tmux.new_window("test", name=name, cwd=cwd)
    await tmux.set_window_option(f"test:{name}", _REMAIN_ON_EXIT_OPTION, _REMAIN_ON_EXIT_ON)
    return pane_id


async def create_running_task(
    connection: Connection,
    clock: Clock,
    *,
    title: str,
    pane_id: str,
    role: Role = Role.IMPLEMENTER,
    slot: int = 1,
) -> Task:
    """Insert a task and drive it straight to `running`/`implement`, with `role`'s pane set.

    Mirrors what the scheduler does at dispatch (03 §10), just enough for `SessionManager`
    tests to have a task that already holds a slot and a pane.
    """
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(task_id="", title=title, request=title))
    assert isinstance(queued.command, Submit)
    submitted = tasks.insert_from_submit(queued.command)
    transition = dataclasses.replace(
        Transition.stay(submitted, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
    )
    tasks.apply_transition(submitted.id, transition)
    tasks.set_slot(submitted.id, slot)
    tasks.set_panes(submitted.id, {role: pane_id})
    task = tasks.get(submitted.id)
    assert task is not None
    return task


def build_stores(connection: Connection, clock: Clock) -> tuple[TaskStore, RunStore]:
    """`TaskStore` and `RunStore` over the same connection and clock."""
    return TaskStore(connection, clock), RunStore(connection, clock)


def build_session_manager(
    *,
    tmux: Tmux,
    tasks: TaskStore,
    runs: RunStore,
    project_root: Path,
    settings: Settings,
    clock: Clock,
    agent_clock: Clock | None = None,
    pane_clock: Clock | None = None,
) -> SessionManager:
    """A `SessionManager` wired to the `fake` agent, `tmux` and the given stores.

    `agent_clock`/`pane_clock` default to a fresh `SystemClock`-backed pair distinct from
    `clock`: production wires one `Clock` everywhere, but a test driving `clock` as a
    `FakeClock` must not also freeze the fake agent's own real-time hook polling or the pane's
    real-time paste delay with it.
    """
    runner = SubprocessRunner()
    resolved_pane_clock = pane_clock if pane_clock is not None else SystemClock()
    agent_pool = AgentPool(
        build_registry(),
        runner,
        settings,
        agent_clock if agent_clock is not None else SystemClock(),
    )
    return SessionManager(
        agent_pool,
        lambda pane_id: TmuxPane(tmux, pane_id, resolved_pane_clock),
        tasks=tasks,
        runs=runs,
        task_roots=build_task_roots(runner, project_root),
        project_root=project_root,
        settings=settings,
        clock=clock,
        decorator=build_decorator(tmux),
    )
