"""Feeds new events to notification delivery and keeps the delivered-up-to cursor durable."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.engine.notification_delivery import NotificationDelivery
    from shikiru.store.events import EventLog
    from shikiru.store.notification_cursor import NotificationCursor


class NotificationFeed:
    """Queues events for delivery and persists the id every urgent event up to is delivered.

    The cursor trails the oldest undelivered urgent event, so a crash or kill replays exactly the
    urgent events (escalations, engine faults, fault stops) that were not yet delivered.
    Informational events are never replayed. The cursor is written on the next engine poll after
    a delivery, so an event delivered in that short window before a kill is delivered again.
    """

    def __init__(
        self, events: EventLog, cursor: NotificationCursor, delivery: NotificationDelivery
    ) -> None:
        """Read events from `events`, store the cursor in `cursor`, queue into `delivery`."""
        self._events = events
        self._cursor = cursor
        self._delivery = delivery
        self._scanned = 0
        self._persisted: int | None = None

    def restore(self) -> None:
        """Load the cursor and re-queue the urgent events it has not covered.

        The first start of a project has no cursor and begins at the latest event.
        """
        self._persisted = self._cursor.read()
        self._scanned = self._events.latest_id() if self._persisted is None else self._persisted
        for event in self._events.since(self._scanned):
            self._scanned = event.id
            self._delivery.replay(event)
        self.persist()

    def scan(self) -> None:
        """Queue the events appended since the last scan and persist the cursor."""
        for event in self._events.since(self._scanned):
            self._scanned = event.id
            self._delivery.enqueue(event)
        self.persist()

    def persist(self) -> None:
        """Write the cursor when it moved."""
        oldest = self._delivery.oldest_undelivered_urgent_id
        delivered_up_to = self._scanned if oldest is None else oldest - 1
        if delivered_up_to != self._persisted:
            self._cursor.write(delivered_up_to)
            self._persisted = delivered_up_to
