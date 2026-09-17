"""A fake `Clock` for deterministic tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta


class FakeClock:
    """A `Clock` whose time only ever moves when `advance()` is called.

    `sleep(seconds)` registers a deadline of `now + seconds` and suspends the
    caller on a future private to that call; it never advances time itself.
    `advance(seconds)` moves `now` forward by `seconds` and resolves every
    pending sleeper whose deadline is now due, in deadline order, so
    concurrent sleepers with different deadlines wake in that order rather
    than all at once. A deadline that is already due when `sleep()` is
    called (including `sleep(0)`) resolves immediately, but the caller still
    yields control once, exactly as a real `await` would.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start
        self._sleepers: list[tuple[datetime, asyncio.Future[None]]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        deadline = self._now + timedelta(seconds=seconds)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        if deadline <= self._now:
            loop.call_soon(future.set_result, None)
        else:
            self._sleepers.append((deadline, future))
        await future

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        due = sorted(
            (sleeper for sleeper in self._sleepers if sleeper[0] <= self._now),
            key=lambda sleeper: sleeper[0],
        )
        for sleeper in due:
            self._sleepers.remove(sleeper)
            _, future = sleeper
            future.set_result(None)
