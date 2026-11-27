"""Request GitHub auto-merge while a task's pull request is final, and withdraw it otherwise."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, TypeVar

from shikiru.domain.events import EventKind
from shikiru.domain.faults import next_backoff
from shikiru.domain.reports import task_commit_subject
from shikiru.domain.tasks import Status
from shikiru.engine.fault_reporter import RECOVERABLE_FAULTS
from shikiru.engine.integration import IntegrationBlocked
from shikiru.errors import PROCESS_FAILURES
from shikiru.workspace.github import PullRequestState, parse_pull_request_number

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
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
    from shikiru.workspace.github import AutoMergeRequest, GitHub, PullRequestStatus

AUTO_MERGE_FAULT_SCOPE = "pull request auto-merge"
_REQUEST_FAULT_SCOPE = "pull request auto-merge request"
_WITHDRAW_FAULT_SCOPE = "pull request auto-merge withdrawal"
_STATUS_READ_FAULT_SCOPE = "pull request status for auto-merge"
_AUTO_MERGE_READ_FAULT_SCOPE = "auto-merge request read"
SQUASH_METHOD = "SQUASH"
_REQUEST_RECORDS = (EventKind.PR_AUTO_MERGE_REQUESTED, EventKind.PR_AUTO_MERGE_CANCELLED)
_SETTLED_STATUSES = (Status.DONE, Status.CANCELLED)
_URL = "url"
_HEAD = "head"
_SUBJECT = "subject"
_BODY = "body"
_STATE = "state"
_REFUSED_STATE = "refused"
_RETRY_LOOP_EXHAUSTED = "auto-merge retry loop ended without returning or raising"
_Result = TypeVar("_Result")


class AutoMergeRefused(IntegrationBlocked):
    """GitHub refused to enable auto-merge for a pull request; the task needs the Operator."""

    def __init__(self, task_id: str, url: str, detail: str) -> None:
        """Build the error for `task_id`'s pull request at `url`, quoting GitHub's `detail`."""
        super().__init__(f"{task_id}: GitHub refused auto-merge of {url}: {detail}")


class AutoMergeWithdrawFailed(IntegrationBlocked):
    """A pending auto-merge request could not be withdrawn, so the task may not go on."""

    def __init__(self, task_id: str, url: str, detail: str) -> None:
        """Build the error for `task_id`'s pull request at `url`, saying why it still holds."""
        super().__init__(f"{task_id}: auto-merge of {url} could not be withdrawn: {detail}")


class AutoMergeReadFailed(IntegrationBlocked):
    """GitHub's pull request or auto-merge state could not be read within the retry limit."""

    def __init__(self, task_id: str, url: str, detail: str) -> None:
        """Build the exhausted read error for `task_id`'s pull request at `url`."""
        super().__init__(f"{task_id}: could not verify auto-merge state of {url}: {detail}")


@dataclass(frozen=True, slots=True)
class _Request:
    url: str
    head: str
    subject: str
    body: str

    def as_data(self) -> dict[str, str]:
        return {_URL: self.url, _HEAD: self.head, _SUBJECT: self.subject, _BODY: self.body}

    def is_held_by(self, held: AutoMergeRequest | None) -> bool:
        return (
            held is not None
            and held.method == SQUASH_METHOD
            and held.subject == self.subject
            and held.body == self.body
        )


