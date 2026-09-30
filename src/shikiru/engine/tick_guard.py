"""The single fault barrier around one engine tick (02 §1, 06 §5.7)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from logging import Logger

    from shikiru.clock import Clock
    from shikiru.config.settings import FaultsConfig
    from shikiru.store.events import EventLog

RECOVERABLE_FAULTS = (ShikiruError, OSError)
"""Environmental faults (a failed tmux, git or filesystem call), retried while they last."""

_BACKOFF_FACTOR = 2


class EngineFaultLimitError(ShikiruError):
    """Unexpected (programming) faults repeated on every tick up to the configured limit."""


class TickGuard:
    """Runs a tick, retrying it with bounded backoff after a fault instead of ending the engine.

    `RECOVERABLE_FAULTS` are retried without limit. Any other exception is a programming error:
    it is logged with its traceback and recorded as an `engine_fault` event like the rest, and
    `unexpected_fault_limit` of them in a row end the engine with `EngineFaultLimitError`.
    """

    def __init__(
        self, *, events: EventLog, clock: Clock, settings: FaultsConfig, logger: Logger
    ) -> None:
        """Record faults in `events` and pace retries with `clock`."""
        self._events = events
        self._clock = clock
        self._settings = settings
        self._logger = logger
        self._streak = 0
        self._unexpected = 0
        self._retry_at: datetime | None = None

    async def attempt(self, tick: Callable[[], Awaitable[None]]) -> None:
        """Run `tick` unless a backoff is pending, absorbing its fault."""
        if self._retry_at is not None and self._clock.now() < self._retry_at:
            return
        try:
            await tick()
        except Exception as error:
            recoverable = isinstance(error, RECOVERABLE_FAULTS)
            self._logger.exception(
                "engine tick failed (%s)", "recoverable" if recoverable else "unexpected"
            )
            self._record(error, recoverable=recoverable)
        else:
            self._recover()

    def _record(self, error: Exception, *, recoverable: bool) -> None:
        self._streak += 1
        self._unexpected = 0 if recoverable else self._unexpected + 1
        backoff = min(
            self._settings.backoff_initial_seconds * _BACKOFF_FACTOR ** (self._streak - 1),
            self._settings.backoff_max_seconds,
        )
        self._retry_at = self._clock.now() + timedelta(seconds=backoff)
        kind = type(error).__name__
        self._logger.warning("engine tick attempt %d failed; retry in %.0fs", self._streak, backoff)
        if self._streak == 1 or self._unexpected == 1:
            self._events.append(
                EventKind.ENGINE_FAULT,
                message=f"engine tick failed: {kind}: {error}",
                data={"error": kind, "recoverable": recoverable},
            )
        if self._unexpected >= self._settings.unexpected_fault_limit:
            message = f"{self._unexpected} consecutive unexpected faults, last {kind}: {error}"
            raise EngineFaultLimitError(message) from error

    def _recover(self) -> None:
        if self._streak:
            self._logger.info("engine tick recovered after %d failed attempts", self._streak)
        self._streak = 0
        self._unexpected = 0
        self._retry_at = None
