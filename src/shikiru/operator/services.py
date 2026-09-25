"""The owned operator service bundle built for one project."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.operator.commands import OperatorCommands
    from shikiru.operator.events import EventFeed
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.reader import StateReader


class OperatorServices:
    """Own the query, command and event services and release their state connections."""

    def __init__(
        self,
        reader: StateReader,
        queries: OperatorQueries,
        commands: OperatorCommands,
        events: EventFeed,
        close: Callable[[], None],
    ) -> None:
        """Store the services and their state-connection cleanup callback."""
        self.reader = reader
        self.queries = queries
        self.commands = commands
        self.events = events
        self._close = close

    def close(self) -> None:
        """Close the bundle's writer and read-only state connections."""
        self._close()
