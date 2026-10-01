"""Prunes finished history so a long-running engine stays bounded (05 §2, 03 §7)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Status
from shikiru.paths import run_dir, task_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import RetentionConfig
    from shikiru.store.notification_cursor import NotificationCursor
    from shikiru.store.retention import StateRetention
    from shikiru.store.tasks import TaskStore


class Retention:
    """Removes long-finished tasks' run directories and events and old commands, periodically.

    One pass runs every `interval_seconds`, also removing old engine events. A task's files and
    events go only when the task is `done` or `cancelled` past the horizon and nothing unresolved
    still needs them: no open escalation, no worktree kept for its unintegrated work, no hold, no
    pull request of a cancelled task left open. Run directories go before their events, so an
    interrupted pass is simply repeated.
    """

    def __init__(
        self,
        *,
        store: StateRetention,
        tasks: TaskStore,
        cursor: NotificationCursor,
        project_root: Path,
        has_open_pull_request: Callable[[str], bool],
        clock: Clock,
        settings: RetentionConfig,
    ) -> None:
        """Prune `store` and the run directories under `project_root` on `clock`'s schedule."""
        self._store = store
        self._tasks = tasks
        self._cursor = cursor
        self._project_root = project_root
        self._has_open_pull_request = has_open_pull_request
        self._clock = clock
        self._settings = settings
        self._next_run: datetime | None = None
        self._logger = logging.getLogger(__name__)

    async def run_if_due(self) -> None:
        """Run one pruning pass when `interval_seconds` have passed since the last one."""
        now = self._clock.now()
        if self._next_run is not None and now < self._next_run:
            return
        await self._prune(now)
        self._next_run = now + timedelta(seconds=self._settings.interval_seconds)

    async def _prune(self, now: datetime) -> None:
        delivered_up_to = self._cursor.read()
        if delivered_up_to is None:
            return
        horizon = now - timedelta(days=self._settings.finished_task_days)
        settled = [
            task_id
            for task_id in self._store.settled_task_ids(horizon)
            if self._releasable(task_id)
        ]
        await asyncio.to_thread(_remove_run_dirs, [run_dir(self._project_root, t) for t in settled])
        events = self._store.delete_task_events(settled, delivered_up_to=delivered_up_to)
        events += self._store.delete_engine_events(
            now - timedelta(days=self._settings.event_days), delivered_up_to=delivered_up_to
        )
        commands = self._store.delete_finished_commands(
            now - timedelta(days=self._settings.command_days)
        )
        if settled or events or commands:
            self._logger.info(
                "retention pruned %d tasks, %d events, %d commands", len(settled), events, commands
            )

    def _releasable(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return (
            task is not None
            and not task.held
            and not task_root(self._project_root, task_id).exists()
            and not (task.status is Status.CANCELLED and self._has_open_pull_request(task_id))
        )


def _remove_run_dirs(directories: list[Path]) -> None:
    for directory in directories:
        if directory.exists():
            shutil.rmtree(directory)
