"""Real-tmux, real-SQLite scaffolding shared by `engine` integration tests."""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import TYPE_CHECKING

import pytest
from fake_agent.agent import create as create_fake_agent
from fake_agent.repl import SCRIPT_ENV_VAR, role_script_env_var

from shikiru.agents.contracts import Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.clock import SystemClock
from shikiru.config.settings import AgentConfig
from shikiru.domain.commands import Submit
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Role, Status, Step, Task
from shikiru.engine.agents import AgentPool
from shikiru.engine.monitor import SessionMonitor
from shikiru.engine.sessions import SessionManager
from shikiru.engine.titles import TaskTitles
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import PaneDecorator
from shikiru.tmux.pane import TmuxPane
from shikiru.tmux.session import SessionView
from shikiru.workspace.roots import task_root
from tests.support.factories import make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, PaneHandle
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

FAKE_AGENT_NAME = "fake"
FAKE_AGENT_ALIAS = "fake2"
FAKE_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-1", effort=None)

_SESSION_NAME = "test"


def build_registry() -> AgentRegistry:
    """An `AgentRegistry` with the `fake` agent registered directly (no entry point needed)."""
    registry = AgentRegistry()
    registry.register(FAKE_AGENT_NAME, create_fake_agent)
    registry.register(FAKE_AGENT_ALIAS, create_fake_agent)
    return registry


def fake_engine_settings() -> Settings:
    """Settings whose implementer ladder is the fake agent, with every ladder agent configured."""
    return make_settings(implementer_ladder=[FAKE_RUNG], agents={FAKE_AGENT_NAME: AgentConfig()})


def build_tmux(tmux_server: TmuxServer) -> Tmux:
    """A `Tmux` client scoped to `tmux_server`'s private socket."""
    return Tmux(tmux_server.runner, socket=tmux_server.socket_name)


def build_decorator(tmux: Tmux) -> PaneDecorator:
    """A `PaneDecorator` over `tmux`."""
    return PaneDecorator(tmux)


async def new_agent_pane(tmux: Tmux, cwd: Path) -> str:
    """Create a task window with `remain-on-exit on`, mirroring a real task window (06 §5.1)."""
    name = f"engine-test-{uuid.uuid4().hex[:8]}"
    return await SessionView(tmux).create_task_window(_SESSION_NAME, window_name=name, cwd=cwd)


async def set_script(tmux_server: TmuxServer, steps: list[dict[str, object]]) -> None:
    """Set `SHIKIRU_FAKE_SCRIPT` for every fake agent pane on `tmux_server`'s server."""
    await tmux_server.run("set-environment", "-g", SCRIPT_ENV_VAR, json.dumps(steps))


async def set_role_script(
    tmux_server: TmuxServer, role: Role, steps: list[dict[str, object]]
) -> None:
    """Script only `role`'s fake agent panes, leaving every other role on its own script.

    Two roles run in the same tmux server for a `TaskFlow` test, each with its own REPL process
    and `Script` index; setting the shared `SHIKIRU_FAKE_SCRIPT` would have them race through
    the same list in lockstep, so a role that needs a specific sequence gets its own variable
    instead (`fake_agent.repl.role_script_env_var`).
    """
    await tmux_server.run(
        "set-environment", "-g", role_script_env_var(role.value), json.dumps(steps)
    )


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
    events: EventLog,
    titles: TaskTitles,
    registry: AgentRegistry | None = None,
    agent_clock: Clock | None = None,
    pane_clock: Clock | None = None,
) -> SessionManager:
    """A `SessionManager` wired to `registry` (the `fake` agent by default), `tmux` and the stores.

    `agent_clock`/`pane_clock` default to a fresh `SystemClock`-backed pair distinct from
    `clock`: production wires one `Clock` everywhere, but a test driving `clock` as a
    `FakeClock` must not also freeze the fake agent's own real-time hook polling or the pane's
    real-time paste delay with it.
    """
    runner = SubprocessRunner()
    resolved_pane_clock = pane_clock if pane_clock is not None else SystemClock()
    agent_pool = AgentPool(
        (registry if registry is not None else build_registry()).create,
        runner,
        settings,
        agent_clock if agent_clock is not None else SystemClock(),
    )
    return SessionManager(
        agent_pool,
        lambda pane_id: TmuxPane(tmux, pane_id, resolved_pane_clock),
        tasks=tasks,
        runs=runs,
        project_root=project_root,
        settings=settings,
        clock=clock,
        decorator=build_decorator(tmux),
        events=events,
        refresh_titles=titles.refresh,
    )


