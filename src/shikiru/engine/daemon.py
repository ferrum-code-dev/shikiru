"""The asyncio engine daemon: liveness, command dispatch and task supervision."""

from __future__ import annotations

import fcntl
import math
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import psutil

from shikiru.domain.commands import COMMAND_POLL_SECONDS, Stop
from shikiru.domain.engine_uptime import (
    ENGINE_PID_FIELD,
    ENGINE_PROCESS_STARTED_AT_FIELD,
)
from shikiru.domain.events import STOP_REASON_KEY, EventKind
from shikiru.errors import ShikiruError
from shikiru.private import create_private_file, make_private_dir
from shikiru.process import ProcessIdentity, identify_pid
from shikiru.store.database import OperationalDatabaseError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import FileHandler, Logger
    from pathlib import Path
    from typing import TextIO

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.events import Event
    from shikiru.engine.handlers import HandlerRegistry
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.engine.notification_delivery import NotificationDelivery
    from shikiru.engine.notification_feed import NotificationFeed
    from shikiru.engine.pull_request_closing import PullRequestClosing
    from shikiru.engine.reconcile import Reconciler
    from shikiru.engine.retention import Retention
    from shikiru.engine.scheduler import Scheduler
    from shikiru.engine.session_guard import SessionGuard
    from shikiru.engine.supervisor import TaskSupervisor
    from shikiru.engine.tick_guard import TickGuard
    from shikiru.store.commands import CommandQueue
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore

ENGINE_LOGGER_NAME = "shikiru"
_ENGINE_LOCK_METADATA_PARTS = 2
ENGINE_LOCK_PROBE_GRACE_SECONDS = 1.0
ENGINE_LOCK_RETRY_SECONDS = 0.02


class EngineAlreadyRunningError(ShikiruError):
    """The project already has an engine holding its exclusive lock."""


