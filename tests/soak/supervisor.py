"""Keeps the engine running between faults, and says when it exits without having been told to."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from tests.soak.model import Violation

if TYPE_CHECKING:
    import asyncio

    from tests.soak.arena import Arena
    from tests.soak.reporter import Reporter

CLOCK = SystemClock()
WATCH_SECONDS = 0.5
RECOVERY_GRACE_SECONDS = 9.0
LOG_TAIL_LINES = 25


class EngineSupervisor:
    def __init__(self, arena: Arena, reporter: Reporter) -> None:
        self._arena = arena
        self._reporter = reporter
        self._expected = False
        self._disturbed_until = CLOCK.now()
        self.restarts = 0

    def expect_exit(self) -> None:
        self._expected = True

    def disturb(self, seconds: float) -> None:
        until = CLOCK.now() + timedelta(seconds=seconds + RECOVERY_GRACE_SECONDS)
        self._disturbed_until = max(self._disturbed_until, until)

    def stable(self, now: datetime) -> bool:
        return now >= self._disturbed_until and self._arena.engine_pid() is not None

    async def watch(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self._arena.engine_pid() is None:
                await self._restart()
            await CLOCK.sleep(WATCH_SECONDS)

    async def _restart(self) -> None:
        if not self._expected:
            self._reporter.violation(
                self._arena.name,
                Violation(
                    0,
                    None,
                    "the engine exited without being told to; log tail:\n"
                    + self._arena.engine_log_tail(LOG_TAIL_LINES),
                ),
            )
        self._expected = False
        self.disturb(0.0)
        await self._arena.start_engine()
        self.restarts += 1
        self._reporter.say(f"{self._arena.name}: engine restarted")
