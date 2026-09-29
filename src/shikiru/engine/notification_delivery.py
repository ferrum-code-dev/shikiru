"""Off-loop, ordered and bounded notification delivery for the engine daemon (06 §5.7)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger

    from shikiru.domain.events import Event
    from shikiru.engine.notifier import Notifier

NOTIFICATION_QUEUE_SIZE = 64


class NotificationDelivery:
    """Delivers events one at a time on its own task so a slow hook never blocks the engine loop."""

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=NOTIFICATION_QUEUE_SIZE)
        self._task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        """Start the delivery task."""
        self._task = asyncio.create_task(self._deliver())

    def enqueue(self, event: Event) -> None:
        """Queue a notifiable `event`, dropping the oldest pending one when the queue is full."""
        if not self._notifier.wants(event):
            return
        if self._queue.full():
            dropped = self._queue.get_nowait()
            self._logger.warning(
                "notification queue full; dropped event %s for event %s", dropped.id, event.id
            )
        self._queue.put_nowait(event)

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

    async def _deliver(self) -> None:
        while True:
            await self._notifier.notify(await self._queue.get())