class EngineLock:
    """An exclusive process lock held for exactly one engine lifetime."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None
        self._identity: ProcessIdentity | None = None

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
        identity = ProcessIdentity(os.getpid(), psutil.Process(os.getpid()).create_time())
        self._identity = identity
        self._file.write(f"{identity.pid} {identity.started_at!r}")
        self._file.flush()

    @property
    def identity(self) -> ProcessIdentity:
        """Return this lock holder's recorded process identity."""
        identity = self._identity
        if identity is None:
            message = "engine lock identity is unavailable before acquisition"
            raise RuntimeError(message)
        return identity

    @staticmethod
    def identity_if_running(path: Path) -> ProcessIdentity | None:
        """Return the verified identity written by the process currently holding `path`."""
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
                    process_started_at = float(contents[1])
                except ValueError:
                    return None
                if pid < 1 or not math.isfinite(process_started_at):
                    return None
                identity = ProcessIdentity(pid, process_started_at)
                return identity if identity.matches(identify_pid(identity.pid)) else None
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return None

    @staticmethod
    def pid_if_running(path: Path) -> int | None:
        """Return the verified PID written by the process that currently holds `path`."""
        identity = EngineLock.identity_if_running(path)
        return identity.pid if identity is not None else None

    def release(self) -> None:
        """Release the lock and close its file descriptor."""
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None
        self._identity = None

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
        loop_guard: TickGuard,
        reconciler: Reconciler,
        notifications: NotificationDelivery,
        notification_feed: NotificationFeed,
        retention: Retention,
        retention_guard: TickGuard,
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
        self._loop_guard = loop_guard
        self._reconciled = False
        self._stop_reason: str | None = None
        self._stopped_event: Event | None = None
        self._reconciler = reconciler
        self._notifications = notifications
        self._notification_feed = notification_feed
        self._retention = retention
        self._retention_guard = retention_guard
        self._pull_request_closing = pull_request_closing
        self._settings = settings
        self._clock = clock
        self._close_state = close_state
        self._last_scheduled_event = 0
        self._logger = logger
        self._log_handler = log_handler

    async def _acquire_lock(self) -> None:
        deadline = self._clock.now() + timedelta(seconds=ENGINE_LOCK_PROBE_GRACE_SECONDS)
        while True:
            try:
                self._lock.acquire()
            except EngineAlreadyRunningError:
                if self._clock.now() >= deadline:
                    raise
                await self._clock.sleep(ENGINE_LOCK_RETRY_SECONDS)
            else:
                return

    async def run(self) -> None:
        """Hold liveness, process commands and schedule tasks until `Stop` or cancellation."""
        try:
            await self._acquire_lock()
        except BaseException:
            self._close_logging()
            self._close_state()
            raise
        try:
            await self._until_done(self._restore_notifications)
            self._notifications.start()
            self._pull_request_closing.start()
            await self._until_done(self._record_start)
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
                events_changed = await self._loop_guard.attempt(self._new_events) or []
                if events_changed:
                    self._last_scheduled_event = max(event.id for event in events_changed)
                if commands_changed or events_changed or now >= next_tick:
                    await self._tick_guard.attempt(self._tick)
                    next_tick = now + timedelta(seconds=self._settings.orchestration.poll_seconds)
                if self._reconciled:
                    await self._retention_guard.attempt(self._retention.run_if_due)
                await self._loop_guard.attempt(self._scan_notifications)
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

    async def _until_done(self, step: Callable[[], Awaitable[bool]]) -> None:
        while await self._loop_guard.attempt(step) is None:
            if self._task_supervisor.stop_requested:
                return
            await self._clock.sleep(COMMAND_POLL_SECONDS)

    async def _restore_notifications(self) -> bool:
        self._notification_feed.restore()
        return True

    async def _record_start(self) -> bool:
        self._events.append(
            EventKind.ENGINE_STARTED,
            message="engine started",
            data=_engine_identity_data(self._lock.identity),
        )
        return True

    async def _new_events(self) -> list[Event]:
        return self._events.since(self._last_scheduled_event)

    async def _scan_notifications(self) -> None:
        self._notification_feed.scan()

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
        self._stopped_event = None
        failures: list[Exception] = []
        try:
            for step in (
                self._record_stop,
                self._notifications.shutdown,
                self._persist_notification_cursor,
                self._pull_request_closing.shutdown,
                self._deliver_stop,
                self._scheduler.shutdown,
                self._task_supervisor.shutdown,
            ):
                try:
                    await step()
                except Exception as error:
                    self._logger.exception("engine shutdown step failed")
                    failures.append(error)
        finally:
            try:
                self._lock.release()
            finally:
                self._close_logging()
                self._close_state()
        if failures:
            raise failures[0]

    async def _record_stop(self) -> None:
        reason = self._stop_reason
        self._stopped_event = self._events.append(
            EventKind.ENGINE_STOPPED,
            message="engine stopped" if reason is None else f"engine stopped: {reason}",
            data={
                **_engine_identity_data(self._lock.identity),
                **({} if reason is None else {STOP_REASON_KEY: reason}),
            },
        )

    async def _persist_notification_cursor(self) -> None:
        self._notification_feed.persist()

    async def _deliver_stop(self) -> None:
        if self._stop_reason is not None and self._stopped_event is not None:
            await self._notifications.deliver_final(
                self._stopped_event, self._settings.orchestration.faults.stop_notify_seconds
            )

    def _close_logging(self) -> None:
        handler = self._log_handler
        self._logger.removeHandler(handler)
        handler.close()


def _engine_identity_data(identity: ProcessIdentity) -> dict[str, int | float]:
    if identity.started_at is None:
        message = "engine process start time is unavailable"
        raise RuntimeError(message)
    return {
        ENGINE_PID_FIELD: identity.pid,
        ENGINE_PROCESS_STARTED_AT_FIELD: identity.started_at,
    }
