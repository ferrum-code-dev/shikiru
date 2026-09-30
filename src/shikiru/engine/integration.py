"""Rebase, conflict hand-off, local merge and pull request orchestration (03-workflow.md §6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, RepoState, Status, Task
from shikiru.errors import CommandFailed, ShikiruError
from shikiru.paths import task_root
from shikiru.workspace.base_refs import MergedBaseUnavailable
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.events import Event
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.base_refs import FetchedBaseRefs
    from shikiru.workspace.git import Git
    from shikiru.workspace.github import GitHub, PullRequestStatus

INTEGRATION_RETRY = timedelta(minutes=2)
"""Delay between blocked integration pre-checks (05-data.md §1)."""

PR_POLL = timedelta(minutes=5)
"""Delay between polls of an open pull request (05-data.md §1)."""


class IntegrationBlocked(ShikiruError):
    """The local base checkout stayed unavailable for the integration wait budget."""

    reason = EscalationReason.INTEGRATION_BLOCKED

    def __init__(self, message: str = "local integration remained blocked") -> None:
        """Build the integration-blocked error, saying what stayed blocked."""
        super().__init__(message)


class IntegrationHeld(Exception):
    """A task hold was set before integration could start its next external effect."""


class IntegrationConflict(ShikiruError):
    """Conflict recovery could not produce an accepted post-rebase result."""

    reason = EscalationReason.INTEGRATION_CONFLICT

    def __init__(self) -> None:
        """Build the integration-conflict error."""
        super().__init__("integration conflicts could not be resolved")


class UncommittedWork(ShikiruError):
    """The task worktree holds uncommitted changes that integration would discard."""

    reason = EscalationReason.UNCOMMITTED_WORK

    def __init__(self, task_id: str, paths: Sequence[str]) -> None:
        """Build the error for `task_id` listing the porcelain status lines in `paths`."""
        listed = ", ".join(line.strip() for line in paths)
        super().__init__(f"{task_id} has uncommitted changes that cannot be integrated: {listed}")


class PullRequestClosed(ShikiruError):
    """The task's pull request was closed without being merged."""

    reason = EscalationReason.PR_CLOSED

    def __init__(self, url: str) -> None:
        """Build the pull-request-closed error for the PR at `url`."""
        super().__init__(f"pull request closed without merging: {url}")


@dataclass(frozen=True, slots=True)
class IntegrationSummary:
    """Observable result of a successful integration."""

    summary: str
    commits: tuple[str, ...] = ()
    residual_risks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntegrationMetadata:
    """Accepted Leader context carried into the terminal task event."""

    summary: str
    residual_risks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergeRequest:
    """A rebased task branch ready to be applied to its base."""

    task_id: str
    title: str
    repo: RepoState
    task_root: Path
    summary: str
    held: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class _MergeCandidate:
    base_sha: str
    branch_sha: str
    base_checked_out: bool
    commits: tuple[str, ...]


class _PrecheckFailed(Exception):
    pass


type ContinueRebase = Callable[[], Awaitable[None]]
type ConflictResolver = Callable[
    [Task, ContinueRebase], Awaitable[tuple[Task, IntegrationMetadata]]
]
type TaskFinisher = Callable[[Task], Awaitable[Path | None]]


@dataclass(frozen=True, slots=True)
class _OpenPullRequest:
    url: str
    commits: tuple[str, ...]


class IntegrationStrategy(Protocol):
    """A strategy that applies an already rebased task branch to its base."""

    def started(self, task_id: str) -> tuple[str, ...] | None:
        """Return the commits of `task_id`'s started, unsettled integration, if there is one."""
        ...

    async def merge(self, request: MergeRequest) -> tuple[str, ...]:
        """Merge the task branch into its base, or start doing so, and return its commits."""
        ...

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Wait until `task_id`'s started integration has landed in its base `repo`.

        Raises `IntegrationHeld` instead of starting another wait step once `held()` is true.
        """
        ...


class IntegrationCoordinator(Protocol):
    """TaskFlow's required boundary to the integration coordinator."""

    async def run(
        self,
        task: Task,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
    ) -> IntegrationSummary:
        """Integrate `task` and finalize it, resolving conflicts through TaskFlow."""
        ...

    async def continue_rebase(self, task: Task) -> None:
        """Continue `task`'s rebase after its conflicts were resolved, if one is in progress."""
        ...


