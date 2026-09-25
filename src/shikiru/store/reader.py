"""Read-only facade over the state DB for operator queries and the dashboard (06 §1, §5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shikiru.store.escalations import fetch_escalation, fetch_open_escalations
from shikiru.store.events import fetch_events_since
from shikiru.store.quotas import fetch_active_agent_blocks, fetch_quota_agents, fetch_quotas
from shikiru.store.runs import fetch_all_runs, fetch_runs_for_task
from shikiru.store.tasks import fetch_task, fetch_tasks

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.clock import Clock
    from shikiru.domain.escalation import Escalation
    from shikiru.domain.events import Event, EventKind
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Status, Task
    from shikiru.store.database import Database
    from shikiru.store.quotas import AgentBlock

DASHBOARD_CHANGE_POLL_SECONDS = 0.5

OnChange = Callable[[], Awaitable[None]]


class StateReader:
    """Read-only views over the state DB, shared by `OperatorQueries` and the dashboard."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Open `database`'s read-only connection, polled through `clock` by `watch`."""
        self._connection = database.connect_readonly()
        self._clock = clock

    def get_task(self, task_id: str) -> Task | None:
        """See `shikiru.store.tasks.fetch_task`."""
        return fetch_task(self._connection, task_id)

    def list_tasks(self, *, statuses: Sequence[Status] | None = None) -> list[Task]:
        """See `shikiru.store.tasks.fetch_tasks`."""
        return fetch_tasks(self._connection, statuses=statuses)

    def list_runs(self, task_id: str) -> list[Run]:
        """See `shikiru.store.runs.fetch_runs_for_task`."""
        return fetch_runs_for_task(self._connection, task_id)

    def list_all_runs(self) -> list[Run]:
        """See `shikiru.store.runs.fetch_all_runs`."""
        return fetch_all_runs(self._connection)

    def get_escalation(self, escalation_id: str) -> Escalation | None:
        """See `shikiru.store.escalations.fetch_escalation`."""
        return fetch_escalation(self._connection, escalation_id)

    def list_open_escalations(self) -> list[Escalation]:
        """See `shikiru.store.escalations.fetch_open_escalations`."""
        return fetch_open_escalations(self._connection)

    def quotas_for(self, agent: str) -> list[QuotaWindow]:
        """See `shikiru.store.quotas.fetch_quotas`."""
        return fetch_quotas(self._connection, agent)

    def list_quotas(self, agents: Sequence[str]) -> dict[str, list[QuotaWindow]]:
        """Read quota windows for each requested agent."""
        return {agent: self.quotas_for(agent) for agent in agents}

    def list_quota_agents(self) -> list[str]:
        """Read every agent with persisted quota windows."""
        return fetch_quota_agents(self._connection)

    def active_agent_blocks(self, now: datetime) -> list[AgentBlock]:
        """See `shikiru.store.quotas.fetch_active_agent_blocks`."""
        return fetch_active_agent_blocks(self._connection, now)

    def events_since(
        self,
        since_id: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task_id: str | None = None,
    ) -> list[Event]:
        """See `shikiru.store.events.fetch_events_since`."""
        return fetch_events_since(self._connection, since_id, kinds=kinds, task_id=task_id)

    def data_version(self) -> int:
        """Return SQLite's `data_version` counter, bumped by every other connection's commit."""
        row = self._connection.execute("PRAGMA data_version").fetchone()
        return row[0]

    def close(self) -> None:
        """Release the read-only connection; the dashboard or operator calls this on shutdown."""
        self._connection.close()

    async def watch(self, on_change: OnChange) -> None:
        """Call `on_change` once after every commit from another connection, until cancelled."""
        last_version = self.data_version()
        while True:
            await self._clock.sleep(DASHBOARD_CHANGE_POLL_SECONDS)
            current_version = self.data_version()
            if current_version == last_version:
                continue
            last_version = current_version
            await on_change()
