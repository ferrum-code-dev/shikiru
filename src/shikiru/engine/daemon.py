"""The asyncio engine daemon: liveness, command dispatch and task supervision."""

from __future__ import annotations

import fcntl
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import psutil

from shikiru.domain.commands import COMMAND_POLL_SECONDS, Stop
from shikiru.domain.events import STOP_REASON_KEY, EventKind
from shikiru.errors import ShikiruError
from shikiru.private import create_private_file, make_private_dir
from shikiru.process import ProcessIdentity, identify_pid
from shikiru.store.database import OperationalDatabaseError

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import FileHandler, Logger
    from pathlib import Path
    from typing import TextIO

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.engine.handlers import HandlerRegistry
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.notification_delivery import NotificationDelivery
    from shikiru.engine.notification_feed import NotificationFeed
    from shikiru.engine.pull_request_closing import PullRequestClosing
    from shikiru.engine.reconcile import Reconciler
    from shikiru.engine.scheduler import Scheduler
    from shikiru.engine.session_guard import SessionGuard
    from shikiru.engine.supervisor import TaskSupervisor
    from shikiru.engine.tick_guard import TickGuard
    from shikiru.store.commands import CommandQueue
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore

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
        make_private_dir(self._path.parent)
        create_private_file(self._path)
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
                return pid if ProcessIdentity(pid, started_at).matches(identify_pid(pid)) else None
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
        session_guard: SessionGuard,
        tick_guard: TickGuard,
        command_guard: TickGuard,
        reconciler: Reconciler,
        notifications: NotificationDelivery,
        notification_feed: NotificationFeed,
        pull_request_closing: PullRequestClosing,
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
        self._session_guard = session_guard
        self._tick_guard = tick_guard
        self._command_guard = command_guard
        self._reconciled = False
        self._stop_reason: str | None = None
        self._reconciler = reconciler
        self._notifications = notifications
        self._notification_feed = notification_feed
        self._pull_request_closing = pull_request_closing
        self._settings = settings
        self._clock = clock
        self._close_state = close_state
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
            self._notification_feed.restore()
            self._notifications.start()
            self._pull_request_closing.start()
            self._events.append(EventKind.ENGINE_STARTED, message="engine started")
            next_tick = self._clock.now()
            while not self._task_supervisor.stop_requested:
                await self._task_supervisor.supervise(self._logger)
                self._notifications.supervise(self._logger)
                self._pull_request_closing.supervise(self._logger)
                if not self._reconciled:
                    await self._tick_guard.attempt(self._prepare)
                commands_changed = bool(await self._command_guard.attempt(self._process_commands))
                if self._task_supervisor.stop_requested:
                    break
                now = self._clock.now()
                events_changed = self._events.since(self._last_scheduled_event)
                if events_changed:
                    self._last_scheduled_event = max(event.id for event in events_changed)
                if commands_changed or events_changed or now >= next_tick:
                    await self._tick_guard.attempt(self._tick)
                    next_tick = now + timedelta(seconds=self._settings.orchestration.poll_seconds)
                self._notification_feed.scan()
                await self._clock.sleep(COMMAND_POLL_SECONDS)
            await self._task_supervisor.supervise(self._logger)
        except Exception as error:
            self._stop_reason = f"{type(error).__name__}: {error}"
            self._logger.exception("engine stopping on an unrecoverable fault")
            raise
        finally:
            await self._shutdown()

    def request_stop(self) -> None:
        """Ask the command loop to stop after acknowledging the current command."""
        self._task_supervisor.request_stop()

    async def _prepare(self) -> None:
        await self._session_guard.ensure()
        if not self._reconciled:
            await self._reconciler.reconcile()
            self._reconciled = True

    async def _tick(self) -> None:
        await self._prepare()
        await self._layout.restore(self._tasks.list_active())
        schedule = await self._scheduler.run_once()
        for dispatch in schedule.dispatches:
            await self._task_supervisor.start_task(dispatch.task_id)
        await self._task_supervisor.ensure_flows()
        await self._reconciler.restore_escalations()
        scheduled_events = self._events.since(self._last_scheduled_event)
        if scheduled_events:
            self._last_scheduled_event = max(event.id for event in scheduled_events)

    async def _process_commands(self) -> bool:
        changed = False
        for queued in self._commands.pending():
            if not self._reconciled and not isinstance(queued.command, Stop):
                continue
            try:
                result = await self._handlers.handle(queued.command)
            except ShikiruError as error:
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                continue
            except OperationalDatabaseError:
                raise
            except Exception as error:
                self._commands.mark_rejected(queued.id, {"error": str(error)})
                raise
            self._commands.mark_done(queued.id, result)
            changed = True
        return changed

    async def _shutdown(self) -> None:
        try:
            reason = self._stop_reason
            stopped = self._events.append(
                EventKind.ENGINE_STOPPED,
                message="engine stopped" if reason is None else f"engine stopped: {reason}",
                data={} if reason is None else {STOP_REASON_KEY: reason},
            )
            await self._notifications.shutdown()
            self._notification_feed.persist()
            await self._pull_request_closing.shutdown()
            if reason is not None:
                await self._notifications.deliver_final(
                    stopped, self._settings.orchestration.faults.stop_notify_seconds
                )
            await self._scheduler.shutdown()
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
