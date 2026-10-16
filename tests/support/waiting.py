"""The only place tests are allowed to poll."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from textual.css.query import NoMatches

if TYPE_CHECKING:
    from tests.support.clock import FakeClock

_POLL_INTERVAL_SECONDS = 0.02

UI_WAIT_SECONDS = 30
ACTION_CONFIRMATION_SECONDS = 0.5

_Predicate = Callable[[], bool | Awaitable[bool]]


class WaitTimeoutError(TimeoutError):
    """A `wait_until` predicate never became true within its timeout."""


async def yield_control() -> None:
    """Let one pass of the event loop run, without waiting for any duration."""
    await asyncio.sleep(0)


async def wait_until(predicate: _Predicate, timeout: float) -> None:
    """Poll `predicate` (sync or async) until it is true, or raise `WaitTimeoutError`.

    A Textual `NoMatches` raised by the predicate means its widget is not mounted yet; it counts
    as "not yet" and, if the wait times out, is chained as the cause.
    """
    deadline = time.monotonic() + timeout
    not_mounted: NoMatches | None = None
    while True:
        try:
            result = predicate()
            if isinstance(result, Awaitable):
                result = await result
        except NoMatches as error:
            not_mounted = error
            result = False
        if result:
            return
        if time.monotonic() >= deadline:
            message = f"predicate did not become true within {timeout}s"
            raise WaitTimeoutError(message) from not_mounted
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def wait_until_advancing(
    clock: FakeClock, predicate: _Predicate, timeout: float, *, step_seconds: float = 1.0
) -> None:
    """Like `wait_until`, moving the fake clock a step whenever a task is sleeping on it."""

    async def advanced() -> bool:
        if clock.has_pending_sleep():
            clock.advance(step_seconds)
        result = predicate()
        if isinstance(result, Awaitable):
            result = await result
        return bool(result)

    await wait_until(advanced, timeout)
