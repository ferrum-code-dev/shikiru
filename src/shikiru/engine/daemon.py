"""The asyncio engine daemon: liveness, command dispatch and task supervision."""

from __future__ import annotations

import fcntl
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import psutil

from shikiru.domain.events import EventKind
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import FileHandler, Logger
    from pathlib import Path
    from typing import TextIO

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.engine.handlers import HandlerRegistry
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.notifier import Notifier
    from shikiru.engine.reconcile import Reconciler
    from shikiru.engine.scheduler import Scheduler
    from shikiru.engine.supervisor import TaskSupervisor
    from shikiru.store.commands import CommandQueue
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore

COMMAND_POLL_SECONDS = 0.25
ENGINE_LOGGER_NAME = "shikiru"
_ENGINE_LOCK_METADATA_PARTS = 2


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
        self._file.seek(0)
        self._file.truncate()
        started_at = psutil.Process(os.getpid()).create_time()
        self._file.write(f"{os.getpid()} {started_at!r}")
        self._file.flush()

    @staticmethod
    def pid_if_running(path: Path) -> int | None:
        """Return the verified PID written by the process that currently holds `path`."""
        try:
            lock_file = path.open()
        except FileNotFoundError:
            return None
        with lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.seek(0)
                contents = lock_file.read().split()
                if len(contents) != _ENGINE_LOCK_METADATA_PARTS:
                    return None
                try:
                    pid = int(contents[0])
                    started_at = float(contents[1])
                except ValueError:
                    return None
                try:
                    actual_started_at = psutil.Process(pid).create_time()
                except psutil.NoSuchProcess, psutil.ZombieProcess:
                    return None
                return pid if actual_started_at == started_at else None
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return None

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
    try:
        lock_file = lock_path.open("r")
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        lock_file.close()


class Engine:
    """Poll commands, run the scheduler and supervise one coroutine per active task."""

    def __init__(
        self,
        *,
        lock_path: Path,
        command_queue: CommandQueue,
        tasks: TaskStore,
        events: EventLog,
        scheduler: Scheduler,
        task_supervisor: TaskSupervisor,
        handlers: HandlerRegistry,
        layout: LayoutGuard,
        reconciler: Reconciler,
        notifier: Notifier,
        settings: Settings,
        clock: Clock,
        close_state: Callable[[], None],
        logger: Logger,
        log_handler: FileHandler,
    ) -> None:
        """Wire the daemon loop to its stores and bootstrap-built command registry."""
        self._lock = EngineLock(lock_path)
        self._commands = command_queue
        self._tasks = tasks
        self._events = events
        self._scheduler = scheduler
        self._task_supervisor = task_supervisor
        self._handlers = handlers
        self._layout = layout
        self._reconciler = reconciler
        self._notifier = notifier
        self._settings = settings
        self._clock = clock
        self._close_state = close_state
        self._last_notified_event = 0
        self._last_scheduled_event = 0
        self._logger = logger
        self._log_handler = log_handler

    async def run(self) -> None:
        """Hold liveness, process commands and schedule tasks until `Stop` or cancellation."""
        try:
            self._lock.acquire()
        except BaseException:
            self._close_logging()
            self._close_state()
            raise
        try:
            self._last_notified_event = self._events.latest_id()
            self._events.append(EventKind.ENGINE_STARTED, message="engine started")
            await self._reconciler.reconcile()
            next_tick = self._clock.now()
            while not self._task_supervisor.stop_requested:
                await self._task_supervisor.supervise(self._logger)
                commands_changed = await self._process_commands()
                if self._task_supervisor.stop_requested:
                    break
                now = self._clock.now()
                events_changed = self._events.since(self._last_scheduled_event)
                if events_changed:
                    self._last_scheduled_event = max(event.id for event in events_changed)
                if commands_changed or events_changed or now >= next_tick:
                    await self._layout.restore(self._tasks.list_active())
                    schedule = await self._scheduler.run_once()
                    for dispatch in schedule.dispatches:
                        await self._task_supervisor.start_task(dispatch.task_id)
                    scheduled_events = self._events.since(self._last_scheduled_event)
                    if scheduled_events:
                        self._last_scheduled_event = max(event.id for event in scheduled_events)
                    next_tick = now + timedelta(seconds=self._settings.orchestration.poll_seconds)
                await self._notify_events()
                await self._clock.sleep(COMMAND_POLL_SECONDS)
            await self._task_supervisor.supervise(self._logger)
        finally:
            await self._shutdown()

    def request_stop(self) -> None:
        """Ask the command loop to stop after acknowledging the current command."""
        self._task_supervisor.request_stop()

    async def _process_commands(self) -> bool:
        changed = False
        for queued in self._commands.pending():
            try:
                result = await self._handlers.handle(queued.command)
            except ShikiruError as error:
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                continue
            except Exception as error:
                self._logger.exception("command %s failed", queued.id)
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                continue
            self._commands.mark_done(queued.id, result)
            changed = True
        return changed

    async def _notify_events(self) -> None:
        for event in self._events.since(self._last_notified_event):
            self._last_notified_event = event.id
            await self._notifier.notify(event)

    async def _shutdown(self) -> None:
        try:
            self._events.append(EventKind.ENGINE_STOPPED, message="engine stopped")
            await self._task_supervisor.shutdown()
        finally:
            try:
                self._lock.release()
            finally:
                self._close_logging()
                self._close_state()

    def _close_logging(self) -> None:
        handler = self._log_handler
        self._logger.removeHandler(handler)
        handler.close()
