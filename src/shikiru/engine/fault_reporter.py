"""Records engine faults and their recovery as `engine_fault` and `engine_recovered` events."""

from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from shikiru.domain.events import EventKind
from shikiru.domain.faults import (
    COMMAND_KEY,
    DETAIL_KEY,
    ERROR_KEY,
    EXIT_CODE_KEY,
    RECOVERABLE_KEY,
    REPEAT_KEY,
    SCOPE_KEY,
    STDERR_KEY,
    TRACEBACK_KEY,
)
from shikiru.errors import CommandFailed, CommandTimeoutError, InvariantError, ShikiruError
from shikiru.store.database import OperationalDatabaseError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime
    from logging import Logger

    from shikiru.clock import Clock
    from shikiru.config.settings import FaultsConfig
    from shikiru.store.events import EventLog

RECOVERABLE_FAULTS = (ShikiruError, OSError, OperationalDatabaseError)
"""Environmental faults (a failed tmux, git, file or database call), retried while they last."""


def is_recoverable(error: Exception) -> bool:
    """Return whether `error` is environmental; an `InvariantError` is a logic bug, never one."""
    return isinstance(error, RECOVERABLE_FAULTS) and not isinstance(error, InvariantError)


def _evidence(error: Exception) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        DETAIL_KEY: str(error),
        TRACEBACK_KEY: "".join(traceback.format_exception(error)),
    }
    if isinstance(error, CommandFailed | CommandTimeoutError):
        evidence[COMMAND_KEY] = list(error.argv)
    if isinstance(error, CommandFailed):
        evidence[EXIT_CODE_KEY] = error.returncode
        evidence[STDERR_KEY] = error.stderr
    return evidence


@dataclass
class _Streak:
    recorded_at: datetime | None = None
    occurrences: int = 0


class FaultReporter:
    """Logs every fault and records one `engine_fault` event per scope, task and error class.

    A repeat of the same fault is counted and recorded again only after
    `repeat_notify_seconds`, so a persistent or flapping fault stays visible without flooding
    the log or the Operator, and a new error class is recorded at once. Each record carries the
    full error, its traceback and, for a failed command, the command, exit code and stderr.
    """

    def __init__(
        self, *, events: EventLog, clock: Clock, settings: FaultsConfig, logger: Logger
    ) -> None:
        """Record in `events`, rate-limited with `clock`."""
        self._events = events
        self._clock = clock
        self._settings = settings
        self._logger = logger
        self._streaks: dict[tuple[str, str | None, str], _Streak] = {}
        self._open: set[tuple[str, str | None]] = set()

    def report(self, error: Exception, *, scope: str, task_id: str | None = None) -> bool:
        """Record `error` raised in `scope` unless just recorded, and classify it.

        Returns whether it is a recoverable fault.
        """
        recoverable = isinstance(error, RECOVERABLE_FAULTS) and not isinstance(
            error, InvariantError
        )
        kind = type(error).__name__
        now = self._clock.now()
        key = (scope, task_id, kind)
        streak = self._streaks.setdefault(key, _Streak())
        streak.occurrences += 1
        interval = timedelta(seconds=self._settings.repeat_notify_seconds)
        if streak.recorded_at is None or now - streak.recorded_at >= interval:
            try:
                self._events.append(
                    EventKind.ENGINE_FAULT,
                    task_id=task_id,
                    message=f"{scope} failed: {kind}: {error}",
                    data={
                        SCOPE_KEY: scope,
                        ERROR_KEY: kind,
                        RECOVERABLE_KEY: recoverable,
                        REPEAT_KEY: streak.occurrences,
                        **_evidence(error),
                    },
                )
            except OperationalDatabaseError:
                self._logger.exception("could not record the fault of %s", scope)
                return recoverable
            streak.recorded_at = now
            streak.occurrences = 0
            self._open.add((scope, task_id))
        return recoverable

    def resolve(self, scope: str, *, task_id: str | None = None) -> None:
        """Record an `engine_recovered` event when `scope` failed, was recorded, and now works."""
        if (scope, task_id) not in self._open:
            return
        try:
            self._events.append(
                EventKind.ENGINE_RECOVERED,
                task_id=task_id,
                message=f"{scope} recovered",
                data={SCOPE_KEY: scope},
            )
        except OperationalDatabaseError:
            self._logger.exception("could not record the recovery of %s", scope)
            return
        self._open.remove((scope, task_id))

    @asynccontextmanager
    async def isolate(self, scope: str, *, task_id: str | None = None) -> AsyncGenerator[None]:
        """Absorb any fault of the block: log it with its traceback and record it for `task_id`."""
        try:
            yield
        except Exception as error:
            self._logger.exception("%s failed", scope)
            self.report(error, scope=scope, task_id=task_id)
        else:
            self.resolve(scope, task_id=task_id)
