"""Rebase, conflict hand-off, local merge and pull request orchestration."""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from shikiru.config.settings import (
    MERGE_INTEGRATION,
    PULL_REQUEST_INTEGRATION,
    SQUASH_INTEGRATION,
)
from shikiru.domain.events import EventKind
from shikiru.domain.faults import next_backoff
from shikiru.domain.results import (
    CHANGES_DELETIONS_KEY,
    CHANGES_FILES_KEY,
    CHANGES_INSERTIONS_KEY,
    DONE_CHANGES_KEY,
    DONE_INTEGRATION_KEY,
    landed_changes,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, RepoState, Status, Task
from shikiru.errors import CommandFailed, CommandTimeoutError, InvariantError, ShikiruError
from shikiru.paths import task_root
from shikiru.workspace.base_refs import BaseFetchFailed, MergedBaseUnavailable
from shikiru.workspace.facts import describe_off_branch
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import OrchestrationConfig
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.base_refs import BaseRefs, FetchedBaseRefs
    from shikiru.workspace.git import Git
    from shikiru.workspace.github import GitHub, PullRequestStatus

INTEGRATION_RETRY = timedelta(minutes=2)
"""Delay between blocked integration pre-checks."""

POLL_FAULT_SCOPE = "pull request poll"
GIT_FAILURE_DETAIL_LIMIT = 4000
GIT_FAILURE_TRUNCATION = " [truncated]"
SQUASH_TASK_TRAILER = "Shikiru-Task"
SQUASH_TIP_TRAILER = "Shikiru-Squashed-Tip"

_PR_ENDED_BY = frozenset((EscalationReason.PR_CLOSED,))
_INTEGRATION_RESTARTED_BY = _PR_ENDED_BY | {EscalationReason.INTEGRATION_CONFLICT}


class IntegrationBlocked(ShikiruError):
    """An integration prerequisite or Git operation could not complete."""

    reason = EscalationReason.INTEGRATION_BLOCKED

    def __init__(self, message: str = "integration remained blocked") -> None:
        """Build the integration-blocked error, saying what stayed blocked."""
        super().__init__(message)


class PullRequestStalled(IntegrationBlocked):
    """An open pull request cannot land: it conflicts, fails its checks or waited too long."""

    def __init__(self, url: str, state: str) -> None:
        """Build the error for the PR at `url`, saying which `state` it is stuck in."""
        super().__init__(f"pull request {url} is {state}")


class IntegrationHeld(Exception):
    """A task hold was set before integration could start its next external effect."""


class IntegrationConflict(ShikiruError):
    """Conflict recovery could not produce an accepted post-rebase result."""

    reason = EscalationReason.INTEGRATION_CONFLICT

    def __init__(self, message: str = "integration conflicts could not be resolved") -> None:
        """Build the integration-conflict error, saying what conflicted."""
        super().__init__(message)

    @classmethod
    def unresolved_rebase(cls, task_id: str, rounds: int, context: str) -> IntegrationConflict:
        """Describe paths that remain unresolved after the bounded conflict turns."""
        return cls(
            f"{task_id}: the rebase still has unresolved paths after "
            f"{rounds} conflict turns: {context}"
        )


class RebaseConflict(Exception):
    """Git still reports unmerged paths after a conflict turn."""

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = tuple(paths)
        super().__init__(f"rebase still has unmerged paths: {', '.join(self.paths)}")


class PullRequestConflicting(IntegrationConflict):
    """An open pull request conflicts with its base: only a new rebase can resolve it."""

    def __init__(self, url: str) -> None:
        """Build the error for the PR at `url`."""
        super().__init__(f"pull request {url} is conflicting with its base")


class UncommittedWork(ShikiruError):
    """The task worktree holds uncommitted changes that integration would discard."""

    reason = EscalationReason.UNCOMMITTED_WORK

    def __init__(self, task_id: str, paths: Sequence[str]) -> None:
        """Build the error for `task_id` listing the porcelain status lines in `paths`."""
        listed = ", ".join(line.strip() for line in paths)
        super().__init__(f"{task_id} has uncommitted changes that cannot be integrated: {listed}")


class OffTaskBranch(ShikiruError):
    """The task worktree's `HEAD` is off the task branch, so merging the branch loses its work."""

    reason = EscalationReason.OFF_TASK_BRANCH

    def __init__(
        self, task_id: str, branch: str, head_branch: str | None, commits: Sequence[str]
    ) -> None:
        """Build the error for `task_id`, naming where `HEAD` is and the commits at risk."""
        super().__init__(f"{task_id}: {describe_off_branch(branch, head_branch, commits)}")


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
class LandedChanges:
    """Final change totals of work as it landed in its base."""

    files: int
    insertions: int
    deletions: int

    def as_data(self) -> dict[str, int]:
        """Return the totals as recorded in event data."""
        return {
            CHANGES_FILES_KEY: self.files,
            CHANGES_INSERTIONS_KEY: self.insertions,
            CHANGES_DELETIONS_KEY: self.deletions,
        }


@dataclass(frozen=True, slots=True)
class Landing:
    """The commits an integration put in its base and the final totals of what they changed.

    A landing without `changes` is one whose totals are not known.
    """

    commits: tuple[str, ...] = ()
    integration: str | None = None
    changes: LandedChanges | None = None

    def done_data(self) -> dict[str, object]:
        """Return the evidence `TASK_DONE` records about what landed."""
        data: dict[str, object] = {"commits": list(self.commits)}
        if self.changes is not None:
            data[DONE_INTEGRATION_KEY] = self.integration
            data[DONE_CHANGES_KEY] = self.changes.as_data()
        return data


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
    landing_sha: str
    base_checked_out: bool
    landing: Landing


@dataclass(frozen=True, slots=True)
class ConflictRecovery:
    """Durable conflict-turn progress passed from the integration flow to its coordinator."""

    resumed_report: bool = False
    completed_turns: int = 0
    force_new_turn: bool = False


class _PrecheckFailed(Exception):
    pass


class _CheckoutDirty(_PrecheckFailed):
    def __init__(self, paths: tuple[str, ...]) -> None:
        super().__init__(", ".join(paths))
        self.paths = paths


type ContinueRebase = Callable[[], Awaitable[None]]
type ConflictResolver = Callable[
    [Task, ContinueRebase, ConflictRecovery], Awaitable[tuple[Task, IntegrationMetadata]]
]
type ConflictRecoveryLoader = Callable[[Task], ConflictRecovery]
type TaskFinisher = Callable[[Task], Awaitable[Path | None]]


@dataclass(frozen=True, slots=True)
class _OpenPullRequest:
    url: str
    landing: Landing


class IntegrationStrategy(Protocol):
    """A strategy that applies an already rebased task branch to its base."""

    def started(self, task_id: str) -> Landing | None:
        """Return what `task_id`'s started, unsettled integration holds, if there is one."""
        ...

    async def merge(self, request: MergeRequest) -> Landing:
        """Merge the task branch into its base, or start doing so, and return what landed."""
        ...

    async def applied(self, repo: RepoState, task_root: Path) -> Landing | None:
        """Return what an earlier, interrupted integration of `repo` landed in its base, if any."""
        ...

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Wait until `task_id`'s started integration has landed in its base `repo`.

        Raises `IntegrationHeld` instead of starting another wait step once `held()` is true.
        """
        ...


class BasePublication(Protocol):
    """Where a completed local integration registers its base branch for a later push."""

    def register(self, base: str) -> None:
        """Record an integration of `base` that must reach the remote."""
        ...


class IntegrationCoordinator(Protocol):
    """TaskFlow's required boundary to the integration coordinator."""

    async def run(
        self,
        task: Task,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
        conflict_recovery: ConflictRecoveryLoader | None = None,
    ) -> IntegrationSummary:
        """Integrate `task` and finalize it, resolving conflicts through TaskFlow."""
        ...

    async def continue_rebase(self, task: Task) -> None:
        """Continue `task`'s rebase after its conflicts were resolved, if one is in progress."""
        ...


class LocalMerge:
    """Apply a rebased branch locally using fast-forward or a compare-and-swap ref update."""

    def __init__(
        self,
        git: Git,
        clock: Clock,
        *,
        events: EventLog,
        project_root: Path,
        integration_wait: int,
    ) -> None:
        """Use `git` on `project_root` for repository operations and `clock` for retry deadlines.

        A wait for a dirty checkout is recorded once in `events` as `integration_waiting`.
        """
        self._git = git
        self._clock = clock
        self._events = events
        self._project_root = project_root
        self._integration_wait = integration_wait

    def started(self, task_id: str) -> Landing | None:
        """Return `None`: a local merge lands within `merge`, so none is ever left unsettled."""
        del task_id

    async def applied(self, repo: RepoState, task_root: Path) -> Landing | None:
        """Return `None`: a rebased branch fast-forwards, so its ancestry shows it landed."""
        del repo, task_root

    async def merge(self, request: MergeRequest) -> Landing:
        """Pre-check the local base, then apply the merge, retrying while it is blocked."""
        project_root = self._project_root
        repo = request.repo
        deadline = self._clock.now() + timedelta(seconds=self._integration_wait)
        announced = False
        while True:
            try:
                candidate = await self._precheck(project_root, request)
                if candidate is not None:
                    if request.held():
                        raise IntegrationHeld
                    await self._apply(project_root, repo, candidate)
            except (_PrecheckFailed, CommandFailed) as error:
                if isinstance(error, _CheckoutDirty) and not announced:
                    announced = True
                    self._announce_wait(request.task_id, error.paths)
                if request.held():
                    raise IntegrationHeld from None
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    raise IntegrationBlocked from None
                await self._clock.sleep(min(INTEGRATION_RETRY.total_seconds(), remaining))
                continue
            break
        return candidate.landing if candidate is not None else self._nothing_landed()

    def _announce_wait(self, task_id: str, paths: tuple[str, ...]) -> None:
        self._events.append(
            EventKind.INTEGRATION_WAITING,
            task_id=task_id,
            message=f"{task_id} waits to integrate: the project checkout has uncommitted "
            f"changes on {', '.join(paths)}",
            data={"paths": list(paths)},
        )

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Return at once: `merge` already landed the branch in its base."""
        del task_id, repo, held

    async def _apply(self, project_root: Path, repo: RepoState, candidate: _MergeCandidate) -> None:
        if candidate.base_checked_out:
            await self._git.merge_ff_only(project_root, candidate.landing_sha)
        else:
            await self._git.update_ref(
                project_root, repo.base, candidate.landing_sha, candidate.base_sha
            )

    _integration = MERGE_INTEGRATION

    def _nothing_landed(self) -> Landing:
        return Landing(integration=self._integration, changes=LandedChanges(0, 0, 0))

    async def _is_empty(self, project_root: Path, base_sha: str, branch_sha: str) -> bool:
        del project_root
        return branch_sha == base_sha

    async def _landing_for(
        self, request: MergeRequest, base_sha: str, branch_sha: str
    ) -> tuple[str, Landing]:
        commits = tuple(await self._git.log_oneline(request.task_root, f"{base_sha}..{branch_sha}"))
        changes = await _landed_changes(self._git, request.task_root, base_sha, branch_sha)
        return branch_sha, Landing(commits, integration=self._integration, changes=changes)

    async def _precheck(self, project_root: Path, request: MergeRequest) -> _MergeCandidate | None:
        task_root_path = request.task_root
        repo = request.repo
        base_sha = await self._git.rev_parse(project_root, repo.base)
        branch_sha = await self._git.rev_parse(task_root_path, repo.branch)
        if await self._is_empty(project_root, base_sha, branch_sha):
            return None
        if not await self._git.merge_base_is_ancestor(project_root, repo.base, repo.branch):
            raise _PrecheckFailed
        base_checked_out = await self._git.current_branch(project_root) == repo.base
        if base_checked_out:
            changed = set(
                await self._git.diff_name_only(task_root_path, f"{base_sha}...{branch_sha}")
            )
            dirty = await self._git.dirty_paths(project_root)
            blocking = _blocking_paths(changed, dirty)
            if blocking:
                raise _CheckoutDirty(blocking)
        landing_sha, landing = await self._landing_for(request, base_sha, branch_sha)
        return _MergeCandidate(
            base_sha=base_sha,
            branch_sha=branch_sha,
            landing_sha=landing_sha,
            base_checked_out=base_checked_out,
            landing=landing,
        )


class SquashMerge(LocalMerge):
    """Land a rebased branch as one commit on its base, sharing every local-merge guard.

    The commit carries the branch's complete tree on top of the current base and trailers naming
    the task and the branch tip it squashed, which is how an interrupted integration is
    recognised afterwards: the original branch is no longer an ancestor of the base.
    """

    async def applied(self, repo: RepoState, task_root: Path) -> Landing | None:
        """Return the squash commit already on the base for the branch's current tip, if any."""
        branch_sha = await self._git.rev_parse(task_root, repo.branch)
        origin = await self._git.merge_base(self._project_root, repo.base, branch_sha)
        landed = await self._git.find_commit(
            self._project_root, f"{origin}..{repo.base}", _tip_trailer(branch_sha)
        )
        return await self._landing_of(landed) if landed is not None else None

    _integration = SQUASH_INTEGRATION

    async def _is_empty(self, project_root: Path, base_sha: str, branch_sha: str) -> bool:
        base_tree = await self._git.rev_parse(project_root, f"{base_sha}^{{tree}}")
        return base_tree == await self._git.rev_parse(project_root, f"{branch_sha}^{{tree}}")

    async def _landing_for(
        self, request: MergeRequest, base_sha: str, branch_sha: str
    ) -> tuple[str, Landing]:
        squash_sha = await self._git.commit_tree(
            self._project_root, branch_sha, base_sha, _squash_message(request, branch_sha)
        )
        return squash_sha, await self._landing_of(squash_sha, parent=base_sha)

    async def _landing_of(self, squash_sha: str, *, parent: str | None = None) -> Landing:
        before = parent if parent is not None else f"{squash_sha}^"
        commits = await self._git.log_oneline(self._project_root, f"{before}..{squash_sha}")
        changes = await _landed_changes(self._git, self._project_root, before, squash_sha)
        return Landing(tuple(commits), integration=SQUASH_INTEGRATION, changes=changes)


async def _landed_changes(git: Git, repo_dir: Path, before: str, after: str) -> LandedChanges:
    stats = await git.committed_line_changes(repo_dir, before, after)
    return LandedChanges(stats.changed_paths, stats.added_lines, stats.deleted_lines)


def _tip_trailer(branch_sha: str) -> str:
    return f"{SQUASH_TIP_TRAILER}: {branch_sha}"


def _squash_message(request: MergeRequest, branch_sha: str) -> str:
    subject = f"{request.task_id}: {' '.join(request.title.split())}"
    trailers = f"{SQUASH_TASK_TRAILER}: {request.task_id}\n{_tip_trailer(branch_sha)}"
    body = request.summary.strip()
    return "\n\n".join(part for part in (subject, body, trailers) if part)


def _blocking_paths(changed: set[str], dirty: set[str]) -> tuple[str, ...]:
    return tuple(
        sorted(right for right in dirty if any(_overlaps(left, right) for left in changed))
    )


def _overlaps(left: str, right: str) -> bool:
    left, right = left.rstrip("/"), right.rstrip("/")
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


class PullRequest:
    """Push a rebased branch, open a pull request for it and poll it until it lands."""

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
        faults: FaultReporter,
        settings: OrchestrationConfig,
    ) -> None:
        """Push to `remote` from `project_root`; record the PR in `events`, waits in `tasks`.

        A merged PR's base is fetched and verified through `base_refs` before the task is done.
        An open PR that stays unmerged for `timeouts.pr_wait` seconds escalates. A failed poll is
        recorded through `faults` and retried `network.poll_attempts` times on the
        `faults` backoff before it escalates.
        """
        self._git = git
        self._github = github
        self._clock = clock
        self._tasks = tasks
        self._events = events
        self._project_root = project_root
        self._remote = remote
        self._base_refs = base_refs
        self._faults = faults
        self._settings = settings

    def started(self, task_id: str) -> Landing | None:
        """Return what `task_id`'s open pull request holds, if it has one."""
        opened = self._open_pull_request(task_id)
        return opened.landing if opened is not None else None

    async def applied(self, repo: RepoState, task_root: Path) -> Landing | None:
        """Return `None`: only a pull request merged on the remote lands, and `settle` waits."""
        del repo, task_root

    async def merge(self, request: MergeRequest) -> Landing:
        """Push the branch and open its pull request, recorded as `pr_opened`.

        An open pull request of the branch that was never recorded (the engine died between
        opening and recording it) is adopted instead of opening another.
        """
        repo = request.repo
        commits = tuple(
            await self._git.log_oneline(request.task_root, f"{repo.base_ref}..{repo.branch}")
        )
        base_sha = await self._git.rev_parse(request.task_root, repo.base_ref)
        branch_sha = await self._git.rev_parse(request.task_root, repo.branch)
        changes = await _landed_changes(self._git, request.task_root, base_sha, branch_sha)
        if not commits:
            return Landing(integration=PULL_REQUEST_INTEGRATION, changes=changes)
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
            data={
                "url": url,
                "branch": repo.branch,
                "commits": list(commits),
                DONE_CHANGES_KEY: changes.as_data(),
            },
        )
        return Landing(commits, integration=PULL_REQUEST_INTEGRATION, changes=changes)

    async def settle(self, task_id: str, repo: RepoState, held: Callable[[], bool]) -> None:
        """Poll the open pull request until it is merged or closed, then verify the merged base.

        Raises `PullRequestClosed` for a closed one, `PullRequestConflicting` when the open PR
        conflicts with its base, `IntegrationBlocked` when the merged commit cannot be fetched
        from and verified on the remote base, when the open PR has failing, settled checks, or
        when it stays unmerged for `pr_wait` seconds (counted from the start of each wait), and
        `IntegrationHeld` before any poll once `held()` is true.
        """
        opened = self._open_pull_request(task_id)
        if opened is None:
            return
        self._tasks.set_slot(task_id, None)
        self._tasks.set_wait_reason(task_id, WaitReason.PR_OPEN)
        deadline = self._clock.now() + timedelta(seconds=self._settings.timeouts.pr_wait)
        while True:
            if held():
                raise IntegrationHeld
            status = await self._status(task_id, opened.url, held)
            if status.state is not PullRequestState.OPEN:
                break
            if status.conflicting:
                self._tasks.set_wait_reason(task_id, None)
                raise PullRequestConflicting(opened.url)
            stalled = _stalled(status, expired=self._clock.now() >= deadline)
            if stalled is not None:
                self._tasks.set_wait_reason(task_id, None)
                raise PullRequestStalled(opened.url, stalled)
            await self._clock.sleep(self._settings.network.pr_poll_seconds)
        self._tasks.set_wait_reason(task_id, None)
        if status.state is PullRequestState.CLOSED:
            raise PullRequestClosed(opened.url)
        try:
            await self._base_refs.sync(repo.base, status.merge_commit)
        except (BaseFetchFailed, MergedBaseUnavailable) as error:
            raise IntegrationBlocked(str(error)) from error

    async def _status(self, task_id: str, url: str, held: Callable[[], bool]) -> PullRequestStatus:
        attempts = self._settings.network.poll_attempts
        backoff = self._settings.faults
        delay = 0.0
        attempt = 0
        while True:
            attempt += 1
            try:
                status = await self._github.pull_request_status(self._project_root, url)
            except (CommandFailed, CommandTimeoutError) as error:
                self._faults.report(error, scope=POLL_FAULT_SCOPE, task_id=task_id)
                if attempt >= attempts:
                    raise
                delay = next_backoff(
                    delay,
                    initial=backoff.backoff_initial_seconds,
                    maximum=backoff.backoff_max_seconds,
                )
                await self._clock.sleep(delay)
                if held():
                    raise IntegrationHeld from None
            else:
                self._faults.resolve(POLL_FAULT_SCOPE, task_id=task_id)
                return status

    def _open_pull_request(self, task_id: str) -> _OpenPullRequest | None:
        return _open_pull_request(self._events, task_id, _INTEGRATION_RESTARTED_BY)


