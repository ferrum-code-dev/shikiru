"""The asyncio engine daemon: liveness, command dispatch and task supervision."""

from __future__ import annotations

import asyncio
import fcntl
import logging
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Self

from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Status
from shikiru.engine.handlers import (
    CancelHandler,
    HandlerContext,
    HandlerRegistry,
    PauseHandler,
    PrioritizeHandler,
    ResolveHandler,
    ResumeHandler,
    RetryHandler,
    SayHandler,
    SetRungHandler,
    StopHandler,
    SubmitHandler,
)
from shikiru.errors import CommandRejectedError, ShikiruError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from typing import TextIO

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.engine.flow import TaskFlow
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.notifier import Notifier
    from shikiru.engine.quota_poller import QuotaPoller
    from shikiru.engine.scheduler import Scheduler
    from shikiru.engine.sessions import SessionManager
    from shikiru.store.commands import CommandQueue
    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.messages import MessageStore
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.roots import TaskRootBuilder

COMMAND_POLL_SECONDS = 0.25
ENGINE_LOCK_FILENAME = "engine.lock"
ENGINE_LOG_DIR = "logs"
ENGINE_LOG_FILENAME = "engine.log"


class EngineAlreadyRunningError(ShikiruError):
    """The project already has an engine holding its exclusive lock."""


class EngineLock:
    """An exclusive process lock held for exactly one engine lifetime."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None

    def acquire(self) -> None:
        """Acquire the lock without blocking, or raise `EngineAlreadyRunningError`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a+")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            message = f"engine lock is held: {self._path}"
            raise EngineAlreadyRunningError(message) from None

    def release(self) -> None:
        """Release the lock and close its file descriptor."""
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()


def engine_is_running(lock_path: Path) -> bool:
    """Return whether another process currently holds `lock_path`."""
    lock = EngineLock(lock_path)
    try:
        lock.acquire()
    except EngineAlreadyRunningError:
        return True
    lock.release()
    return False


