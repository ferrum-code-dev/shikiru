"""Records engine faults as rate-limited `engine_fault` events (02 §1, 06 §5.7)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.errors import InvariantError, ShikiruError
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


@dataclass
class _Streak:
    recorded_at: datetime | None = None
    occurrences: int = 0


class FaultReporter:
    """Logs every fault and records one `engine_fault` event per scope, task and error class.

    A repeat of the same fault is counted and recorded again only after
    `repeat_notify_seconds`, so a persistent or flapping fault stays visible without flooding
    the log or the Operator, and a new error class is recorded at once.
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
            self._events.append(
                EventKind.ENGINE_FAULT,
                task_id=task_id,
                message=f"{scope} failed: {kind}: {error}",
                data={
                    "scope": scope,
                    "error": kind,
                    "recoverable": recoverable,
                    "repeat": streak.occurrences,
                },
            )
            streak.recorded_at = now
            streak.occurrences = 0
        return recoverable

    @asynccontextmanager
    async def isolate(self, scope: str, *, task_id: str | None = None) -> AsyncGenerator[None]:
        """Absorb any fault of the block: log it with its traceback and record it for `task_id`."""
        try:
            yield
        except Exception as error:
            self._logger.exception("%s failed", scope)
            self.report(error, scope=scope, task_id=task_id)
