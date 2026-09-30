"""Ends an E2E attempt that makes no progress or outlives its budget (07 §5)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.clock import Clock
    from tests.e2e.diagnostics import Snapshot

STALL_AFTER = timedelta(minutes=4)
WATCH_POLL_SECONDS = 5.0


class StallError(AssertionError):
    """No event was recorded and no turn process lived for the stall window."""


class StallDetector:
    """Decides that a run stalled: no new event and no live turn process for `stall_after`."""

    def __init__(self, clock: Clock, stall_after: timedelta) -> None:
        """Count the stall window from now on `clock`."""
        self._clock = clock
        self._stall_after = stall_after
        self._progress_at = clock.now()
        self._last_event_id = 0

    def stalled(self, last_event_id: int, *, turn_alive: bool) -> bool:
        """Record one observation and report whether the run has made no progress for too long."""
        now = self._clock.now()
        if turn_alive or last_event_id != self._last_event_id:
            self._last_event_id = last_event_id
            self._progress_at = now
        return now - self._progress_at >= self._stall_after


async def watch(
    probe: Callable[[], Awaitable[Snapshot]],
    clock: Clock,
    *,
    stall_after: timedelta = STALL_AFTER,
    poll_seconds: float = WATCH_POLL_SECONDS,
) -> None:
    """Poll `probe` until the run stalls, then raise `StallError` with its diagnostics."""
    detector = StallDetector(clock, stall_after)
    while True:
        await clock.sleep(poll_seconds)
        snapshot = await probe()
        if detector.stalled(snapshot.last_event_id, turn_alive=snapshot.turn_alive):
            message = f"no event and no turn process for {stall_after}\n{snapshot.render()}"
            raise StallError(message)


async def guarded(
    work: Awaitable[None],
    *,
    probe: Callable[[], Awaitable[Snapshot]],
    clock: Clock,
    timeout: float,
    stall_after: timedelta = STALL_AFTER,
) -> None:
    """Run `work`, failing it on a stall or after `timeout` seconds, each with diagnostics."""
    tasks = (
        asyncio.ensure_future(work),
        asyncio.ensure_future(watch(probe, clock, stall_after=stall_after)),
        asyncio.ensure_future(clock.sleep(timeout)),
    )
    work_task, watch_task, _timer = tasks
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if watch_task.done():
            watch_task.result()
        if work_task.done():
            work_task.result()
            return
        message = f"attempt exceeded its {timeout:.0f}s budget\n{(await probe()).render()}"
        raise TimeoutError(message)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
