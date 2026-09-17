"""Injectable time: every timer in Shikiru goes through a `Clock`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """A source of the current time and of delay, injected wherever either is needed."""

    def now(self) -> datetime:
        """Return the current UTC-aware time."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller for `seconds`."""
        ...


class SystemClock:
    """A `Clock` backed by the real wall clock and event loop."""

    def now(self) -> datetime:
        """Return the current UTC-aware time."""
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller for `seconds`."""
        await asyncio.sleep(seconds)
