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
    PromptSubmitted,
    SessionStarted,
)
from shikiru.domain.ladder import pick_rung
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.domain.runs import RunStatus
from shikiru.engine.decorations import decorate_pane
from shikiru.engine.monitor import InjectedLines, SessionMonitor, wait_for_signal
from shikiru.errors import CommandFailed, ShikiruError
from shikiru.workspace.roots import run_dir, task_root

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, InteractiveAgent, Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.decorator import PaneDecorator
    from shikiru.tmux.pane import TmuxPane

EXIT_KILL_DELAY_SECONDS = 10
_EXIT_POLL_INTERVAL_SECONDS = 1
INJECT_CONFIRM_SECONDS = 15


def _run_matches_rung(run: Run, rung: Rung) -> bool:
    return run.agent == rung.agent and run.model == rung.model and run.effort == rung.effort


class SessionLaunchFailedError(ShikiruError):
    """A launch never reached `SessionStarted` because its pane died."""


@dataclass
class _LiveSession:
    pane_id: str
    rung: Rung
    session_id: str | None
    session: AgentSession
    run_id: int
    fresh: bool
    monitor: SessionMonitor
    injected_lines: InjectedLines
    monitor_task: asyncio.Task[None]
    decorate_task: asyncio.Task[None]
    pending_signal: MonitorSignal | None = None


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
        events: EventLog | None = None,
        refresh_titles: Callable[[Task], Awaitable[None]] | None = None,
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
        self._events = events
        self._refresh_titles = refresh_titles
        self._live: dict[tuple[str, Role], _LiveSession] = {}

    async def ensure(self, task: Task, role: Role) -> AgentSession:
        """Ensure a live session for `task`'s `role`, per every row of 03 §4.1's table."""
        rung = pick_rung(task, role, self._settings)
        pane = self._pane(task, role)
        tracked = self._live.get((task.id, role))
        pane_id = task.panes[role]
        if tracked is not None and tracked.pane_id != pane_id:
            await self._forget(task.id, role)
            tracked = None
        if tracked is not None and tracked.monitor_task.done():
            await self._forget(task.id, role)
            tracked = None
        try:
            pane_alive = await pane.is_alive()
        except CommandFailed:
            pane_alive = False

        if tracked is not None:
            if pane_alive and tracked.rung == rung:
                self._sync_started(task, tracked)
                return tracked.session
            if pane_alive and tracked.rung.agent == rung.agent:
                resume_session_id = tracked.session_id
                await self._exit_session(pane, rung.agent)
                await self._await_pane_deaths([pane])
                await self._forget(task.id, role)
                return await self._relaunch(
                    task, role, pane, rung, resume_session_id=resume_session_id
                )
            await self._forget(task.id, role)

        live_run = self._runs.get_live(task.id, role)
        if live_run is not None and live_run.agent == rung.agent:
            if pane_alive and _run_matches_rung(live_run, rung):
                return await self._attach(task, role, pane, rung, live_run)
            if pane_alive:
                await self._exit_session(pane, rung.agent)
                await self._await_pane_deaths([pane])
            return await self._relaunch(
                task, role, pane, rung, resume_session_id=live_run.session_id
            )
        return await self._relaunch(task, role, pane, rung, resume_session_id=None)

    async def handoff(self, task: Task, role: Role) -> AgentSession:
        """Start a fresh session for `task`'s `role` on its current rung (03 §7)."""
        pane = self._pane(task, role)
        tracked = self._live.get((task.id, role))
        if tracked is not None:
            with suppress(CommandFailed):
                await pane.paste_line(self._agent_pool.get(tracked.rung.agent).keys.exit_input)
            await self._await_pane_deaths([pane])
            await self._forget(task.id, role)
        return await self._relaunch(
            task,
            role,
            pane,
            pick_rung(task, role, self._settings),
            resume_session_id=None,
        )

    async def interrupt(self, task: Task) -> None:
        """Interrupt every live agent session belonging to `task` (03 §4.3)."""
        for key, live in tuple(self._live.items()):
            if key[0] != task.id:
                continue
            pane = self._pane(task, key[1])
            with suppress(CommandFailed):
                await pane.send_keys(*self._agent_pool.get(live.rung.agent).keys.interrupt)

    async def inject_line(self, task: Task, role: Role, line: str) -> PaneDied | Stalled | None:
        """Inject one line and retain its pending marker until acceptance or session termination."""
        live = self._live.get((task.id, role))
        if live is None:
            message = f"{task.id} has no live {role.value} session"
            raise ShikiruError(message)
        pane = self._pane(task, role)
        queue = live.monitor.subscribe()
        live.injected_lines.add(line)
        try:
            try:
                await pane.paste_line(line)
            except CommandFailed:
                return PaneDied()
            deadline = self._clock.now() + timedelta(seconds=INJECT_CONFIRM_SECONDS)
            while True:
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    try:
                        screen = await pane.capture_visible()
                    except CommandFailed:
                        return PaneDied()
                    return Stalled(screen=screen)
                signal = await wait_for_signal(queue, self._clock, remaining)
                if isinstance(signal, PromptSubmitted) and signal.text == line:
                    return None
                if isinstance(signal, (PaneDied, Stalled)):
                    return signal
        finally:
            live.monitor.unsubscribe(queue)

    async def exit_task(self, task: Task) -> None:
        """Exit every live session `task` still holds, then kill any pane still alive.

        03 §4.1: sessions get `keys.exit_input` and a chance to exit gracefully; a pane still
        alive once `EXIT_KILL_DELAY_SECONDS` have passed is killed outright. Polling for death
        instead of always sleeping the full delay means the common case (every agent exits
        promptly) doesn't pay for the worst case.
        """
        tracked = {key: live for key, live in self._live.items() if key[0] == task.id}
        for key, live in tracked.items():
            pane = self._pane(task, key[1])
            await self._exit_session(pane, live.rung.agent)
            await self._forget(*key)
        for run in self._runs.for_task(task.id):
            if run.status is RunStatus.LIVE:
                self._runs.close(run.id)
        survivors = await self._await_pane_deaths(
            [self._pane_factory(pane_id) for pane_id in task.panes.values()]
        )
        for pane in survivors:
            with suppress(CommandFailed):
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
            alive: list[TmuxPane] = []
            for pane in survivors:
                try:
                    if await pane.is_alive():
                        alive.append(pane)
                except CommandFailed:
                    continue
            survivors = alive
            if not survivors or self._clock.now() >= deadline:
                return survivors
            await self._clock.sleep(_EXIT_POLL_INTERVAL_SECONDS)
        return survivors

    async def _attach(
        self, task: Task, role: Role, pane: TmuxPane, rung: Rung, run: Run
    ) -> AgentSession:
        agent = self._agent_pool.get(rung.agent)
        spec = self._build_spec(task, role, rung, resume_session_id=run.session_id)
        try:
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
        except BaseException:
            await self._abort_unmonitored_run(run.id, pane)
            raise

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
        try:
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
        except BaseException:
            await self._abort_unmonitored_run(run.id, pane)
            raise

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
            events=self._events,
            refresh_titles=self._refresh_titles,
        )
        decoration_queue = monitor.subscribe()
        monitor_task = asyncio.ensure_future(monitor.run())
        try:
            decorate_task: asyncio.Task[None] = asyncio.ensure_future(
                decorate_pane(
                    decoration_queue,
                    pane_target=task.panes[role],
                    role=role,
                    decorator=self._decorator,
                )
            )
        except BaseException:
            await self._cancel_tasks(monitor_task)
            self._runs.exit(run_id)
            raise
        startup_signal: MonitorSignal | None = None
        if expects_started:
            start_queue = monitor.subscribe()
            try:
                startup = await self._await_started(start_queue)
            except BaseException:
                monitor.unsubscribe(start_queue)
                await self._cancel_tasks(monitor_task, decorate_task)
                self._runs.exit(run_id)
                raise
            monitor.unsubscribe(start_queue)
            startup_signal = startup if not isinstance(startup, SessionStarted) else None
            if isinstance(startup, SessionStarted):
                session_id = startup.session_id
                resume_command = (
                    agent.resume_command(session_id, task_root(self._project_root, task.id))
                    if session_id is not None
                    else None
                )
                self._runs.set_session(run_id, session_id, resume_command)
        live = _LiveSession(
            pane_id=task.panes[role],
            rung=rung,
            session_id=session_id,
            session=session,
            run_id=run_id,
            fresh=fresh,
            monitor=monitor,
            injected_lines=injected_lines,
            monitor_task=monitor_task,
            decorate_task=decorate_task,
            pending_signal=startup_signal,
        )
        self._live[(task.id, role)] = live
        try:
            self._sync_started(task, live)
        except BaseException:
            self._live.pop((task.id, role), None)
            await self._cancel_tasks(monitor_task, decorate_task)
            self._runs.exit(run_id)
            raise
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
        live = self._live[(task_id, role)]
        queue = live.monitor.subscribe()
        if live.pending_signal is not None:
            if live.monitor.launch_pending:
                queue.put_nowait(live.pending_signal)
            live.pending_signal = None
        return queue

    def unsubscribe(self, task_id: str, role: Role, queue: asyncio.Queue[MonitorSignal]) -> None:
        """Stop `queue` from receiving `task_id`'s `role` session's signals."""
        self._live[(task_id, role)].monitor.unsubscribe(queue)

    def injected_lines(self, task_id: str, role: Role) -> InjectedLines:
        """Return `task_id`'s `role` session's `InjectedLines`, for its `TurnRunner` (03 §9)."""
        return self._live[(task_id, role)].injected_lines

    def launch_pending(self, task_id: str, role: Role) -> bool:
        """Return whether `task_id`'s `role` monitor still awaits `SessionStarted`."""
        return self._live[(task_id, role)].monitor.launch_pending

    def clear_intervention(self, task_id: str, role: Role) -> None:
        """Restore the role decoration after its intervention hold clears."""
        live = self._live.get((task_id, role))
        if live is not None:
            live.monitor.clear_intervention()

    def rearm_stall_detection(self, task_id: str, role: Role) -> None:
        """Restart the current session's stall deadline after a working-screen classification."""
        self._live[(task_id, role)].monitor.rearm_stall_detection()

    async def shutdown(self) -> None:
        """Cancel every live session's monitor and decorator, touching no pane or agent.

        For engine shutdown: once the process is stopping, nothing needs to keep polling a
        session that is about to disappear anyway.
        """
        for key in list(self._live):
            await self._forget(*key)

    @staticmethod
    async def _await_started(
        queue: asyncio.Queue[MonitorSignal],
    ) -> SessionStarted | Stalled:
        while True:
            signal = await queue.get()
            if isinstance(signal, SessionStarted):
                return signal
            if isinstance(signal, Stalled):
                return signal
            if isinstance(signal, PaneDied):
                message = "session launch did not start: pane died"
                raise SessionLaunchFailedError(message)

    async def _exit_session(self, pane: TmuxPane, agent_name: str) -> None:
        with suppress(CommandFailed):
            await pane.paste_line(self._agent_pool.get(agent_name).keys.exit_input)

    async def _abort_unmonitored_run(self, run_id: int, pane: TmuxPane) -> None:
        self._runs.exit(run_id)
        try:
            pane_alive = await pane.is_alive()
        except CommandFailed:
            return
        if pane_alive:
            with suppress(CommandFailed):
                await pane.kill()

    def _sync_started(self, task: Task, live: _LiveSession) -> None:
        session_id = live.monitor.started_session_id
        if live.session_id is not None or session_id is None:
            return
        live.session_id = session_id
        agent = self._agent_pool.get(live.rung.agent)
        resume_command = agent.resume_command(session_id, task_root(self._project_root, task.id))
        self._runs.set_session(live.run_id, session_id, resume_command)

    async def _forget(self, task_id: str, role: Role) -> None:
        live = self._live.pop((task_id, role), None)
        if live is None:
            return
        live.injected_lines.clear()
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
