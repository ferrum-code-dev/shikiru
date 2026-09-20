"""Rebase, conflict hand-off and local merge orchestration (03-workflow.md §6)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, RepoState, Status, Task
from shikiru.errors import CommandFailed, ShikiruError
from shikiru.workspace.repo_paths import repo_dir
from shikiru.workspace.roots import task_root

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.store.events import EventLog
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.git import Git

INTEGRATION_RETRY = timedelta(minutes=2)
"""Delay between blocked integration pre-checks (05-data.md §1)."""


class IntegrationBlocked(ShikiruError):
    """The local base checkout stayed unavailable for the integration wait budget."""

    reason = EscalationReason.INTEGRATION_BLOCKED

    def __init__(self) -> None:
        """Build the integration-blocked error."""
        super().__init__("local integration remained blocked")


class IntegrationConflict(ShikiruError):
    """Conflict recovery could not produce an accepted post-rebase result."""

    reason = EscalationReason.INTEGRATION_CONFLICT

    def __init__(self) -> None:
        """Build the integration-conflict error."""
        super().__init__("integration conflicts could not be resolved")


@dataclass(frozen=True, slots=True)
class IntegrationSummary:
    """Observable result of a successful integration."""

    summary: str
    commits: tuple[str, ...] = ()
    residual_risks: tuple[str, ...] = ()


@dataclass(slots=True)
class IntegrationMetadata:
    """Accepted Leader context carried into the terminal task event."""

    summary: str
    residual_risks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _MergeCandidate:
    repo: RepoState
    project_dir: Path
    task_dir: Path
    base_sha: str
    branch_sha: str
    base_checked_out: bool
    commits: tuple[str, ...]


class _PrecheckFailed(Exception):
    pass


type ContinueRebase = Callable[[], Awaitable[None]]
type ConflictResolver = Callable[[Task, RepoState, ContinueRebase], Awaitable[Task]]
type Finalizer = Callable[[Task, IntegrationSummary], Awaitable[None]]
type WindowCloser = Callable[[Task], Awaitable[None]]


class IntegrationStrategy(Protocol):
    """A strategy that applies an already rebased task branch to its base."""

    async def merge(
        self, project_root: Path, task_root_path: Path, repos: Sequence[RepoState]
    ) -> IntegrationSummary:
        """Merge every repository that is ahead of its configured base."""
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


class LocalMerge:
    """Apply rebased branches locally using fast-forward or compare-and-swap refs."""

    def __init__(self, git: Git, clock: Clock, *, integration_wait: int) -> None:
        """Use `git` for repository operations and `clock` for retry deadlines."""
        self._git = git
        self._clock = clock
        self._integration_wait = integration_wait

    async def merge(
        self, project_root: Path, task_root_path: Path, repos: Sequence[RepoState]
    ) -> IntegrationSummary:
        """Pre-check every repo, then atomically apply every eligible local merge."""
        deadline = self._clock.now() + timedelta(seconds=self._integration_wait)
        while True:
            try:
                candidates = await self._precheck(project_root, task_root_path, repos)
                await self._apply(candidates)
            except _PrecheckFailed, CommandFailed:
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    raise IntegrationBlocked from None
                await self._clock.sleep(min(INTEGRATION_RETRY.total_seconds(), remaining))
                continue
            break
        commits = tuple(
            f"{candidate.repo.path}: {commit}"
            for candidate in candidates
            for commit in candidate.commits
        )
        return IntegrationSummary(
            summary=f"Merged {len(commits)} commit(s) across {len(candidates)} repo(s).",
            commits=commits,
        )

    async def _apply(self, candidates: Sequence[_MergeCandidate]) -> None:
        for candidate in candidates:
            if candidate.base_checked_out:
                await self._git.merge_ff_only(candidate.project_dir, candidate.repo.branch)
            else:
                await self._git.update_ref(
                    candidate.project_dir,
                    candidate.repo.base,
                    candidate.branch_sha,
                    candidate.base_sha,
                )

    async def _precheck(
        self, project_root: Path, task_root_path: Path, repos: Sequence[RepoState]
    ) -> list[_MergeCandidate]:
        candidates: list[_MergeCandidate] = []
        for repo in repos:
            project_dir = repo_dir(project_root, repo.path)
            task_dir = repo_dir(task_root_path, repo.path)
            base_sha = await self._git.rev_parse(project_dir, repo.base)
            branch_sha = await self._git.rev_parse(task_dir, repo.branch)
            if branch_sha == base_sha:
                continue
            if not await self._git.merge_base_is_ancestor(project_dir, repo.base, repo.branch):
                raise _PrecheckFailed
            base_checked_out = await self._git.current_branch(project_dir) == repo.base
            if base_checked_out:
                changed = set(
                    await self._git.diff_name_only(task_dir, f"{base_sha}...{branch_sha}")
                )
                dirty = await self._git.dirty_paths(project_dir)
                if _paths_overlap(changed, dirty):
                    raise _PrecheckFailed
            commits = tuple(await self._git.log_oneline(task_dir, f"{base_sha}..{branch_sha}"))
            candidates.append(
                _MergeCandidate(
                    repo=repo,
                    project_dir=project_dir,
                    task_dir=task_dir,
                    base_sha=base_sha,
                    branch_sha=branch_sha,
                    base_checked_out=base_checked_out,
                    commits=commits,
                )
            )
        return candidates


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
        finalizer: Finalizer,
        close_window: WindowCloser,
    ) -> None:
        """Inject every state, cleanup and repository boundary required by integration."""
        self._git = git
        self._strategy = strategy
        self._lock = lock
        self._project_root = project_root
        self._tasks = tasks
        self._events = events
        self._finalizer = finalizer
        self._close_window = close_window

    async def run(
        self,
        task: Task,
        resolve_conflicts: ConflictResolver,
        metadata: IntegrationMetadata,
    ) -> IntegrationSummary:
        """Rebase and integrate `task`, delegating conflict turns to TaskFlow."""
        async with self._lock:
            self._events.append(
                EventKind.INTEGRATION_STARTED,
                task_id=task.id,
                message=f"{task.id} integration started",
                data={"repos": [repo.path for repo in task.repos]},
            )
            current = task
            for repo in current.repos:
                task_dir = repo_dir(task_root(self._project_root, current.id), repo.path)
                base_sha = await self._git.rev_parse(task_dir, repo.base)
                branch_sha = await self._git.rev_parse(task_dir, repo.branch)
                if base_sha == branch_sha:
                    continue
                if await self._try_rebase(task_dir, repo.base):
                    await self._git.rebase_abort(task_dir)
                    await self._try_rebase(task_dir, repo.base)
                    current = await resolve_conflicts(
                        current, repo, lambda task_dir=task_dir: self._continue_rebase(task_dir)
                    )
                    await self._continue_rebase(task_dir)
            raw_summary = await self._strategy.merge(
                self._project_root, task_root(self._project_root, current.id), current.repos
            )
            summary = dataclasses.replace(
                raw_summary,
                summary=metadata.summary,
                residual_risks=metadata.residual_risks,
            )
            await self._finalizer(current, summary)
            self._tasks.set_summary(current.id, summary.summary)
            updated = self._tasks.get(current.id)
            if updated is None:
                message = f"{current.id} vanished before integration could finish"
                raise ShikiruError(message)
            self._tasks.apply_transition(current.id, _done_transition(updated))
            done = self._tasks.get(current.id)
            if done is None:
                message = f"{current.id} vanished after integration completed"
                raise ShikiruError(message)
            self._tasks.set_slot(done.id, None)
            await self._close_window(done)
            self._events.append(
                EventKind.TASK_DONE,
                task_id=current.id,
                message=summary.summary,
                data={
                    "summary": summary.summary,
                    "commits": list(summary.commits),
                    "residual_risks": list(summary.residual_risks),
                },
            )
            return summary

    async def _try_rebase(self, task_dir: Path, base: str) -> bool:
        try:
            await self._git.rebase(task_dir, base)
        except CommandFailed:
            if not await self._git.is_rebase_in_progress(task_dir):
                raise
            return True
        return False

    async def _continue_rebase(self, task_dir: Path) -> None:
        if not await self._git.is_rebase_in_progress(task_dir):
            return
        try:
            await self._git.rebase_continue(task_dir)
        except CommandFailed:
            if await self._git.is_rebase_in_progress(task_dir):
                raise IntegrationConflict from None
            raise


def _done_transition(task: Task) -> Transition:
    return Transition(
        step=None,
        status=Status.DONE,
        iteration=task.iteration,
        counters=task.counters,
        rung_index=dict(task.rung_index),
        overrides=dict(task.overrides),
    )
