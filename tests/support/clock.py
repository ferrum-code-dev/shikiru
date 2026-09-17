"""A fake `Clock` for deterministic tests."""

from __future__ import annotations

from datetime import datetime, timedelta


class FakeClock:
    """A `Clock` whose time only moves when `advance()` is called.

    `sleep()` never suspends the caller: it advances the clock by `seconds`
    and returns immediately, so timeouts, backoffs and quota waits run
    instantly in tests instead of really waiting.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