class LocalMerge:
    """Apply a rebased branch locally using fast-forward or a compare-and-swap ref update."""

    def __init__(
        self, git: Git, clock: Clock, *, project_root: Path, integration_wait: int
    ) -> None:
        """Use `git` on `project_root` for repository operations and `clock` for retry deadlines."""
        self._git = git
        self._clock = clock
        self._project_root = project_root
        self._integration_wait = integration_wait

    def started(self, task_id: str) -> tuple[str, ...] | None:
        """Return `None`: a local merge lands within `merge`, so none is ever left unsettled."""
        del task_id

    async def merge(self, request: MergeRequest) -> tuple[str, ...]:
        """Pre-check the local base, then apply the merge, retrying while it is blocked."""
        project_root = self._project_root
        task_root_path = request.task_root
        repo = request.repo
        deadline = self._clock.now() + timedelta(seconds=self._integration_wait)
        while True:
            try:
                candidate = await self._precheck(project_root, task_root_path, repo)
                if candidate is not None:
                    if request.held():
                        raise IntegrationHeld
                    await self._apply(project_root, repo, candidate)
            except _PrecheckFailed, CommandFailed:
                if request.held():
                    raise IntegrationHeld from None
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    raise IntegrationBlocked from None
                await self._clock.sleep(min(INTEGRATION_RETRY.total_seconds(), remaining))
                continue
            break
        return candidate.commits if candidate is not None else ()

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Return at once: `merge` already landed the branch in its base."""
        del task_id, repo, held

    async def _apply(self, project_root: Path, repo: RepoState, candidate: _MergeCandidate) -> None:
        if candidate.base_checked_out:
            await self._git.merge_ff_only(project_root, repo.branch)
        else:
            await self._git.update_ref(
                project_root, repo.base, candidate.branch_sha, candidate.base_sha
            )

    async def _precheck(
        self, project_root: Path, task_root_path: Path, repo: RepoState
    ) -> _MergeCandidate | None:
        base_sha = await self._git.rev_parse(project_root, repo.base)
        branch_sha = await self._git.rev_parse(task_root_path, repo.branch)
        if branch_sha == base_sha:
            return None
        if not await self._git.merge_base_is_ancestor(project_root, repo.base, repo.branch):
            raise _PrecheckFailed
        base_checked_out = await self._git.current_branch(project_root) == repo.base
        if base_checked_out:
            changed = set(
                await self._git.diff_name_only(task_root_path, f"{base_sha}...{branch_sha}")
            )
            dirty = await self._git.dirty_paths(project_root)
            if _paths_overlap(changed, dirty):
                raise _PrecheckFailed
        commits = tuple(await self._git.log_oneline(task_root_path, f"{base_sha}..{branch_sha}"))
        return _MergeCandidate(
            base_sha=base_sha,
            branch_sha=branch_sha,
            base_checked_out=base_checked_out,
            commits=commits,
        )


def _paths_overlap(changed: set[str], dirty: set[str]) -> bool:
    for left in changed:
        normalized_left = left.rstrip("/")
        for right in dirty:
            normalized_right = right.rstrip("/")
            if (
                normalized_left == normalized_right
                or normalized_left.startswith(f"{normalized_right}/")
                or normalized_right.startswith(f"{normalized_left}/")
            ):
                return True
    return False


class PullRequest:
    """Push a rebased branch, open a pull request for it and poll it until it lands (03 §6)."""

    def __init__(
        self,
        *,
        git: Git,
        github: GitHub,
        clock: Clock,
        tasks: TaskStore,
        events: EventLog,
        project_root: Path,
        remote: str,
        base_refs: FetchedBaseRefs,
    ) -> None:
        """Push to `remote` from `project_root`; record the PR in `events`, waits in `tasks`.

        A merged PR's base is fetched and verified through `base_refs` before the task is done.
        """
        self._git = git
        self._github = github
        self._clock = clock
        self._tasks = tasks
        self._events = events
        self._project_root = project_root
        self._remote = remote
        self._base_refs = base_refs

    def started(self, task_id: str) -> tuple[str, ...] | None:
        """Return the commits of `task_id`'s open pull request, if it has one."""
        opened = self._open_pull_request(task_id)
        return opened.commits if opened is not None else None

    async def merge(self, request: MergeRequest) -> tuple[str, ...]:
        """Push the branch and open its pull request, recorded as `pr_opened`.

        An open pull request of the branch that was never recorded (the engine died between
        opening and recording it) is adopted instead of opening another.
        """
        repo = request.repo
        commits = tuple(
            await self._git.log_oneline(request.task_root, f"{repo.base_ref}..{repo.branch}")
        )
        if not commits:
            return ()
        lease = await self._git.remote_branch_sha(self._project_root, self._remote, repo.branch)
        await self._git.push(self._project_root, self._remote, repo.branch, lease=lease)
        url = await self._github.open_pull_request(
            self._project_root, base=repo.base, head=repo.branch
        ) or await self._github.create_pull_request(
            self._project_root,
            base=repo.base,
            head=repo.branch,
            title=request.title,
            body=request.summary,
        )
        self._events.append(
            EventKind.PR_OPENED,
            task_id=request.task_id,
            message=f"{request.task_id} opened {url}",
            data={"url": url, "branch": repo.branch, "commits": list(commits)},
        )
        return commits

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Poll the open pull request until it is merged or closed, then verify the merged base.

        Raises `PullRequestClosed` for a closed one, `IntegrationBlocked` when the merged
        commit cannot be fetched from and verified on the remote base, and `IntegrationHeld`
        before any poll once `held()` is true.
        """
        opened = self._open_pull_request(task_id)
        if opened is None:
            return
        self._tasks.set_slot(task_id, None)
        self._tasks.set_wait_reason(task_id, WaitReason.PR_OPEN)
        while True:
            if held():
                raise IntegrationHeld
            status = await self._status(opened.url)
            if status.state is not PullRequestState.OPEN:
                break
            await self._clock.sleep(PR_POLL.total_seconds())
        self._tasks.set_wait_reason(task_id, None)
        if status.state is PullRequestState.CLOSED:
            raise PullRequestClosed(opened.url)
        try:
            await self._base_refs.sync(repo.base, status.merge_commit)
        except (CommandFailed, MergedBaseUnavailable) as error:
            raise IntegrationBlocked(str(error)) from error

    async def _status(self, url: str) -> PullRequestStatus:
        return await self._github.pull_request_status(self._project_root, url)

    def _open_pull_request(self, task_id: str) -> _OpenPullRequest | None:
        events = self._events.since(
            0, kinds=[EventKind.PR_OPENED, EventKind.ESCALATION], task_id=task_id
        )
        return _open_pull_request(events)


def _open_pull_request(events: Sequence[Event]) -> _OpenPullRequest | None:
    opened: _OpenPullRequest | None = None
    for event in events:
        if event.kind is EventKind.PR_OPENED:
            opened = _OpenPullRequest(event.data["url"], tuple(event.data["commits"]))
        elif event.data["reason"] == EscalationReason.PR_CLOSED:
            opened = None
    return opened


class Integrator:
    """Own the project integration lock, rebase protocol and terminal cleanup."""

    def __init__(
        self,
        *,
        git: Git,
        strategy: IntegrationStrategy,
        lock: asyncio.Lock,
        project_root: Path,
        tasks: TaskStore,
        events: EventLog,
        finish_task: TaskFinisher,
    ) -> None:
        """Inject every state, cleanup and repository boundary required by integration."""
        self._git = git
        self._strategy = strategy
        self._lock = lock
        self._project_root = project_root
        self._tasks = tasks
        self._events = events
        self._finish_task = finish_task

    async def run(
        self,
        task: Task,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
    ) -> IntegrationSummary:
        """Rebase and integrate `task`, delegating conflict turns to TaskFlow.

        An integration the strategy already started (an open PR) is only settled, never
        rebased or applied again (03 §6-7).
        """
        repo = task.repo
        if repo is None:
            message = f"{task.id} has no task root to integrate"
            raise ShikiruError(message)
        current, current_metadata = task, metadata
        commits = self._strategy.started(task.id)
        if commits is None:
            current, current_metadata, commits = await self._apply(
                task, repo, resolve_conflicts, metadata
            )
        await self._strategy.settle(current.id, repo, lambda: self._is_held(current.id))
        summary = IntegrationSummary(
            summary=current_metadata.summary,
            commits=commits,
            residual_risks=current_metadata.residual_risks,
        )
        self._tasks.set_summary(current.id, summary.summary)
        updated = self._tasks.get(current.id)
        if updated is None:
            message = f"{current.id} vanished before integration could finish"
            raise ShikiruError(message)
        self._tasks.finish(
            current.id,
            _done_transition(updated),
            EventKind.TASK_DONE,
            message=summary.summary,
            data={
                "summary": summary.summary,
                "commits": list(summary.commits),
                "residual_risks": list(summary.residual_risks),
            },
        )
        done = self._tasks.get(current.id)
        if done is None:
            message = f"{current.id} vanished after integration completed"
            raise ShikiruError(message)
        await self._finish_task(done)
        return summary

    async def _apply(
        self,
        task: Task,
        repo: RepoState,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
    ) -> tuple[Task, IntegrationMetadata, tuple[str, ...]]:
        async with self._lock:
            self._require_not_held(task.id)
            self._events.append(
                EventKind.INTEGRATION_STARTED,
                task_id=task.id,
                message=f"{task.id} integration started",
                data={"branch": repo.branch, "base": repo.base},
            )
            current = task
            current_metadata = metadata
            task_dir = task_root(self._project_root, current.id)
            await self._require_committed(current, task_dir)
            base_sha = await self._git.rev_parse(task_dir, repo.base_ref)
            branch_sha = await self._git.rev_parse(task_dir, repo.branch)
            if base_sha != branch_sha and await self._try_rebase(task_dir, repo.base_ref):
                current, current_metadata = await resolve_conflicts(
                    current, lambda: self._continue_rebase(current.id, task_dir)
                )
                await self._continue_rebase(current.id, task_dir)
            await self._require_committed(current, task_dir)
            self._require_not_held(current.id)
            commits = await self._strategy.merge(
                MergeRequest(
                    task_id=current.id,
                    title=current.title,
                    repo=repo,
                    task_root=task_dir,
                    summary=current_metadata.summary,
                    held=lambda: self._is_held(current.id),
                )
            )
            return current, current_metadata, commits

    def _is_held(self, task_id: str) -> bool:
        current = self._tasks.get(task_id)
        if current is None:
            message = f"{task_id} vanished during integration"
            raise ShikiruError(message)
        return current.held

    def _require_not_held(self, task_id: str) -> None:
        if self._is_held(task_id):
            raise IntegrationHeld

    async def _require_committed(self, task: Task, task_dir: Path) -> None:
        if await self._git.is_rebase_in_progress(task_dir):
            return
        status = await self._git.status_porcelain(task_dir)
        if status:
            raise UncommittedWork(task.id, status)

    async def continue_rebase(self, task: Task) -> None:
        """Continue `task`'s rebase after its conflicts were resolved, if one is in progress."""
        await self._continue_rebase(task.id, task_root(self._project_root, task.id))

    async def _try_rebase(self, task_dir: Path, base: str) -> bool:
        try:
            await self._git.rebase(task_dir, base)
        except CommandFailed:
            if not await self._git.is_rebase_in_progress(task_dir):
                raise
            return True
        return False

    async def _continue_rebase(self, task_id: str, task_dir: Path) -> None:
        if not await self._git.is_rebase_in_progress(task_dir):
            return
        self._require_not_held(task_id)
        try:
            await self._git.rebase_continue(task_dir)
        except CommandFailed:
            if await self._git.is_rebase_in_progress(task_dir):
                raise IntegrationConflict from None
            raise


def _done_transition(task: Task) -> Transition:
    return Transition.stay(task, step=None, status=Status.DONE)
