"""Owned threads for blocking quota reads."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from shikiru.agents.contracts import QuotaReading

THREAD_NAME_PREFIX = "quota-probe-"


def _deliver(
    future: asyncio.Future[QuotaReading | None],
    reading: QuotaReading | None,
    error: Exception | None,
) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(reading)


class QuotaProbeThreads:
    """Runs blocking quota reads on daemon threads, at most one unfinished read per agent.

    A blocked read cannot be interrupted, so it is never abandoned and replaced: while an
    agent's read has not returned, a new read of that agent is refused. The threads are
    daemons, so a read that never returns delays neither shutdown nor process exit.
    """

    def __init__(self) -> None:
        """Start with no read running."""
        self._returned: dict[str, threading.Event] = {}

    def busy(self, agent: str) -> bool:
        """Return whether `agent`'s previous read is still running."""
        returned = self._returned.get(agent)
        return returned is not None and not returned.is_set()

    def start(
        self,
        agent: str,
        read: Callable[[datetime], QuotaReading | None],
        since: datetime,
    ) -> asyncio.Future[QuotaReading | None]:
        """Run `read(since)` on a new thread and return a future for its outcome.

        Raises `ShikiruError` while `agent`'s previous read is still running.
        """
        if self.busy(agent):
            message = f"the previous quota probe of {agent} is still waiting for its source"
            raise ShikiruError(message)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[QuotaReading | None] = loop.create_future()
        returned = threading.Event()

        def run() -> None:
            reading: QuotaReading | None = None
            failure: Exception | None = None
            try:
                reading = read(since)
            except Exception as error:  # noqa: BLE001
                failure = error
            finally:
                returned.set()
            if not loop.is_closed():
                loop.call_soon_threadsafe(_deliver, future, reading, failure)

        thread = threading.Thread(target=run, name=f"{THREAD_NAME_PREFIX}{agent}", daemon=True)
        thread.start()
        self._returned[agent] = returned
        return future
