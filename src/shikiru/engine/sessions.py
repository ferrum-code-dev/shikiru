"""`SessionManager`: ensures a live `AgentSession` per task role (03 §4.1).

`ensure()` covers every row of 03 §4.1's table: an already-monitored session is returned as
is; an engine restart reattaches to a still-running pane; a dead pane with a known session id
relaunches with resume; a rung change on the same agent exits and relaunches with resume under
the new model; anything else launches fresh. Every launched or relaunched session is handed
straight to a `SessionMonitor`, so nothing but the monitor ever reads its `signals()`.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.agents.contracts import AgentContext, LaunchSpec, SessionStarted
from shikiru.config.settings import AgentConfig
from shikiru.domain.ladder import pick_rung
from shikiru.engine.monitor import PaneDied, SessionMonitor, Stalled, decorate_pane
from shikiru.errors import CommandFailed, ShikiruError
from shikiru.tmux.pane import TmuxPane
from shikiru.workspace.roots import run_dir

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, InteractiveAgent, Rung
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.process import SubprocessRunner
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.client import Tmux
    from shikiru.tmux.decorator import PaneDecorator
    from shikiru.workspace.roots import TaskRootBuilder

PROJECT_ENV_VAR = "SHIKIRU_PROJECT"
TASK_ENV_VAR = "SHIKIRU_TASK"
SLOT_ENV_VAR = "SHIKIRU_SLOT"

EXIT_KILL_DELAY_SECONDS = 10


class SessionLaunchFailedError(ShikiruError):
    """A launch never reached `SessionStarted`: the pane died, or it stalled past its timeout."""


@dataclass
class _LiveSession:
    rung: Rung
    session_id: str | None
    session: AgentSession
    run_id: int
    monitor: SessionMonitor
    monitor_task: asyncio.Task[None]
    decorate_task: asyncio.Task[None]


class SessionManager:
    """Ensures, resumes, attaches and exits one `AgentSession` per task role (03 §4.1)."""

    def __init__(
        self,
        registry: AgentRegistry,
        runner: SubprocessRunner,
        *,
        agent_clock: Clock,
        tmux: Tmux,
        pane_clock: Clock,
        tasks: TaskStore,
        runs: RunStore,
        task_roots: TaskRootBuilder,
        project_root: Path,
        settings: Settings,
        clock: Clock,
        decorator: PaneDecorator,
    ) -> None:
        """Build sessions through `registry`/`runner`/`agent_clock`, monitor with `clock`.

        `pane_clock` times pane injection (`TmuxPane`'s real-time paste delay); `clock` times
        this manager's own polling and timeouts. In production both are the same `SystemClock`;
        tests may split them so the fake agent's own real-time hook polling isn't frozen by a
        `FakeClock` driving deterministic stall/launch-timeout assertions.
        """
        self._registry = registry
        self._runner = runner
        self._agent_clock = agent_clock
        self._tmux = tmux
        self._pane_clock = pane_clock
        self._tasks = tasks
        self._runs = runs
        self._task_roots = task_roots
        self._project_root = project_root
        self._settings = settings
        self._clock = clock
        self._decorator = decorator
        self._agents: dict[str, InteractiveAgent] = {}
        self._live: dict[tuple[str, Role], _LiveSession] = {}

    async def ensure(self, task: Task, role: Role) -> AgentSession:
        """Ensure a live session for `task`'s `role`, per every row of 03 §4.1's table."""
        rung = pick_rung(task, role, self._settings)
        pane = self._pane(task, role)
        pane_alive = await pane.is_alive()
        tracked = self._live.get((task.id, role))

        if tracked is not None:
            if pane_alive and tracked.rung == rung:
                return tracked.session
            if pane_alive and tracked.rung.agent == rung.agent:
                resume_session_id = tracked.session_id
                await pane.paste_line(self._agent(rung.agent).keys.exit_input)
                await self._forget(task.id, role)
                return await self._relaunch(
                    task, role, pane, rung, resume_session_id=resume_session_id
                )
            await self._forget(task.id, role)

        live_run = self._runs.get_live(task.id, role)
        if live_run is not None and live_run.agent == rung.agent:
            if pane_alive:
                return await self._attach(task, role, pane, rung, live_run)
            return await self._relaunch(
                task, role, pane, rung, resume_session_id=live_run.session_id
            )
        return await self._relaunch(task, role, pane, rung, resume_session_id=None)

    async def exit_task(self, task: Task) -> None:
        """Exit every live session `task` still holds, then kill its panes (03 §4.1 task end)."""
        for key in [key for key in self._live if key[0] == task.id]:
            live = self._live[key]
            pane = self._pane(task, key[1])
            with suppress(CommandFailed):
                await pane.paste_line(self._agent(live.rung.agent).keys.exit_input)
            self._runs.close(live.run_id)
            await self._forget(*key)
        await self._clock.sleep(EXIT_KILL_DELAY_SECONDS)
        for pane_id in task.panes.values():
            if not await self._tmux.is_pane_dead(pane_id):
                await self._tmux.kill_pane(pane_id)

    async def _attach(
        self, task: Task, role: Role, pane: TmuxPane, rung: Rung, run: Run
    ) -> AgentSession:
        agent = self._agent(rung.agent)
        spec = self._build_spec(task, role, rung, resume_session_id=run.session_id)
        session = await agent.attach(spec, pane, run.agent_state)
        return await self._start_monitoring(
            task,
            role,
            pane=pane,
            rung=rung,
            agent=agent,
            run_id=run.id,
            session_id=run.session_id,
            session=session,
            expects_started=False,
        )

    async def _relaunch(
        self,
        task: Task,
        role: Role,
        pane: TmuxPane,
        rung: Rung,
        *,
        resume_session_id: str | None,
    ) -> AgentSession:
        live_run = self._runs.get_live(task.id, role)
        if live_run is not None:
            self._runs.exit(live_run.id)
        run = self._runs.start(task.id, role, rung)
        agent = self._agent(rung.agent)
        spec = self._build_spec(task, role, rung, resume_session_id=resume_session_id)
        session = await agent.launch(spec, pane)
        return await self._start_monitoring(
            task,
            role,
            pane=pane,
            rung=rung,
            agent=agent,
            run_id=run.id,
            session_id=None,
            session=session,
            expects_started=True,
        )

    async def _start_monitoring(
        self,
        task: Task,
        role: Role,
        *,
        pane: TmuxPane,
        rung: Rung,
        agent: InteractiveAgent,
        run_id: int,
        session_id: str | None,
        session: AgentSession,
        expects_started: bool,
    ) -> AgentSession:
        monitor = SessionMonitor(
            session,
            pane,
            task_id=task.id,
            run_id=run_id,
            run_dir=run_dir(self._project_root, task.id),
            tasks=self._tasks,
            runs=self._runs,
            clock=self._clock,
            timeouts=self._settings.orchestration.timeouts,
            poll_seconds=self._settings.orchestration.poll_seconds,
            expects_started=expects_started,
        )
        decoration_queue = monitor.subscribe()
        monitor_task = asyncio.ensure_future(monitor.run())
        decorate_task = asyncio.ensure_future(
            decorate_pane(
                decoration_queue,
                pane_target=task.panes[role],
                role=role,
                decorator=self._decorator,
            )
        )
        if expects_started:
            start_queue = monitor.subscribe()
            try:
                session_id = await self._await_started(start_queue)
            finally:
                monitor.unsubscribe(start_queue)
            resume_command = (
                agent.resume_command(session_id, self._task_roots.task_root(task.id))
                if session_id is not None
                else None
            )
            self._runs.set_session(run_id, session_id, resume_command)
        self._live[(task.id, role)] = _LiveSession(
            rung=rung,
            session_id=session_id,
            session=session,
            run_id=run_id,
            monitor=monitor,
            monitor_task=monitor_task,
            decorate_task=decorate_task,
        )
        return session

    def subscribe(self, task_id: str, role: Role) -> asyncio.Queue[MonitorSignal]:
        """Subscribe to `task_id`'s `role` session's fan-out (the only way to read its signals)."""
        return self._live[(task_id, role)].monitor.subscribe()

    def unsubscribe(self, task_id: str, role: Role, queue: asyncio.Queue[MonitorSignal]) -> None:
        """Stop `queue` from receiving `task_id`'s `role` session's signals."""
        self._live[(task_id, role)].monitor.unsubscribe(queue)

    @staticmethod
    async def _await_started(queue: asyncio.Queue[MonitorSignal]) -> str | None:
        while True:
            signal = await queue.get()
            if isinstance(signal, SessionStarted):
                return signal.session_id
            if isinstance(signal, (Stalled, PaneDied)):
                message = "session launch did not start: " + (
                    "pane died" if isinstance(signal, PaneDied) else "stalled"
                )
                raise SessionLaunchFailedError(message)

    async def _forget(self, task_id: str, role: Role) -> None:
        live = self._live.pop((task_id, role), None)
        if live is None:
            return
        for task in (live.monitor_task, live.decorate_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _agent(self, name: str) -> InteractiveAgent:
        cached = self._agents.get(name)
        if cached is not None:
            return cached
        config = self._settings.agents.get(name, AgentConfig())
        context = AgentContext(options=config.options, runner=self._runner, clock=self._agent_clock)
        agent = self._registry.create(name, context)
        self._agents[name] = agent
        return agent

    def _pane(self, task: Task, role: Role) -> TmuxPane:
        pane_id = task.panes.get(role)
        if pane_id is None:
            message = f"{task.id} has no pane assigned for {role.value}"
            raise ShikiruError(message)
        return TmuxPane(self._tmux, pane_id, self._pane_clock)

    def _build_spec(
        self, task: Task, role: Role, rung: Rung, *, resume_session_id: str | None
    ) -> LaunchSpec:
        return LaunchSpec(
            rung=rung,
            role=role.value,
            cwd=self._task_roots.task_root(task.id),
            run_dir=run_dir(self._project_root, task.id),
            add_dirs=(),
            env={
                PROJECT_ENV_VAR: self._settings.project.name,
                TASK_ENV_VAR: task.id,
                SLOT_ENV_VAR: str(self._require_slot(task)),
            },
            resume_session_id=resume_session_id,
        )

    @staticmethod
    def _require_slot(task: Task) -> int:
        if task.slot is None:
            message = f"{task.id} has no assigned slot; ensure() needs a dispatched task"
            raise ShikiruError(message)
        return task.slot
