"""Off-loop, prioritised and bounded notification delivery for the engine daemon (06 §5.7)."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger

    from shikiru.domain.events import Event
    from shikiru.engine.notifier import Notifier

INFORMATIONAL_QUEUE_SIZE = 64


class NotificationDelivery:
    """Delivers events one at a time on its own task so a slow hook never blocks the engine loop.

    Urgent events (escalations, engine faults, fault stops) are delivered before informational
    ones and are never dropped; only the informational backlog is bounded.
    """

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier
        self._urgent: deque[Event] = deque()
        self._informational: deque[Event] = deque(maxlen=INFORMATIONAL_QUEUE_SIZE)
        self._pending = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        """Start the delivery task."""
        self._task = asyncio.create_task(self._deliver())

    def enqueue(self, event: Event) -> None:
        """Queue a notifiable `event`; a full informational backlog drops its oldest entry."""
        if not self._notifier.wants(event):
            return
        if self._notifier.is_urgent(event):
            self._urgent.append(event)
        else:
            if len(self._informational) == INFORMATIONAL_QUEUE_SIZE:
                self._logger.warning(
                    "notification queue full; dropped event %s for event %s",
                    self._informational[0].id,
                    event.id,
                )
            self._informational.append(event)
        self._pending.set()

    def supervise(self, logger: Logger) -> None:
        """Log a delivery task failure and start a replacement task."""
        task = self._task
        if task is None or not task.done():
            return
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "notification delivery failed",
                exc_info=(type(error), error, error.__traceback__),
            )
        self.start()

    async def shutdown(self) -> None:
        """Cancel the delivery task, terminating a running hook, without draining the queue."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def deliver_final(self, event: Event, timeout_seconds: float) -> None:
        """Deliver `event` directly once the loop is over, giving up after `timeout_seconds`."""
        try:
            await asyncio.wait_for(self._notifier.notify(event), timeout_seconds)
        except TimeoutError:
            self._logger.warning("final notification for event %s timed out", event.id)

    async def _deliver(self) -> None:
        while True:
            queue = self._urgent or self._informational
            if not queue:
                self._pending.clear()
                await self._pending.wait()
                continue
            await self._notifier.notify(queue.popleft())
