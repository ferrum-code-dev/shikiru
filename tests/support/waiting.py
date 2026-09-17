"""The only place tests are allowed to poll."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

_POLL_INTERVAL_SECONDS = 0.02

_Predicate = Callable[[], bool | Awaitable[bool]]


class WaitTimeoutError(TimeoutError):
    """A `wait_until` predicate never became true within its timeout."""


async def yield_control() -> None:
    """Let one pass of the event loop run, without waiting for any duration."""
    await asyncio.sleep(0)


async def wait_until(predicate: _Predicate, timeout: float) -> None:
    """Poll `predicate` (sync or async) until it is true, or raise `WaitTimeoutError`."""
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        if time.monotonic() >= deadline:
            message = f"predicate did not become true within {timeout}s"
            raise WaitTimeoutError(message)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