def make_monitor(
    session: AgentSession,
    pane: PaneHandle,
    *,
    task: Task,
    run: Run,
    run_dir: Path,
    tasks: TaskStore,
    runs: RunStore,
    clock: Clock,
    settings: Settings,
    expects_started: bool,
    events: EventLog,
    refresh_titles: Callable[[Task], Awaitable[None]],
) -> SessionMonitor:
    """A `SessionMonitor` for `task`/`run`, timed from `settings.orchestration`."""
    return SessionMonitor(
        session,
        pane,
        task_id=task.id,
        run_id=run.id,
        run_dir=run_dir,
        tasks=tasks,
        runs=runs,
        clock=clock,
        timeouts=settings.orchestration.timeouts,
        poll_seconds=settings.orchestration.poll_seconds,
        expects_started=expects_started,
        events=events,
        refresh_titles=refresh_titles,
    )


@dataclasses.dataclass
class EngineEnv:
    """Everything an `engine` integration test needs: real tmux, real stores, a live manager."""

    tmux: Tmux
    tasks: TaskStore
    runs: RunStore
    connection: Connection
    project_root: Path
    manager: SessionManager
    _extra_managers: list[SessionManager] = dataclasses.field(default_factory=list)

    def build_manager(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        registry: AgentRegistry | None = None,
        agent_clock: Clock | None = None,
        pane_clock: Clock | None = None,
    ) -> SessionManager:
        """Build another `SessionManager` sharing this env's tmux and stores.

        Shut down automatically alongside `manager` when the fixture tears down.
        """
        manager_clock = clock if clock is not None else SystemClock()
        manager = build_session_manager(
            tmux=self.tmux,
            tasks=self.tasks,
            runs=self.runs,
            project_root=self.project_root,
            settings=settings,
            clock=manager_clock,
            events=EventLog(self.connection, manager_clock),
            titles=TaskTitles(build_decorator(self.tmux), self.runs, settings),
            registry=registry,
            agent_clock=agent_clock,
            pane_clock=pane_clock,
        )
        self._extra_managers.append(manager)
        return manager

    async def shutdown_all(self) -> None:
        """Shut down `manager` and every manager built through `build_manager`."""
        for extra in self._extra_managers:
            await extra.shutdown()
        await self.manager.shutdown()

    async def new_pane(self) -> str:
        """Create a fresh task window pane in this env's tmux, rooted at `project_root`."""
        return await new_agent_pane(self.tmux, self.project_root)

    def pane(self, pane_id: str) -> TmuxPane:
        """A `TmuxPane` for `pane_id` on this env's tmux, timed by a real clock."""
        return TmuxPane(self.tmux, pane_id, SystemClock())

    async def build_task(
        self, *, title: str = "t", role: Role = Role.IMPLEMENTER, slot: int = 1
    ) -> Task:
        """Insert a task running `role` in a fresh pane, with its task root directory created."""
        pane_id = await self.new_pane()
        task = await create_running_task(
            self.connection, SystemClock(), title=title, pane_id=pane_id, role=role, slot=slot
        )
        task_root(self.project_root, task.id).mkdir(parents=True)
        return task


@pytest.fixture
async def engine_env(
    tmux_server: TmuxServer, connection: Connection, tmp_path: Path
) -> AsyncIterator[EngineEnv]:
    """An `EngineEnv` with a default fake-agent, single-rung `SessionManager`.

    The default manager and any built through `EngineEnv.build_manager` are shut down on
    teardown, so tests need no `try`/`finally` of their own just for cleanup.
    """
    tmux = build_tmux(tmux_server)
    tasks, runs = build_stores(connection, SystemClock())
    settings = fake_engine_settings()
    events = EventLog(connection, SystemClock())
    titles = TaskTitles(build_decorator(tmux), runs, settings)
    manager = build_session_manager(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        project_root=tmp_path,
        settings=settings,
        clock=SystemClock(),
        events=events,
        titles=titles,
    )
    env = EngineEnv(
        tmux=tmux,
        tasks=tasks,
        runs=runs,
        connection=connection,
        project_root=tmp_path,
        manager=manager,
    )
    try:
        yield env
    finally:
        await env.shutdown_all()
