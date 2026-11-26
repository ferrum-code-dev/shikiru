"""Request GitHub auto-merge while a task's pull request is final, and withdraw it otherwise."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.faults import next_backoff
from shikiru.domain.reports import task_commit_subject
from shikiru.domain.tasks import Status
from shikiru.engine.fault_reporter import RECOVERABLE_FAULTS
from shikiru.engine.integration import IntegrationBlocked
from shikiru.errors import PROCESS_FAILURES
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import OrchestrationConfig
    from shikiru.domain.events import Event
    from shikiru.domain.tasks import Task
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.engine.integration import IntegrationMetadata
    from shikiru.store.events import EventLog
    from shikiru.store.feedback import FeedbackStore
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.github import GitHub

AUTO_MERGE_FAULT_SCOPE = "pull request auto-merge"
_REQUEST_RECORDS = (EventKind.PR_AUTO_MERGE_REQUESTED, EventKind.PR_AUTO_MERGE_CANCELLED)
_SETTLED_STATUSES = (Status.DONE, Status.CANCELLED)
_URL = "url"
_HEAD = "head"
_SUBJECT = "subject"
_BODY = "body"
_STATE = "state"


class AutoMergeRefused(IntegrationBlocked):
    """GitHub refused to enable auto-merge for a pull request; the task needs the Operator."""

    def __init__(self, task_id: str, url: str, error: Exception) -> None:
        """Build the error for `task_id`'s pull request at `url`, quoting GitHub's `error`."""
        super().__init__(f"{task_id}: GitHub refused auto-merge of {url}: {error}")


class PullRequestAutoMerge:
    """Request and withdraw GitHub auto-merge for a task's pull request.

    A request names the squash commit's subject and description and the head Shikiru published.
    The latest request or withdrawal recorded in `events` is the state: a request is not repeated
    for the same values after a restart or an adoption, and a withdrawal is recorded only once.
    """

    def __init__(
        self,
        *,
        github: GitHub,
        events: EventLog,
        feedback: FeedbackStore,
        project_root: Path,
        metadata_of: Callable[[Task], IntegrationMetadata],
    ) -> None:
        """Act through `github` from `project_root`, recording in `events`.

        `feedback` tells whether a round is unfinished, and `metadata_of` gives the task's accepted
        subject and description.
        """
        self._github = github
        self._events = events
        self._feedback = feedback
        self._project_root = project_root
        self._metadata_of = metadata_of

    def may_merge(self, task: Task) -> bool:
        """Return whether GitHub may merge `task`'s pull request now.

        That needs a task waiting on its pull request, not held, with no feedback or check round
        unfinished.
        """
        return (
            task.status is Status.INTEGRATING
            and not task.held
            and self._feedback.active(task.id) is None
        )

    async def request(self, task: Task, url: str, head: str) -> None:
        """Request squash auto-merge of the pull request at `url` if `task` may merge now.

        Nothing is sent when the latest record already requests these values. A refusal raises
        `AutoMergeRefused`, and the request is recorded only once GitHub accepted it.
        """
        if not self.may_merge(task):
            return
        metadata = self._metadata_of(task)
        values = {
            _URL: url,
            _HEAD: head,
            _SUBJECT: task_commit_subject(metadata.subject, task.id),
            _BODY: metadata.pr_description,
        }
        latest = self._latest(task.id)
        if (
            latest is not None
            and latest.kind is EventKind.PR_AUTO_MERGE_REQUESTED
            and all(latest.data.get(key) == value for key, value in values.items())
        ):
            return
        try:
            await self._github.request_auto_merge(
                self._project_root,
                url,
                head=head,
                subject=values[_SUBJECT],
                body=values[_BODY],
            )
        except PROCESS_FAILURES as error:
            raise AutoMergeRefused(task.id, url, error) from error
        self._events.append(
            EventKind.PR_AUTO_MERGE_REQUESTED,
            task_id=task.id,
            message=f"{task.id} requested auto-merge of {url}",
            data=values,
        )

    async def cancel(self, task_id: str) -> None:
        """Withdraw the pending auto-merge request of `task_id`, if one is recorded.

        A pull request that is no longer open has nothing left to withdraw, so only the record is
        closed. Otherwise a failed withdrawal raises and nothing is recorded.
        """
        latest = self._latest(task_id)
        if latest is None or latest.kind is not EventKind.PR_AUTO_MERGE_REQUESTED:
            return
        url = str(latest.data[_URL])
        state = (await self._github.pull_request_status(self._project_root, url)).state
        if state is PullRequestState.OPEN:
            await self._github.cancel_auto_merge(self._project_root, url)
        self._events.append(
            EventKind.PR_AUTO_MERGE_CANCELLED,
            task_id=task_id,
            message=f"{task_id} withdrew the auto-merge request of {url}",
            data={_URL: url, _STATE: state.value},
        )

    def _latest(self, task_id: str) -> Event | None:
        records = self._events.since(0, kinds=_REQUEST_RECORDS, task_id=task_id)
        return records[-1] if records else None


class AutoMergeReconciler:
    """Withdraws auto-merge requests that are no longer final, on its own task off the engine loop.

    Every poll checks each unfinished task. A task that is held, not waiting on its pull request
    or with an unfinished round has its request withdrawn, including after an engine restart. A
    failed withdrawal is recorded as a fault and retried on a backoff.
    """

    def __init__(
        self,
        *,
        auto_merge: PullRequestAutoMerge,
        tasks: TaskStore,
        faults: FaultReporter,
        clock: Clock,
        settings: OrchestrationConfig,
    ) -> None:
        """Sweep `tasks` through `auto_merge`, reporting failures to `faults`, on `clock`."""
        self._auto_merge = auto_merge
        self._tasks = tasks
        self._faults = faults
        self._clock = clock
        self._settings = settings
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the reconciling task."""
        self._task = asyncio.create_task(self._run())

    def supervise(self, logger: Logger) -> None:
        """Log a reconciling failure and start a replacement task."""
        task = self._task
        if task is None or not task.done():
            return
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "pull request auto-merge reconciling failed",
                exc_info=(type(error), error, error.__traceback__),
            )
        self.start()

    async def shutdown(self) -> None:
        """Cancel the reconciling task; the next engine start sweeps again."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        delay = 0.0
        while True:
            failed = await self.sweep()
            if failed:
                faults = self._settings.faults
                delay = next_backoff(
                    delay,
                    initial=faults.backoff_initial_seconds,
                    maximum=faults.backoff_max_seconds,
                )
                await self._clock.sleep(delay)
            else:
                delay = 0.0
                await self._clock.sleep(self._settings.poll_seconds)

    async def sweep(self) -> bool:
        """Withdraw every request that is no longer final; return whether any withdrawal failed."""
        failed = False
        for task in self._tasks.list_all():
            if task.status in _SETTLED_STATUSES or self._auto_merge.may_merge(task):
                continue
            try:
                await self._auto_merge.cancel(task.id)
            except RECOVERABLE_FAULTS as error:
                failed = True
                self._faults.report(error, scope=AUTO_MERGE_FAULT_SCOPE, task_id=task.id)
            else:
                self._faults.resolve(AUTO_MERGE_FAULT_SCOPE, task_id=task.id)
        return failed