class Engine:
    """Poll commands, run the scheduler and supervise one coroutine per active task."""

    def __init__(
        self,
        *,
        lock_path: Path,
        command_queue: CommandQueue,
        tasks: TaskStore,
        runs: RunStore,
        escalations: EscalationStore,
        events: EventLog,
        quotas: QuotaStore,
        messages: MessageStore,
        scheduler: Scheduler,
        sessions: SessionManager,
        flow: TaskFlow,
        roots: TaskRootBuilder,
        layout: LayoutGuard,
        quota_poller: QuotaPoller,
        notifier: Notifier,
        settings: Settings,
        project_root: Path,
        clock: Clock,
        close_state: Callable[[], None],
        refresh_titles: Callable[[Task], Awaitable[None]],
    ) -> None:
        """Wire the engine's stores, orchestration components and lifecycle callbacks."""
        self._lock = EngineLock(lock_path)
        self._commands = command_queue
        self._tasks = tasks
        self._runs = runs
        self._escalations = escalations
        self._events = events
        self._quotas = quotas
        self._messages = messages
        self._scheduler = scheduler
        self._sessions = sessions
        self._flow = flow
        self._roots = roots
        self._layout = layout
        self._quota_poller = quota_poller
        self._notifier = notifier
        self._settings = settings
        self._project_root = project_root
        self._clock = clock
        self._close_state = close_state
        self._stop = asyncio.Event()
        self._flow_tasks: dict[str, asyncio.Task[None]] = {}
        self._finished_tasks: set[str] = set()
        self._last_notified_event = 0
        self._last_scheduled_event = 0
        self._last_quota_event = 0
        self._logger = logging.getLogger(f"shikiru.engine.{project_root}")
        context = HandlerContext(
            tasks=tasks,
            escalations=escalations,
            events=events,
            messages=messages,
            sessions=sessions,
            project_root=project_root,
            cancel_task=self.cancel_task,
            start_task=self.start_task,
            finish_task=self.finish_task,
            set_consult_question=flow.set_consult_question,
            stop_engine=self.request_stop,
            refresh_titles=refresh_titles,
        )
        self._handlers = HandlerRegistry(
            [
                SubmitHandler(context),
                PrioritizeHandler(context),
                PauseHandler(context),
                ResumeHandler(context),
                CancelHandler(context),
                RetryHandler(context),
                SetRungHandler(context),
                SayHandler(context),
                ResolveHandler(context),
                StopHandler(context),
            ]
        )
        self._log_handler = self._configure_logging(lock_path)

    async def run(self) -> None:
        """Hold liveness, process commands and schedule tasks until `Stop` or cancellation."""
        try:
            self._lock.acquire()
        except BaseException:
            self._close_logging()
            self._close_state()
            raise
        poller_task: asyncio.Task[None] | None = None
        try:
            self._last_notified_event = self._events.latest_id()
            poller_task = self._quota_poller.start()
            self._events.append(EventKind.ENGINE_STARTED, message="engine started")
            next_tick = self._clock.now()
            while not self._stop.is_set():
                await self._supervise_flows()
                commands_changed = await self._process_commands()
                if self._stop.is_set():
                    break
                await self._poll_after_quota_failure()
                now = self._clock.now()
                events_changed = self._events.since(self._last_scheduled_event)
                if events_changed:
                    self._last_scheduled_event = max(event.id for event in events_changed)
                if commands_changed or events_changed or now >= next_tick:
                    await self._layout.restore(self._tasks.list_active())
                    schedule = await self._scheduler.run_once()
                    for dispatch in schedule.dispatches:
                        await self.start_task(dispatch.task_id)
                    await self._layout.restore(self._tasks.list_active())
                    scheduled_events = self._events.since(self._last_scheduled_event)
                    if scheduled_events:
                        self._last_scheduled_event = max(event.id for event in scheduled_events)
                    next_tick = now + timedelta(seconds=self._settings.orchestration.poll_seconds)
                await self._notify_events()
                await self._clock.sleep(COMMAND_POLL_SECONDS)
            await self._supervise_flows()
        finally:
            await self._shutdown(poller_task)

    def request_stop(self) -> None:
        """Ask the command loop to stop after acknowledging the current command."""
        self._stop.set()

    async def start_task(self, task_id: str) -> None:
        """Schedule a task flow once, leaving supervision in this engine."""
        existing = self._flow_tasks.get(task_id)
        if existing is not None and not existing.done():
            return
        self._flow_tasks[task_id] = asyncio.create_task(self._run_flow(task_id))

    async def cancel_task(self, task_id: str) -> None:
        """Interrupt and await one supervised flow coroutine, if present."""
        current = self._flow_tasks.get(task_id)
        if current is None:
            return
        current.cancel()
        with suppress(asyncio.CancelledError):
            await current
        self._flow_tasks.pop(task_id, None)

    async def finish_task(self, task: Task) -> None:
        """Close sessions/window and clean the task root after terminal cancellation."""
        if task.id in self._finished_tasks:
            return
        await self._sessions.exit_task(task)
        await self._layout.close(task)
        await self._roots.cleanup(task.id, task.repo)
        self._finished_tasks.add(task.id)

    async def _process_commands(self) -> bool:
        changed = False
        for queued in self._commands.pending():
            try:
                result = await self._handlers.handle(queued.command)
            except (CommandRejectedError, ShikiruError) as error:
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                continue
            except Exception as error:
                self._logger.exception("command %s failed", queued.id)
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                continue
            self._commands.mark_done(queued.id, result)
            changed = True
        return changed

    async def _poll_after_quota_failure(self) -> None:
        events = self._events.since(self._last_quota_event)
        if events:
            self._last_quota_event = max(event.id for event in events)
        if any(event.kind is EventKind.QUOTA_BLOCKED for event in events):
            await self._quota_poller.poll_once()

    async def _run_flow(self, task_id: str) -> None:
        try:
            await self._flow.run(task_id)
        finally:
            current = self._tasks.get(task_id)
            if current is not None and (
                current.status is Status.CANCELLED
                or (current.status is Status.DONE and current.status_reason is not None)
            ):
                await self.finish_task(current)

    async def _supervise_flows(self) -> None:
        for task_id, flow_task in tuple(self._flow_tasks.items()):
            if not flow_task.done():
                continue
            self._flow_tasks.pop(task_id, None)
            if flow_task.cancelled():
                continue
            error = flow_task.exception()
            if error is not None:
                self._logger.error(
                    "task flow %s failed",
                    task_id,
                    exc_info=(type(error), error, error.__traceback__),
                )
                raise error

    async def _notify_events(self) -> None:
        for event in self._events.since(self._last_notified_event):
            self._last_notified_event = event.id
            await self._notifier.notify(event)

    async def _shutdown(self, poller_task: asyncio.Task[None] | None) -> None:
        try:
            self._events.append(EventKind.ENGINE_STOPPED, message="engine stopped")
            await self._quota_poller.stop()
            if poller_task is not None:
                poller_task.cancel()
                with suppress(asyncio.CancelledError):
                    await poller_task
            for task in tuple(self._flow_tasks.values()):
                task.cancel()
            if self._flow_tasks:
                await asyncio.gather(*self._flow_tasks.values(), return_exceptions=True)
            self._flow_tasks.clear()
            await self._sessions.shutdown()
        finally:
            try:
                self._lock.release()
            finally:
                self._close_logging()
                self._close_state()

    def _configure_logging(self, lock_path: Path) -> logging.FileHandler:
        log_path = lock_path.parent / ENGINE_LOG_DIR / ENGINE_LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path)
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        return handler

    def _close_logging(self) -> None:
        handler = self._log_handler
        self._logger.removeHandler(handler)
        handler.close()
