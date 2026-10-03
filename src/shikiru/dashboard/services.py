"""Real `WidgetServices` implementations, wired by `bootstrap` (docs/design/06-operator.md §5.4).

`GitRepoStatusService` is the `header` widget's only source of git data and caches it for its
configured interval so a dashboard refresh never spawns a git process on every tick.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from shikiru.dashboard.contract import (
    ContainerView,
    DiagnosticSampleState,
    DockerView,
    RepoStatus,
    ResourceView,
)
from shikiru.domain.tasks import Role, Status
from shikiru.errors import ShikiruError
from shikiru.tmux.session import HOME_WINDOW_NAME
from shikiru.ui.age import format_age

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import RepoConfig
    from shikiru.operator.queries import OperatorQueries
    from shikiru.telemetry.docker import DockerSampler
    from shikiru.telemetry.resources import ResourceSampler, ResourceSnapshot
    from shikiru.tmux.client import Tmux
    from shikiru.workspace.git import Git

_PANE_RESOURCE_FORMAT = "#{pane_id} #{pane_pid}"
TASK_MISSING = "task is missing"
TASK_WINDOW_UNRECORDED = "task window is not recorded"
TASK_WINDOW_MISSING = "task window is unavailable"
TASK_ROLE_PANE_UNRECORDED = "role pane is not recorded"
TASK_ROLE_PANE_MISSING = "role pane is unavailable"
TASK_ROLE_PANE_WINDOW_MISMATCH = "role pane is outside the recorded task window"
HOME_WINDOW_MISSING = "home window is unavailable"
TERMINAL_TASK_TARGET = "task is terminal; its workflow window is unavailable"
TASK_TARGET_OPENED = "opened existing task window"
ROLE_TARGET_OPENED = "opened existing role pane"
HOME_TARGET_OPENED = "opened home workspace"


@dataclass(frozen=True)
class _CachedRepoStatus:
    fetched_at: datetime
    status: RepoStatus


class GitRepoStatusService:
    """Report `header`'s repo row, refreshing at most once per configured interval (06 §5.4)."""

    def __init__(
        self,
        git: Git,
        repo: RepoConfig,
        project_dir: Path,
        clock: Clock,
        *,
        cache_ttl_seconds: float,
    ) -> None:
        """Read `project_dir`'s checkout, configured by `repo`, through `git` when asked."""
        self._git = git
        self._repo = repo
        self._project_dir = project_dir
        self._clock = clock
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: _CachedRepoStatus | None = None

    async def repo_status(self) -> RepoStatus:
        """Return the checkout's branch, ahead/behind `base` and dirty count."""
        now = self._clock.now()
        if self._cache is not None:
            age = (now - self._cache.fetched_at).total_seconds()
            if age < self._cache_ttl_seconds:
                return replace(self._cache.status, sampled_ago=format_age(age))
        status = await self._status()
        self._cache = _CachedRepoStatus(fetched_at=now, status=status)
        return replace(status, sampled_ago=format_age(0))

    async def _status(self) -> RepoStatus:
        repo = self._repo
        repo_dir = self._project_dir
        branch = await self._git.current_branch(repo_dir)
        dirty = len(await self._git.status_porcelain(repo_dir))
        ahead, behind = 0, 0
        if repo.base is not None and repo.base != branch:
            ahead = len(await self._git.log_oneline(repo_dir, f"{repo.base}..{branch}"))
            behind = len(await self._git.log_oneline(repo_dir, f"{branch}..{repo.base}"))
        return RepoStatus(branch=branch, ahead=ahead, behind=behind, dirty=dirty)


class DashboardServices:
    """The `WidgetServices` bootstrap wires into every `dash` process (06 §5.4)."""

    def __init__(
        self,
        repo_status_service: GitRepoStatusService,
        *,
        clock: Clock,
        resources_refresh_seconds: float,
        engine_up: Callable[[], bool],
        engine_pid: Callable[[], int | None],
        tmux: Tmux,
        session_target: str,
        resources: ResourceSampler,
        docker: DockerSampler,
    ) -> None:
        """Delegate source reads and sampling through injected process boundaries."""
        self._repo_status_service = repo_status_service
        self._clock = clock
        self._resources_refresh_seconds = resources_refresh_seconds
        self._engine_up = engine_up
        self._engine_pid = engine_pid
        self._tmux = tmux
        self._session_target = session_target
        self._resources = resources
        self._docker = docker

    async def repo_status(self) -> RepoStatus:
        """See `GitRepoStatusService.repo_status`."""
        return await self._repo_status_service.repo_status()

    def engine_up(self) -> bool:
        """Return whether the engine holds its liveness lock."""
        return self._engine_up()

    async def resources(self) -> ResourceView:
        """Sample host usage and process trees for the engine and all project panes."""
        try:
            pane_pids: list[int] = []
            if await self._tmux.target_exists(self._session_target):
                pane_pids = [
                    _pane_pid(line)
                    for line in await self._tmux.list_session_panes(
                        self._session_target, _PANE_RESOURCE_FORMAT
                    )
                ]
            snapshot = self._resources.sample_process_trees(self._engine_pid(), pane_pids)
        except (OSError, RuntimeError, ShikiruError, ValueError) as error:
            latest = self._resources.latest()
            state = (
                DiagnosticSampleState.STALE
                if latest.sampled_at is not None
                else DiagnosticSampleState.FAILED
            )
            return _resource_view(latest, self._clock, state=state, error=str(error))
        return _resource_view(snapshot, self._clock, state=DiagnosticSampleState.AVAILABLE)

    def resource_sample(self) -> ResourceView | None:
        """Return the latest dashboard resource reading without sampling again."""
        snapshot = self._resources.latest()
        if snapshot.sampled_at is None:
            return None
        age = (self._clock.now() - snapshot.sampled_at).total_seconds()
        state = (
            DiagnosticSampleState.STALE
            if age >= self._resources_refresh_seconds
            else DiagnosticSampleState.AVAILABLE
        )
        return _resource_view(snapshot, self._clock, state=state)

    async def containers(self) -> DockerView:
        """Return Docker stats in the stable dashboard contract shape."""
        snapshot = await self._docker.sample_if_due()
        if snapshot.error is not None:
            state = (
                DiagnosticSampleState.STALE
                if snapshot.sampled_at is not None
                else DiagnosticSampleState.FAILED
                if snapshot.failed
                else DiagnosticSampleState.UNAVAILABLE
            )
        elif not snapshot.available:
            state = DiagnosticSampleState.UNAVAILABLE
        elif not snapshot.containers:
            state = DiagnosticSampleState.EMPTY
        else:
            state = DiagnosticSampleState.AVAILABLE
        return DockerView(
            available=snapshot.available,
            state=state,
            sampled_ago=_sampled_ago(snapshot.sampled_at, self._clock),
            error=snapshot.error,
            containers=tuple(
                ContainerView(
                    name=container.name,
                    cpu_pct=container.cpu_pct,
                    memory=container.memory,
                )
                for container in snapshot.containers
            ),
        )


