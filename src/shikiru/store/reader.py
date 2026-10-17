"""Read-only facade over the state DB for operator queries and the dashboard (06 §1, §5)."""

from __future__ import annotations

import sqlite3  # noqa: TID251
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shikiru.errors import DatabaseError
from shikiru.store.commands import (
    CommandHistoryPage,
    StoredCommand,
    fetch_command,
    fetch_command_history,
    fetch_pending_command,
)
from shikiru.store.escalations import fetch_escalation, fetch_open_escalations
from shikiru.store.events import (
    MAX_TIMELINE_EVENT_LIMIT,
    fetch_events_since,
    fetch_latest_events,
    fetch_timeline_events,
)
from shikiru.store.quotas import AgentBlock, fetch_agent_block, fetch_quota_agents, fetch_quotas
from shikiru.store.runs import fetch_all_runs, fetch_runs_for_task, fetch_taken_over_roles_for_task
from shikiru.store.tasks import (
    fetch_task,
    fetch_task_browser_page,
    fetch_task_browser_pages,
    fetch_task_relationships,
    fetch_tasks,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.clock import Clock
    from shikiru.domain.commands import Command, CommandStatus
    from shikiru.domain.escalation import Escalation
    from shikiru.domain.events import Event, EventKind
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Role, Status, Task
    from shikiru.store.database import Database
    from shikiru.store.tasks import TaskBrowserPage, TaskRelationships

DASHBOARD_CHANGE_POLL_SECONDS = 0.5
CASEFOLD_SQL_FUNCTION = "casefold"
MAX_COMMAND_HISTORY_LIMIT = 50

OnChange = Callable[[], Awaitable[None]]


class StateReader:
    """Read-only views over the state DB, shared by `OperatorQueries` and the dashboard."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Open `database`'s read-only connection, polled through `clock` by `watch`."""
        self._connection = database.connect_readonly()
        self._connection.create_function(
            CASEFOLD_SQL_FUNCTION,
            1,
            str.casefold,
            deterministic=True,
        )
        self._database_path = database.path
        self._clock = clock

    def get_task(self, task_id: str) -> Task | None:
        """See `shikiru.store.tasks.fetch_task`."""
        return fetch_task(self._connection, task_id)

    def list_tasks(self, *, statuses: Sequence[Status] | None = None) -> list[Task]:
        """See `shikiru.store.tasks.fetch_tasks`."""
        try:
            return fetch_tasks(self._connection, statuses=statuses)
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def task_browser_page(
        self,
        *,
        category: str,
        search: str,
        limit: int,
        offset: int,
        diagnosed_stall_task_ids: Sequence[str] = (),
        quota_wait_task_ids: Sequence[str] = (),
    ) -> TaskBrowserPage:
        """Read one bounded task browser page and its matching and project counts."""
        try:
            return fetch_task_browser_page(
                self._connection,
                category=category,
                search=search,
                limit=limit,
                offset=offset,
                diagnosed_stall_task_ids=diagnosed_stall_task_ids,
                quota_wait_task_ids=quota_wait_task_ids,
            )
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def task_browser_pages(
        self,
        *,
        category: str,
        search: str,
        limit: int,
        page_count: int,
        diagnosed_stall_task_ids: Sequence[str] = (),
        quota_wait_task_ids: Sequence[str] = (),
    ) -> tuple[TaskBrowserPage, ...]:
        """Read a contiguous range of bounded task pages from one database snapshot."""
        try:
            return fetch_task_browser_pages(
                self._connection,
                category=category,
                search=search,
                limit=limit,
                page_count=page_count,
                diagnosed_stall_task_ids=diagnosed_stall_task_ids,
                quota_wait_task_ids=quota_wait_task_ids,
            )
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def task_relationships(self, task_id: str, *, offset: int = 0) -> TaskRelationships | None:
        """Read a task's bounded family group and its distinct stored graph relationships."""
        try:
            return fetch_task_relationships(self._connection, task_id, offset=offset)
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def agent_block(self, agent: str) -> AgentBlock | None:
        """Read the engine's recorded quota block for one required agent."""
        try:
            return fetch_agent_block(self._connection, agent)
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def list_runs(self, task_id: str) -> list[Run]:
        """See `shikiru.store.runs.fetch_runs_for_task`."""
        return fetch_runs_for_task(self._connection, task_id)

    def list_taken_over_roles(self, task_id: str) -> tuple[Role, ...]:
        """Return the bounded set of roles currently taken over for `task_id`."""
        return fetch_taken_over_roles_for_task(self._connection, task_id)

    def list_all_runs(self) -> list[Run]:
        """See `shikiru.store.runs.fetch_all_runs`."""
        return fetch_all_runs(self._connection)

    def command_history(
        self,
        *,
        limit: int,
        offset: int = 0,
        status: CommandStatus | None = None,
    ) -> CommandHistoryPage:
        """Read a bounded command-history page, preserving stored status and result."""
        if not 0 < limit <= MAX_COMMAND_HISTORY_LIMIT:
            message = f"command history limit must be between 1 and {MAX_COMMAND_HISTORY_LIMIT}"
            raise ValueError(message)
        if offset < 0:
            message = "command history offset cannot be negative"
            raise ValueError(message)
        try:
            return fetch_command_history(
                self._connection,
                limit=limit,
                offset=offset,
                status=status,
            )
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def command_status(self, command_id: int) -> StoredCommand | None:
        """Read one durable command status by id without changing its lifecycle."""
        try:
            return fetch_command(self._connection, command_id)
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def pending_command(self, command: Command) -> StoredCommand | None:
        """Find one identical pending command through the read-only connection."""
        try:
            return fetch_pending_command(self._connection, command)
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

    def get_escalation(self, escalation_id: str) -> Escalation | None:
        """See `shikiru.store.escalations.fetch_escalation`."""
        return fetch_escalation(self._connection, escalation_id)

    def list_open_escalations(self) -> list[Escalation]:
        """See `shikiru.store.escalations.fetch_open_escalations`."""
        return fetch_open_escalations(self._connection)

    def list_quotas(self, agents: Sequence[str]) -> dict[str, list[QuotaWindow]]:
        """Read quota windows for each requested agent."""
        return {agent: fetch_quotas(self._connection, agent) for agent in agents}

    def list_quota_agents(self) -> list[str]:
        """Read every agent with persisted quota windows."""
        return fetch_quota_agents(self._connection)

    def events_since(
        self,
        since_id: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        task_id: str | None = None,
    ) -> list[Event]:
        """See `shikiru.store.events.fetch_events_since`."""
        return fetch_events_since(self._connection, since_id, kinds=kinds, task_id=task_id)

    def latest_events(
        self,
        limit: int,
        *,
        kinds: Sequence[EventKind] | None = None,
        implementer_turns_only: bool = False,
        task_id: str | None = None,
    ) -> list[Event]:
        """Read the latest matching events in chronological order."""
        return fetch_latest_events(
            self._connection,
            limit,
            kinds=kinds,
            implementer_turns_only=implementer_turns_only,
            task_id=task_id,
        )

    def task_timeline_events(
        self,
        task_id: str,
        *,
        limit: int,
        before: tuple[str, int] | None = None,
        kinds: Sequence[EventKind] | None = None,
        role: str | None = None,
        turn: str | None = None,
    ) -> list[Event]:
        """Read a bounded, newest-first task event page for the evidence timeline."""
        if not 0 < limit <= MAX_TIMELINE_EVENT_LIMIT:
            message = f"task timeline event limit must be between 1 and {MAX_TIMELINE_EVENT_LIMIT}"
            raise ValueError(message)
        try:
            return fetch_timeline_events(
                self._connection,
                task_id,
                limit=limit,
                before=before,
                kinds=kinds,
                role=role,
                turn=turn,
            )
        except sqlite3.Error as error:
            message = f"could not query read-only database {self._database_path}: {error}"
            raise DatabaseError(message) from error

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
