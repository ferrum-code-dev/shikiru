"""The fault barrier around one engine tick or command pass."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from logging import Logger

    from shikiru.clock import Clock
    from shikiru.config.settings import FaultsConfig
    from shikiru.engine.fault_reporter import FaultReporter

_BACKOFF_FACTOR = 2


class EngineFaultLimitError(ShikiruError):
    """Unexpected (programming) faults repeated on every tick up to the configured limit."""


class TickGuard:
    """Runs a tick, retrying it with bounded backoff after a fault instead of ending the engine.

    Recoverable faults (`fault_reporter.RECOVERABLE_FAULTS`) are retried without limit. Any other
    exception is a programming error: it is recorded like the rest, and `unexpected_fault_limit`
    of them in a row end the engine with `EngineFaultLimitError`.
    """

    def __init__(
        self,
        *,
        reporter: FaultReporter,
        clock: Clock,
        settings: FaultsConfig,
        logger: Logger,
        scope: str,
    ) -> None:
        """Record faults through `reporter` under `scope` and pace retries with `clock`."""
        self._reporter = reporter
        self._clock = clock
        self._settings = settings
        self._logger = logger
        self._scope = scope
        self._streak = 0
        self._unexpected = 0
        self._backoff = 0.0
        self._retry_at: datetime | None = None

    async def attempt[T](self, tick: Callable[[], Awaitable[T]]) -> T | None:
        """Run `tick` unless a backoff is pending, absorbing its fault.

        Returns what `tick` returned, or `None` when it was skipped or failed.
        """
        if self._retry_at is not None and self._clock.now() < self._retry_at:
            return None
        try:
            result = await tick()
        except Exception as error:
            self._logger.exception("%s failed", self._scope)
            self._record(error)
            return None
        self._recover()
        return result

    def _record(self, error: Exception) -> None:
        recoverable = self._reporter.report(error, scope=self._scope)
        self._streak += 1
        self._unexpected = 0 if recoverable else self._unexpected + 1
        grown = (
            self._settings.backoff_initial_seconds
            if self._streak == 1
            else self._backoff * _BACKOFF_FACTOR
        )
        self._backoff = min(grown, self._settings.backoff_max_seconds)
        self._retry_at = self._clock.now() + timedelta(seconds=self._backoff)
        self._logger.warning(
            "%s attempt %d failed; retry in %.0fs", self._scope, self._streak, self._backoff
        )
        if self._unexpected >= self._settings.unexpected_fault_limit:
            kind = type(error).__name__
            message = f"{self._unexpected} consecutive unexpected faults, last {kind}: {error}"
            raise EngineFaultLimitError(message) from error

    def _recover(self) -> None:
        self._reporter.resolve(self._scope)
        if self._streak:
            self._logger.info("%s recovered after %d failed attempts", self._scope, self._streak)
        self._streak = 0
        self._unexpected = 0
        self._retry_at = None
