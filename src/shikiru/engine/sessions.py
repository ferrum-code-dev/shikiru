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
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    PROJECT_ENV_VAR,
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    SLOT_ENV_VAR,
    TASK_ENV_VAR,
    LaunchSpec,
    SessionStarted,
)
from shikiru.domain.ladder import pick_rung
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.engine.decorations import decorate_pane
from shikiru.engine.monitor import InjectedLines, SessionMonitor
from shikiru.errors import CommandFailed, ShikiruError
from shikiru.workspace.roots import run_dir, task_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, InteractiveAgent, Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.decorator import PaneDecorator
    from shikiru.tmux.pane import TmuxPane

EXIT_KILL_DELAY_SECONDS = 10
_EXIT_POLL_INTERVAL_SECONDS = 1


class SessionLaunchFailedError(ShikiruError):
    """A launch never reached `SessionStarted`: the pane died, or it stalled past its timeout."""


@dataclass
class _LiveSession:
    rung: Rung
    session_id: str | None
    session: AgentSession
    run_id: int
    fresh: bool
    monitor: SessionMonitor
    injected_lines: InjectedLines
    monitor_task: asyncio.Task[None]
    decorate_task: asyncio.Task[None]


class SessionManager:
    """Ensures, resumes, attaches and exits one `AgentSession` per task role (03 §4.1)."""

    def __init__(
        self,
        agent_pool: AgentPool,
        pane_factory: Callable[[str], TmuxPane],
        *,
        tasks: TaskStore,
        runs: RunStore,
        project_root: Path,
        settings: Settings,
        clock: Clock,
        decorator: PaneDecorator,
    ) -> None:
        """Resolve agents through `agent_pool`, panes through `pane_factory`, time with `clock`."""
        self._agent_pool = agent_pool
        self._pane_factory = pane_factory
        self._tasks = tasks
        self._runs = runs
        self._project_root = project_root
        self._settings = settings
        self._clock = clock
        self._decorator = decorator
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
                await pane.paste_line(self._agent_pool.get(rung.agent).keys.exit_input)
                await self._await_pane_deaths([pane])
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
        """Exit every live session `task` still holds, then kill any pane still alive.

        03 §4.1: sessions get `keys.exit_input` and a chance to exit gracefully; a pane still
        alive once `EXIT_KILL_DELAY_SECONDS` have passed is killed outright. Polling for death
        instead of always sleeping the full delay means the common case (every agent exits
        promptly) doesn't pay for the worst case.
        """
        for key in [key for key in self._live if key[0] == task.id]:
            live = self._live[key]
            pane = self._pane(task, key[1])
            with suppress(CommandFailed):
                await pane.paste_line(self._agent_pool.get(live.rung.agent).keys.exit_input)
            self._runs.close(live.run_id)
            await self._forget(*key)
        survivors = await self._await_pane_deaths(
            [self._pane_factory(pane_id) for pane_id in task.panes.values()]
        )
        for pane in survivors:
            await pane.kill()

    async def _await_pane_deaths(self, panes: list[TmuxPane]) -> list[TmuxPane]:
        """Poll `panes` until every one has died, bounded by `EXIT_KILL_DELAY_SECONDS`.

        Returns whichever panes are still alive once the deadline passes. Polling instead of
        always sleeping the full delay means the common case (every pane dies promptly) doesn't
        pay for the worst case.
        """
        deadline = self._clock.now() + timedelta(seconds=EXIT_KILL_DELAY_SECONDS)
        survivors = panes
        while survivors:
            survivors = [pane for pane in survivors if await pane.is_alive()]
            if not survivors or self._clock.now() >= deadline:
                return survivors
            await self._clock.sleep(_EXIT_POLL_INTERVAL_SECONDS)
        return survivors

    async def _attach(
        self, task: Task, role: Role, pane: TmuxPane, rung: Rung, run: Run
    ) -> AgentSession:
        agent = self._agent_pool.get(rung.agent)
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
            fresh=False,
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
        agent = self._agent_pool.get(rung.agent)
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
            fresh=resume_session_id is None,
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
        fresh: bool,
    ) -> AgentSession:
        injected_lines = InjectedLines()
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
            pending_lines=injected_lines,
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
            except SessionLaunchFailedError:
                monitor.unsubscribe(start_queue)
                await self._cancel_tasks(monitor_task, decorate_task)
                self._runs.exit(run_id)
                raise
            monitor.unsubscribe(start_queue)
            resume_command = (
                agent.resume_command(session_id, task_root(self._project_root, task.id))
                if session_id is not None
                else None
            )
            self._runs.set_session(run_id, session_id, resume_command)
        self._live[(task.id, role)] = _LiveSession(
            rung=rung,
            session_id=session_id,
            session=session,
            run_id=run_id,
            fresh=fresh,
            monitor=monitor,
            injected_lines=injected_lines,
            monitor_task=monitor_task,
            decorate_task=decorate_task,
        )
        return session

    def launched_fresh(self, task_id: str, role: Role) -> bool:
        """Return whether `task_id`'s `role` session was just launched with no prior session.

        03 §4.1's last row: when true, the caller's first turn should be a `handoff` if the
        task already has earlier reports. Resuming, attaching or an unchanged live session are
        all `False`: only a launch with no `resume_session_id` is fresh.
        """
        return self._live[(task_id, role)].fresh

    def subscribe(self, task_id: str, role: Role) -> asyncio.Queue[MonitorSignal]:
        """Subscribe to `task_id`'s `role` session's fan-out (the only way to read its signals)."""
        return self._live[(task_id, role)].monitor.subscribe()

    def unsubscribe(self, task_id: str, role: Role, queue: asyncio.Queue[MonitorSignal]) -> None:
        """Stop `queue` from receiving `task_id`'s `role` session's signals."""
        self._live[(task_id, role)].monitor.unsubscribe(queue)

    def injected_lines(self, task_id: str, role: Role) -> InjectedLines:
        """Return `task_id`'s `role` session's `InjectedLines`, for its `TurnRunner` (03 §9)."""
        return self._live[(task_id, role)].injected_lines

    async def shutdown(self) -> None:
        """Cancel every live session's monitor and decorator, touching no pane or agent.

        For engine shutdown: once the process is stopping, nothing needs to keep polling a
        session that is about to disappear anyway.
        """
        for key in list(self._live):
            await self._forget(*key)

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
        await self._cancel_tasks(live.monitor_task, live.decorate_task)

    @staticmethod
    async def _cancel_tasks(*tasks: asyncio.Task[None]) -> None:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _pane(self, task: Task, role: Role) -> TmuxPane:
        pane_id = task.panes.get(role)
        if pane_id is None:
            message = f"{task.id} has no pane assigned for {role.value}"
            raise ShikiruError(message)
        return self._pane_factory(pane_id)

    def _build_spec(
        self, task: Task, role: Role, rung: Rung, *, resume_session_id: str | None
    ) -> LaunchSpec:
        task_run_dir = run_dir(self._project_root, task.id)
        return LaunchSpec(
            rung=rung,
            role=role.value,
            cwd=task_root(self._project_root, task.id),
            run_dir=task_run_dir,
            add_dirs=(),
            env={
                RUN_DIR_ENV_VAR: str(task_run_dir),
                ROLE_ENV_VAR: role.value,
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
