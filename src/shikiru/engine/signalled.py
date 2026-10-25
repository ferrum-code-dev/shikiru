"""Runs the engine so that SIGTERM and SIGINT stop it gracefully, within a bound."""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.engine.daemon import Engine
    from shikiru.engine.turn_processes import TurnProcesses

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class ForcedShutdownError(ShikiruError):
    """The engine was signalled twice or outlived its shutdown bound and was ended by force."""


class SignalledEngine:
    """Run an `Engine` until it stops, turning the first stop signal into `Engine.request_stop`."""

    def __init__(
        self,
        *,
        engine: Engine,
        turn_processes: TurnProcesses,
        clock: Clock,
        shutdown_seconds: float,
    ) -> None:
        """Bound the graceful shutdown by `shutdown_seconds` on `clock`."""
        self._engine = engine
        self._turn_processes = turn_processes
        self._clock = clock
        self._shutdown_seconds = shutdown_seconds
        self._deadline: asyncio.Task[None] | None = None
        self._forced = False

    async def run(self) -> None:
        """Run the engine; a second signal or the bound cancels it and ends every turn process."""
        loop = asyncio.get_running_loop()
        engine = asyncio.create_task(self._engine.run())
        for signum in STOP_SIGNALS:
            loop.add_signal_handler(signum, self._on_signal, engine)
        try:
            await engine
        except asyncio.CancelledError:
            if not self._forced:
                raise
            await self._turn_processes.end_orphans()
            message = f"engine ended by force after {self._shutdown_seconds:g} s or a second signal"
            raise ForcedShutdownError(message) from None
        finally:
            for signum in STOP_SIGNALS:
                loop.remove_signal_handler(signum)
            if self._deadline is not None:
                self._deadline.cancel()

    def _on_signal(self, engine: asyncio.Task[None]) -> None:
        if self._deadline is not None:
            self._force(engine)
            return
        self._engine.request_stop()
        self._deadline = asyncio.create_task(self._expire(engine))

    async def _expire(self, engine: asyncio.Task[None]) -> None:
        await self._clock.sleep(self._shutdown_seconds)
        self._force(engine)

    def _force(self, engine: asyncio.Task[None]) -> None:
        self._forced = True
        engine.cancel()