class PullRequestAutoMerge:
    """Request and withdraw GitHub auto-merge for a task's pull request.

    GitHub's own `autoMergeRequest` is the state a request or a withdrawal is judged by. Every
    request is recorded in `events` before it is sent, so a request GitHub accepts is never lost
    when the engine stops before recording it: the next withdrawal reads GitHub and withdraws it.
    Requests and withdrawals for one task are serialized, and an enable is withdrawn before its
    lock is released if the task stopped being final while `gh` ran. Withdrawals inspect GitHub
    even without a local request record, so adopted requests are covered. Reads, enables and
    withdrawals use the configured bounded retry and fault backoff; a request whose reply is lost
    is confirmed on GitHub instead of being taken as a refusal.
    """

    def __init__(
        self,
        *,
        github: GitHub,
        events: EventLog,
        feedback: FeedbackStore,
        tasks: TaskStore,
        project_root: Path,
        metadata_of: Callable[[Task], IntegrationMetadata],
        clock: Clock,
        faults: FaultReporter,
        settings: OrchestrationConfig,
    ) -> None:
        """Act through `github` from `project_root`, recording in `events`.

        `feedback` tells whether a round is unfinished, and `metadata_of` gives the task's accepted
        subject and description. Remote failures are retried on `clock` with `settings` and
        reported through `faults`.
        """
        self._github = github
        self._events = events
        self._feedback = feedback
        self._tasks = tasks
        self._project_root = project_root
        self._metadata_of = metadata_of
        self._clock = clock
        self._faults = faults
        self._settings = settings
        self._locks: dict[str, asyncio.Lock] = {}

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

    async def request(self, task: Task, url: str, head: str) -> PullRequestStatus | None:
        """Request squash auto-merge of the pull request at `url` if `task` may merge now.

        Nothing is sent when GitHub already holds exactly these values. Otherwise a stale request
        is withdrawn first, the request is recorded, and GitHub is asked. A merged pull request
        discovered while resolving an ambiguous request is returned to the landing flow. A refusal,
        confirmed on GitHub, raises `AutoMergeRefused`.
        """
        async with self._lock(task.id):
            current = self._tasks.require(task.id)
            if not self.may_merge(current):
                return None
            metadata = self._metadata_of(current)
            title = task_commit_subject(metadata.subject, task.id)
            wanted = _Request(
                url=url,
                head=head,
                subject=f"{title} (#{parse_pull_request_number(url)})",
                body=metadata.pr_description,
            )
            can_request, held = await self._request_state(task.id, url)
            if not can_request:
                return None
            if wanted.is_held_by(held):
                if not self._recorded_as(task.id, wanted):
                    self._record_request(task.id, wanted)
                await self._withdraw_if_not_final(task.id, url)
                return None
            if held is not None:
                await self._disable(task.id, url)
            self._record_request(task.id, wanted)
            status = await self._request(task.id, url, wanted)
            await self._withdraw_if_not_final(task.id, url)
            return status

    async def withdraw(self, task_id: str, url: str | None = None) -> None:
        """Withdraw the auto-merge request of `task_id`, confirming on GitHub that it is gone.

        The GitHub state is inspected even without a local request record, because an adopted pull
        request may already have auto-merge enabled. A pull request that is no longer open has
        nothing left to withdraw. Otherwise a request GitHub still holds raises
        `AutoMergeWithdrawFailed`.
        """
        async with self._lock(task_id):
            await self._withdraw(task_id, url)

    async def _withdraw(self, task_id: str, url: str | None) -> None:
        latest = self._latest(task_id)
        url = url or self._open_pull_request_url(task_id)
        if url is None and latest is not None:
            url = str(latest.data[_URL])
        if url is None:
            return
        state = (await self._pull_request_status(task_id, url)).state
        held = (
            await self._auto_merge_request(task_id, url) if state is PullRequestState.OPEN else None
        )
        if held is not None:
            await self._disable(task_id, url)
        if (
            latest is not None and latest.kind is EventKind.PR_AUTO_MERGE_REQUESTED
        ) or held is not None:
            self._events.append(
                EventKind.PR_AUTO_MERGE_CANCELLED,
                task_id=task_id,
                message=f"{task_id} withdrew the auto-merge request of {url}",
                data={_URL: url, _STATE: state.value},
            )

    async def _withdraw_if_not_final(self, task_id: str, url: str) -> None:
        if not self.may_merge(self._tasks.require(task_id)):
            await self._withdraw(task_id, url)

    def _lock(self, task_id: str) -> asyncio.Lock:
        return self._locks.setdefault(task_id, asyncio.Lock())

    async def _request_state(self, task_id: str, url: str) -> tuple[bool, AutoMergeRequest | None]:
        delay = 0.0
        attempts = self._settings.network.poll_attempts
        for attempt in range(1, attempts + 1):
            if not self.may_merge(self._tasks.require(task_id)):
                await self._withdraw(task_id, url)
                return False, None
            try:
                held = await self._github.auto_merge_request(self._project_root, url)
            except PROCESS_FAILURES as error:
                self._faults.report(error, scope=_AUTO_MERGE_READ_FAULT_SCOPE, task_id=task_id)
                if not self.may_merge(self._tasks.require(task_id)):
                    await self._withdraw(task_id, url)
                    return False, None
                if attempt == attempts:
                    detail = f"could not read auto-merge request after {attempts} attempts: {error}"
                    raise AutoMergeReadFailed(task_id, url, detail) from error
                delay = self._next_retry_delay(delay)
                await self._clock.sleep(delay)
            else:
                self._faults.resolve(_AUTO_MERGE_READ_FAULT_SCOPE, task_id=task_id)
                return True, held
        raise AssertionError(_RETRY_LOOP_EXHAUSTED)

    async def _request(self, task_id: str, url: str, wanted: _Request) -> PullRequestStatus | None:
        delay = 0.0
        attempts = self._settings.network.poll_attempts
        for attempt in range(1, attempts + 1):
            if not self.may_merge(self._tasks.require(task_id)):
                return None
            if attempt > 1:
                can_retry, status = await self._before_request_retry(task_id, url, wanted)
                if not can_retry:
                    return status
            try:
                await self._github.request_auto_merge(
                    self._project_root,
                    url,
                    head=wanted.head,
                    subject=wanted.subject,
                    body=wanted.body,
                )
            except PROCESS_FAILURES as error:
                self._faults.report(error, scope=_REQUEST_FAULT_SCOPE, task_id=task_id)
                can_retry, status = await self._after_request_failure(
                    task_id, url, wanted, attempt=attempt, attempts=attempts, error=error
                )
                if status is not None:
                    return status
                if not can_retry:
                    return None
                delay = self._next_retry_delay(delay)
                await self._clock.sleep(delay)
            else:
                self._faults.resolve(_REQUEST_FAULT_SCOPE, task_id=task_id)
                return None
        return None

    async def _before_request_retry(
        self, task_id: str, url: str, wanted: _Request
    ) -> tuple[bool, PullRequestStatus | None]:
        status = await self._pull_request_status(task_id, url)
        if status.state is not PullRequestState.OPEN:
            self._faults.resolve(_REQUEST_FAULT_SCOPE, task_id=task_id)
            return False, status
        held = await self._auto_merge_request(task_id, url)
        if wanted.is_held_by(held):
            self._faults.resolve(_REQUEST_FAULT_SCOPE, task_id=task_id)
            return False, None
        if held is not None:
            await self._disable(task_id, url)
        return self.may_merge(self._tasks.require(task_id)), None

    async def _after_request_failure(
        self,
        task_id: str,
        url: str,
        wanted: _Request,
        *,
        attempt: int,
        attempts: int,
        error: Exception,
    ) -> tuple[bool, PullRequestStatus | None]:
        held = await self._auto_merge_request(task_id, url)
        if wanted.is_held_by(held):
            self._faults.resolve(_REQUEST_FAULT_SCOPE, task_id=task_id)
            return False, None
        if held is not None:
            await self._disable(task_id, url)
        if not self.may_merge(self._tasks.require(task_id)):
            return False, None
        status = await self._pull_request_status(task_id, url)
        if status.state is not PullRequestState.OPEN:
            self._faults.resolve(_REQUEST_FAULT_SCOPE, task_id=task_id)
            return False, status
        if not self.may_merge(self._tasks.require(task_id)):
            return False, None
        if attempt == attempts:
            self._events.append(
                EventKind.PR_AUTO_MERGE_CANCELLED,
                task_id=task_id,
                message=f"{task_id} did not get auto-merge of {url}",
                data={_URL: url, _STATE: _REFUSED_STATE},
            )
            raise AutoMergeRefused(task_id, url, str(error)) from error
        return True, None

    async def _pull_request_status(self, task_id: str, url: str) -> PullRequestStatus:
        return await self._retry_read(
            task_id,
            url,
            "pull request status",
            _STATUS_READ_FAULT_SCOPE,
            lambda: self._github.pull_request_status(self._project_root, url),
        )

    async def _auto_merge_request(self, task_id: str, url: str) -> AutoMergeRequest | None:
        return await self._retry_read(
            task_id,
            url,
            "auto-merge request",
            _AUTO_MERGE_READ_FAULT_SCOPE,
            lambda: self._github.auto_merge_request(self._project_root, url),
        )

    async def _retry_read(
        self,
        task_id: str,
        url: str,
        description: str,
        scope: str,
        call: Callable[[], Awaitable[_Result]],
    ) -> _Result:
        delay = 0.0
        attempts = self._settings.network.poll_attempts
        for attempt in range(1, attempts + 1):
            try:
                result = await call()
            except PROCESS_FAILURES as error:
                self._faults.report(error, scope=scope, task_id=task_id)
                if attempt == attempts:
                    detail = f"could not read {description} after {attempts} attempts: {error}"
                    raise AutoMergeReadFailed(task_id, url, detail) from error
                delay = self._next_retry_delay(delay)
                await self._clock.sleep(delay)
            else:
                self._faults.resolve(scope, task_id=task_id)
                return result
        raise AssertionError(_RETRY_LOOP_EXHAUSTED)

    async def _disable(self, task_id: str, url: str) -> None:
        delay = 0.0
        attempts = self._settings.network.poll_attempts
        for attempt in range(1, attempts + 1):
            try:
                await self._github.cancel_auto_merge(self._project_root, url)
            except PROCESS_FAILURES as error:
                self._faults.report(error, scope=_WITHDRAW_FAULT_SCOPE, task_id=task_id)
                held = await self._auto_merge_request(task_id, url)
                if held is None:
                    self._faults.resolve(_WITHDRAW_FAULT_SCOPE, task_id=task_id)
                    return
                if attempt == attempts:
                    raise AutoMergeWithdrawFailed(task_id, url, str(error)) from error
            else:
                self._faults.resolve(_WITHDRAW_FAULT_SCOPE, task_id=task_id)
                if await self._auto_merge_request(task_id, url) is None:
                    return
                if attempt == attempts:
                    raise AutoMergeWithdrawFailed(task_id, url, "GitHub still holds the request")
            delay = self._next_retry_delay(delay)
            await self._clock.sleep(delay)

    def _next_retry_delay(self, previous: float) -> float:
        faults = self._settings.faults
        return next_backoff(
            previous,
            initial=faults.backoff_initial_seconds,
            maximum=faults.backoff_max_seconds,
        )

    def _record_request(self, task_id: str, wanted: _Request) -> None:
        self._events.append(
            EventKind.PR_AUTO_MERGE_REQUESTED,
            task_id=task_id,
            message=f"{task_id} requested auto-merge of {wanted.url}",
            data=wanted.as_data(),
        )

    def _recorded_as(self, task_id: str, wanted: _Request) -> bool:
        latest = self._latest(task_id)
        return (
            latest is not None
            and latest.kind is EventKind.PR_AUTO_MERGE_REQUESTED
            and latest.data == wanted.as_data()
        )

    def _latest(self, task_id: str) -> Event | None:
        records = self._events.since(0, kinds=_REQUEST_RECORDS, task_id=task_id)
        return records[-1] if records else None

    def _open_pull_request_url(self, task_id: str) -> str | None:
        records = self._events.since(
            0, kinds=(EventKind.PR_OPENED, EventKind.PR_WITHDRAWN), task_id=task_id
        )
        latest = records[-1] if records else None
        if latest is None or latest.kind is not EventKind.PR_OPENED:
            return None
        return str(latest.data[_URL])


class AutoMergeReconciler:
    """Withdraws auto-merge requests that are no longer final, on its own task off the engine loop.

    A task is checked on its transition to non-final and on each engine start, then no more often
    than the pull request polling interval. Synchronous withdrawal before a hold or round still
    takes place in the caller. A failed withdrawal is recorded as a fault and retried on a backoff.
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
        self._checked_nonfinal: dict[str, datetime] = {}

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
                self._checked_nonfinal.pop(task.id, None)
                continue
            checked_at = self._checked_nonfinal.get(task.id)
            interval = timedelta(seconds=self._settings.network.pr_poll_seconds)
            if checked_at is not None and self._clock.now() - checked_at < interval:
                continue
            try:
                await self._auto_merge.withdraw(task.id)
            except RECOVERABLE_FAULTS as error:
                failed = True
                self._faults.report(error, scope=AUTO_MERGE_FAULT_SCOPE, task_id=task.id)
            else:
                self._checked_nonfinal[task.id] = self._clock.now()
                self._faults.resolve(AUTO_MERGE_FAULT_SCOPE, task_id=task.id)
        return failed
