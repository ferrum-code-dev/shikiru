"""Event reads and bounded long-polling for operators."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.clock import Clock
    from shikiru.domain.events import Event, EventKind
    from shikiru.store.reader import StateReader

EVENT_POLL_SECONDS = 0.5
MAX_WAIT_SECONDS = 300.0


class EventFeed:
    """Read and wait for state events through a read-only `StateReader`."""

    def __init__(self, reader: StateReader, clock: Clock) -> None:
        """Use `reader` for event rows and `clock` for deterministic long-poll delays."""
        self._reader = reader
        self._clock = clock

    def read(
        self,
        since: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task: str | None = None,
    ) -> list[Event]:
        """Return events after `since`, with optional kind and task filters."""
        return self._reader.events_since(since, kinds=kinds, task_id=task)

    async def wait(
        self,
        since: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task: str | None = None,
        timeout: float = MAX_WAIT_SECONDS,
    ) -> list[Event]:
        """Wait until matching events arrive or `timeout` seconds elapse."""
        if timeout < 0 or timeout > MAX_WAIT_SECONDS:
            message = f"timeout must be between 0 and {MAX_WAIT_SECONDS:g} seconds"
            raise ValueError(message)
        deadline = self._clock.now() + timedelta(seconds=timeout)
        while True:
            events = self.read(since, kinds=kinds, task=task)
            if events:
                return events
            if self._clock.now() >= deadline:
                return []
            remaining = (deadline - self._clock.now()).total_seconds()
            await self._clock.sleep(min(EVENT_POLL_SECONDS, remaining))