def _stalled(status: PullRequestStatus, *, expired: bool) -> str | None:
    if status.checks_failing and not status.checks_pending:
        return "failing checks"
    return "unmerged beyond the wait budget" if expired else None


def _open_pull_request(
    events: EventLog, task_id: str, ended_by: frozenset[EscalationReason]
) -> _OpenPullRequest | None:
    opened: _OpenPullRequest | None = None
    kinds = [EventKind.PR_OPENED, EventKind.PR_WITHDRAWN, EventKind.ESCALATION]
    for event in events.since(0, kinds=kinds, task_id=task_id):
        if event.kind is EventKind.PR_OPENED:
            found = landed_changes(event.data)
            opened = _OpenPullRequest(
                event.data["url"],
                Landing(
                    tuple(event.data["commits"]),
                    integration=PULL_REQUEST_INTEGRATION if found is not None else None,
                    changes=LandedChanges(*found) if found is not None else None,
                ),
            )
        elif event.kind is EventKind.PR_WITHDRAWN or event.data["reason"] in ended_by:
            opened = None
    return opened


class PullRequestCloser:
    """Close a task's open pull request when the task is cancelled."""

    def __init__(self, *, github: GitHub, events: EventLog, project_root: Path) -> None:
        """Close through `github` from `project_root` the PR that `events` record as open."""
        self._github = github
        self._events = events
        self._project_root = project_root

    def has_open(self, task_id: str) -> bool:
        """Return whether `task_id` has a pull request that was neither closed nor withdrawn."""
        return _open_pull_request(self._events, task_id, _PR_ENDED_BY) is not None

    async def close_open(self, task_id: str) -> None:
        """Close `task_id`'s open pull request, if it has one that is still open.

        Records `pr_withdrawn` once the pull request is closed or was found not open, so the
        close is never repeated.
        """
        opened = _open_pull_request(self._events, task_id, _PR_ENDED_BY)
        if opened is None:
            return
        state = (await self._github.pull_request_status(self._project_root, opened.url)).state
        if state is PullRequestState.OPEN:
            await self._github.close_pull_request(self._project_root, opened.url)
            state = PullRequestState.CLOSED
        self._events.append(
            EventKind.PR_WITHDRAWN,
            task_id=task_id,
            message=f"{task_id} withdrew {opened.url}",
            data={"url": opened.url, "state": state.value},
        )