class TaskNavigation:
    """Select recorded task, role-pane or home targets without creating resources."""

    def __init__(
        self,
        tmux: Tmux,
        queries: OperatorQueries,
        session_target: str,
    ) -> None:
        """Use the existing tmux client, current task queries and home session target."""
        self._tmux = tmux
        self._queries = queries
        self._session_target = session_target

    async def task_window(self, task_id: str) -> str:
        """Select the task's current window or return a readable availability result."""
        task = self._queries.get_task_state(task_id)
        if task is None:
            return f"{task_id}: {TASK_MISSING}"
        if task.status in (Status.DONE, Status.CANCELLED):
            return f"{task_id}: {TERMINAL_TASK_TARGET}"
        if task.window_id is None:
            return f"{task_id}: {TASK_WINDOW_UNRECORDED}"
        if not await self._tmux.target_exists(task.window_id):
            return f"{task_id}: {TASK_WINDOW_MISSING}"
        try:
            await self._tmux.select_window(task.window_id)
        except ShikiruError as error:
            return f"{task_id}: {TASK_WINDOW_MISSING}: {error}"
        return f"{task_id}: {TASK_TARGET_OPENED}"

    async def role_pane(self, task_id: str, role: Role) -> str:
        """Select the task's current recorded role pane or explain why it is unavailable."""
        task = self._queries.get_task_state(task_id)
        if task is None:
            return f"{task_id}: {TASK_MISSING}"
        if task.status in (Status.DONE, Status.CANCELLED):
            return f"{task_id}: {TERMINAL_TASK_TARGET}"
        if task.window_id is None or not await self._tmux.target_exists(task.window_id):
            return f"{task_id}: {TASK_WINDOW_MISSING}"
        pane_id = task.panes.get(role)
        return await self._select_role_pane(task_id, role, pane_id, task.window_id)

    async def _select_role_pane(
        self,
        task_id: str,
        role: Role,
        pane_id: str | None,
        window_id: str,
    ) -> str:
        if pane_id is None or not await self._tmux.pane_exists(pane_id):
            reason = TASK_ROLE_PANE_UNRECORDED if pane_id is None else TASK_ROLE_PANE_MISSING
            return f"{task_id} {role.value}: {reason}"
        try:
            if await self._tmux.window_id(pane_id) != window_id:
                return f"{task_id} {role.value}: {TASK_ROLE_PANE_WINDOW_MISMATCH}"
            await self._tmux.select_pane(pane_id)
        except ShikiruError as error:
            return f"{task_id} {role.value}: {TASK_ROLE_PANE_MISSING}: {error}"
        return f"{task_id} {role.value}: {ROLE_TARGET_OPENED}"

    async def home(self) -> str:
        """Select the existing home window or report that it is unavailable."""
        target = f"{self._session_target}:{HOME_WINDOW_NAME}"
        if not await self._tmux.target_exists(target):
            return HOME_WINDOW_MISSING
        try:
            await self._tmux.select_window(target)
        except ShikiruError as error:
            return f"{HOME_WINDOW_MISSING}: {error}"
        return HOME_TARGET_OPENED


def _resource_view(
    snapshot: ResourceSnapshot,
    clock: Clock,
    *,
    state: DiagnosticSampleState,
    error: str | None = None,
) -> ResourceView:
    return ResourceView(
        host_cpu_pct=snapshot.host_cpu_pct,
        host_ram_used_pct=snapshot.host_ram_used_pct,
        free_ram_gb=snapshot.free_ram_gb,
        instance_cpu_pct=snapshot.instance_cpu_pct,
        instance_rss_gb=snapshot.instance_rss_gb,
        sampled_at=snapshot.sampled_at,
        state=state,
        sampled_ago=_sampled_ago(snapshot.sampled_at, clock),
        error=error,
    )


def _sampled_ago(sampled_at: datetime | None, clock: Clock) -> str | None:
    if sampled_at is None:
        return None
    age = (clock.now() - sampled_at).total_seconds()
    return format_age(age) if age >= 0 else None


def _pane_pid(line: str) -> int:
    _, separator, pid_text = line.partition(" ")
    if not separator:
        message = f"tmux pane resource row is invalid: {line!r}"
        raise ValueError(message)
    return int(pid_text)
