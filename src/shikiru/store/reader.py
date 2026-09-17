"""Read-only facade over the state DB for operator queries and the dashboard (06 §1, §5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shikiru.store.escalations import fetch_escalation, fetch_open_escalations
from shikiru.store.events import fetch_events_since
from shikiru.store.quotas import fetch_active_agent_blocks, fetch_quotas
from shikiru.store.runs import fetch_runs_for_task
from shikiru.store.tasks import fetch_task, fetch_tasks

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.domain.escalation import Escalation
    from shikiru.domain.events import Event, EventKind
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Status, Task
    from shikiru.store.database import Database
    from shikiru.store.quotas import AgentBlock, Quota

DASHBOARD_CHANGE_POLL_SECONDS = 0.5

OnChange = Callable[[], Awaitable[None] | None]


class StateReader:
    """Read-only views over the state DB, shared by `OperatorQueries` and the dashboard."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Open `database`'s read-only connection, polled through `clock` by `watch`."""
        self._connection = database.connect_readonly()
        self._clock = clock

    def get_task(self, task_id: str) -> Task | None:
        """Read one task by id, or `None` if it doesn't exist."""
        return fetch_task(self._connection, task_id)

    def list_tasks(self, *, statuses: Sequence[Status] | None = None) -> list[Task]:
        """Read every task, optionally restricted to `statuses`, ordered by `seq`."""
        return fetch_tasks(self._connection, statuses=statuses)

    def list_runs(self, task_id: str) -> list[Run]:
        """Read every run recorded for `task_id`, most recently started first."""
        return fetch_runs_for_task(self._connection, task_id)

    def get_escalation(self, escalation_id: str) -> Escalation | None:
        """Read one escalation by id, or `None` if it doesn't exist."""
        return fetch_escalation(self._connection, escalation_id)

    def list_open_escalations(self) -> list[Escalation]:
        """Read every open escalation, oldest first."""
        return fetch_open_escalations(self._connection)

    def quotas_for(self, agent: str) -> list[Quota]:
        """Read every quota window recorded for `agent`."""
        return fetch_quotas(self._connection, agent)

    def active_agent_blocks(self, now: datetime) -> list[AgentBlock]:
        """Read every agent block still in effect at `now`."""
        return fetch_active_agent_blocks(self._connection, now)

    def events_since(
        self,
        since_id: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task_id: str | None = None,
    ) -> list[Event]:
        """Read every event since `since_id`, optionally filtered by `kinds` and `task_id`."""
        return fetch_events_since(self._connection, since_id, kinds=kinds, task_id=task_id)

    def data_version(self) -> int:
        """Return SQLite's `data_version` counter, bumped by every other connection's commit."""
        row = self._connection.execute("PRAGMA data_version").fetchone()
        return row[0]

    async def watch(self, on_change: OnChange) -> None:
        """Call `on_change` once after every commit from another connection, until cancelled."""
        last_version = self.data_version()
        while True:
            await self._clock.sleep(DASHBOARD_CHANGE_POLL_SECONDS)
            current_version = self.data_version()
            if current_version == last_version:
                continue
            last_version = current_version
            result = on_change()
            if result is not None:
                await result