class Integrator:
    """Own the project integration lock, rebase protocol and terminal cleanup."""

    def __init__(
        self,
        *,
        git: Git,
        strategy: IntegrationStrategy,
        base_refs: BaseRefs,
        lock: asyncio.Lock,
        project_root: Path,
        tasks: TaskStore,
        events: EventLog,
        finish_task: TaskFinisher,
        publication: BasePublication | None = None,
        atomically: Callable[[], AbstractContextManager[object]] = nullcontext,
    ) -> None:
        """Inject every state, cleanup and repository boundary required by integration.

        The rebase starts from the ref `base_refs` resolves, so a remote base is fetched afresh.
        A completed integration registers its base with `publication` inside the same
        `atomically` transaction that records the task as done, so a crash between the Git
        update and that record is repaired by the integration running again.
        """
        self._git = git
        self._strategy = strategy
        self._base_refs = base_refs
        self._lock = lock
        self._project_root = project_root
        self._tasks = tasks
        self._events = events
        self._finish_task = finish_task
        self._publication = publication
        self._atomically = atomically

    async def run(
        self,
        task: Task,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
        conflict_recovery: ConflictRecoveryLoader | None = None,
    ) -> IntegrationSummary:
        """Rebase and integrate `task`, delegating conflict turns to TaskFlow.

        An integration the strategy already started (an open PR) is only settled, never
        rebased or applied again.
        """
        repo = task.repo
        if repo is None:
            message = f"{task.id} has no task root to integrate"
            raise InvariantError(message)
        current, current_metadata = task, metadata
        started = self._strategy.started(task.id)
        if started is None:
            current, current_metadata, landing = await self._apply(
                task, repo, resolve_conflicts, metadata, conflict_recovery
            )
        else:
            landing = started
        await self._strategy.settle(current.id, repo, lambda: self._is_held(current.id))
        summary = IntegrationSummary(
            summary=current_metadata.summary,
            commits=landing.commits,
            residual_risks=current_metadata.residual_risks,
        )
        self._tasks.set_summary(current.id, summary.summary)
        updated = self._tasks.get(current.id)
        if updated is None:
            message = f"{current.id} vanished before integration could finish"
            raise InvariantError(message)
        with self._atomically():
            self._tasks.finish(
                current.id,
                _done_transition(updated),
                EventKind.TASK_DONE,
                message=summary.summary,
                data={
                    "summary": summary.summary,
                    **landing.done_data(),
                    "residual_risks": list(summary.residual_risks),
                },
            )
            if self._publication is not None:
                self._publication.register(repo.base)
        done = self._tasks.get(current.id)
        if done is None:
            message = f"{current.id} vanished after integration completed"
            raise InvariantError(message)
        await self._finish_task(done)
        return summary

    async def _apply(
        self,
        task: Task,
        repo: RepoState,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
        conflict_recovery: ConflictRecoveryLoader | None,
    ) -> tuple[Task, IntegrationMetadata, Landing]:
        async with self._lock:
            self._require_not_held(task.id)
            task_dir = task_root(self._project_root, task.id)
            if not await self._git.is_rebase_in_progress(task_dir):
                landed = await self._strategy.applied(repo, task_dir)
                if landed is not None:
                    return task, metadata, landed
            recovery_state = (
                conflict_recovery(task) if conflict_recovery is not None else ConflictRecovery()
            )
            current = task
            current_metadata = metadata
            in_progress = await self._git.is_rebase_in_progress(task_dir)
            if not in_progress and not recovery_state.resumed_report:
                self._events.append(
                    EventKind.INTEGRATION_STARTED,
                    task_id=task.id,
                    message=f"{task.id} integration started",
                    data={"branch": repo.branch, "base": repo.base},
                )
                recovery_state = ConflictRecovery(force_new_turn=recovery_state.force_new_turn)
            await self._require_integrable(current, repo, task_dir)
            base_ref = await self._resolve_base(repo)
            base_sha = await self._git.rev_parse(task_dir, base_ref)
            branch_sha = await self._git.rev_parse(task_dir, repo.branch)
            recovery = None
            if in_progress:
                recovery = await self._try_rebase(
                    task_dir,
                    base_ref,
                    current,
                    recovery_state,
                )
            elif recovery_state.resumed_report and (
                base_sha == branch_sha
                or await self._git.merge_base_is_ancestor(task_dir, base_ref, repo.branch)
            ):
                recovery = recovery_state
            elif base_sha != branch_sha:
                recovery = await self._try_rebase(task_dir, base_ref, current, recovery_state)
                if recovery is None and recovery_state.resumed_report:
                    recovery = recovery_state
            if recovery is not None:
                current, current_metadata = await resolve_conflicts(
                    current,
                    lambda: self._continue_rebase(current.id, task_dir),
                    recovery,
                )
                await self._continue_rebase(current.id, task_dir)
            await self._require_integrable(current, repo, task_dir)
            self._require_not_held(current.id)
            landing = await self._strategy.merge(
                MergeRequest(
                    task_id=current.id,
                    title=current.title,
                    repo=repo,
                    task_root=task_dir,
                    summary=current_metadata.summary,
                    held=lambda: self._is_held(current.id),
                )
            )
            return current, current_metadata, landing

    async def _resolve_base(self, repo: RepoState) -> str:
        try:
            return await self._base_refs.resolve(repo.base)
        except BaseFetchFailed as error:
            raise IntegrationBlocked(str(error)) from error

    def _is_held(self, task_id: str) -> bool:
        current = self._tasks.get(task_id)
        if current is None:
            message = f"{task_id} vanished during integration"
            raise InvariantError(message)
        return current.held

    def _require_not_held(self, task_id: str) -> None:
        if self._is_held(task_id):
            raise IntegrationHeld

    async def _require_integrable(self, task: Task, repo: RepoState, task_dir: Path) -> None:
        if await self._git.is_rebase_in_progress(task_dir):
            return
        head_branch = await self._git.head_branch(task_dir)
        if head_branch != repo.branch:
            commits = await self._git.log_oneline(task_dir, f"{repo.base_ref}..HEAD")
            raise OffTaskBranch(task.id, repo.branch, head_branch, commits)
        status = await self._git.status_porcelain(task_dir)
        if status:
            raise UncommittedWork(task.id, status)

    async def continue_rebase(self, task: Task) -> None:
        """Continue `task`'s rebase after its conflicts were resolved, if one is in progress."""
        await self._continue_rebase(task.id, task_root(self._project_root, task.id))

    async def _try_rebase(
        self,
        task_dir: Path,
        base: str,
        task: Task,
        recovery_state: ConflictRecovery,
    ) -> ConflictRecovery | None:
        if await self._git.is_rebase_in_progress(task_dir):
            paths = await self._git.unmerged_paths(task_dir)
            if paths:
                return dataclasses.replace(recovery_state, resumed_report=False)
            if recovery_state.resumed_report:
                return recovery_state
            await self._git.rebase_abort(task_dir)
        try:
            await self._git.rebase(task_dir, base)
        except CommandTimeoutError, asyncio.CancelledError:
            await self._abort_interrupted_rebase(task_dir)
            raise
        except CommandFailed as error:
            paths = await self._git.unmerged_paths(task_dir)
            if paths:
                return dataclasses.replace(recovery_state, resumed_report=False)
            raise IntegrationBlocked(_git_failure_message(task.id, "rebase", error)) from error
        return None

    async def _abort_interrupted_rebase(self, task_dir: Path) -> None:
        abort = asyncio.create_task(self._abort_rebase(task_dir))
        try:
            await asyncio.shield(abort)
        except asyncio.CancelledError:
            await abort
            raise

    async def _abort_rebase(self, task_dir: Path) -> None:
        if await self._git.is_rebase_in_progress(task_dir):
            await self._git.rebase_abort(task_dir)

    async def _continue_rebase(self, task_id: str, task_dir: Path) -> None:
        if not await self._git.is_rebase_in_progress(task_dir):
            return
        paths = await self._git.unmerged_paths(task_dir)
        if paths:
            raise RebaseConflict(paths)
        self._require_not_held(task_id)
        try:
            await self._git.rebase_continue(task_dir)
        except CommandFailed as error:
            paths = await self._git.unmerged_paths(task_dir)
            if paths:
                raise RebaseConflict(paths) from error
            raise IntegrationBlocked(
                _git_failure_message(task_id, "rebase --continue", error)
            ) from error


def _git_failure_message(task_id: str, command: str, error: CommandFailed) -> str:
    detail = error.stderr.strip()
    if len(detail) > GIT_FAILURE_DETAIL_LIMIT:
        detail = f"{detail[:GIT_FAILURE_DETAIL_LIMIT]}{GIT_FAILURE_TRUNCATION}"
    if not detail:
        detail = "Git returned no error details."
    return f"{task_id}: git {command} failed with exit {error.returncode}: {detail}"


def _done_transition(task: Task) -> Transition:
    return Transition.stay(task, step=None, status=Status.DONE)
