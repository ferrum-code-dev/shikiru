"""Off-loop, prioritised and bounded notification delivery for the engine daemon."""

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
    """Delivers events on two tasks, so a slow hook never blocks the engine loop or an escalation.

    Urgent events (escalations, engine faults, fault stops) have a delivery task of their own, so
    a stuck informational hook never delays one by more than the urgent hook in flight; they are
    never dropped, and only the informational backlog is bounded. Each lane delivers one event at
    a time. An urgent event leaves its queue only after its delivery, so
    `oldest_undelivered_urgent_id` covers the one in flight.
    """

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier
        self._urgent: deque[Event] = deque()
        self._informational: deque[Event] = deque(maxlen=INFORMATIONAL_QUEUE_SIZE)
        self._urgent_pending = asyncio.Event()
        self._informational_pending = asyncio.Event()
        self._urgent_task: asyncio.Task[None] | None = None
        self._informational_task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        """Start the urgent and the informational delivery tasks."""
        self._urgent_task = asyncio.create_task(self._deliver_urgent())
        self._informational_task = asyncio.create_task(self._deliver_informational())

    def enqueue(self, event: Event) -> None:
        """Queue a notifiable `event`; a full informational backlog drops its oldest entry."""
        if not self._notifier.wants(event):
            return
        if self._notifier.is_urgent(event):
            self._urgent.append(event)
            self._urgent_pending.set()
        else:
            if len(self._informational) == INFORMATIONAL_QUEUE_SIZE:
                self._logger.warning(
                    "notification queue full; dropped event %s for event %s",
                    self._informational[0].id,
                    event.id,
                )
            self._informational.append(event)
            self._informational_pending.set()

    def replay(self, event: Event) -> None:
        """Queue `event` after a restart when it is urgent; informational ones are not replayed."""
        if self._notifier.is_urgent(event):
            self.enqueue(event)

    @property
    def oldest_undelivered_urgent_id(self) -> int | None:
        """Return the id of the oldest urgent event not yet delivered, if any."""
        return self._urgent[0].id if self._urgent else None

    def supervise(self, logger: Logger) -> None:
        """Log a delivery task failure and start a replacement task."""
        if _failed(self._urgent_task, logger):
            self._urgent_task = asyncio.create_task(self._deliver_urgent())
        if _failed(self._informational_task, logger):
            self._informational_task = asyncio.create_task(self._deliver_informational())

    async def shutdown(self) -> None:
        """Cancel the delivery tasks, terminating running hooks, without draining the queues."""
        tasks = (self._urgent_task, self._informational_task)
        self._urgent_task = self._informational_task = None
        for task in tasks:
            if task is not None:
                task.cancel()
        for task in tasks:
            if task is not None:
                with suppress(asyncio.CancelledError):
                    await task

    async def deliver_final(self, event: Event, timeout_seconds: float) -> None:
        """Deliver `event` directly once the loop is over, giving up after `timeout_seconds`."""
        try:
            await asyncio.wait_for(self._notifier.notify(event), timeout_seconds)
        except TimeoutError:
            self._logger.warning("final notification for event %s timed out", event.id)

    async def _deliver_urgent(self) -> None:
        while True:
            if self._urgent:
                await self._notifier.notify(self._urgent[0])
                self._urgent.popleft()
            else:
                self._urgent_pending.clear()
                await self._urgent_pending.wait()

    async def _deliver_informational(self) -> None:
        while True:
            if self._informational:
                await self._notifier.notify(self._informational.popleft())
            else:
                self._informational_pending.clear()
                await self._informational_pending.wait()


def _failed(task: asyncio.Task[None] | None, logger: Logger) -> bool:
    if task is None or not task.done():
        return False
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error(
            "notification delivery failed",
            exc_info=(type(error), error, error.__traceback__),
        )
    return True
