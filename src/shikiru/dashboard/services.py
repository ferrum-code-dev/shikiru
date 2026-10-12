"""Real `WidgetServices` implementations, wired by `bootstrap` (docs/design/06-operator.md §5.4).

`GitRepoStatusService` is the `header` widget's only source of git data and caches it for its
configured interval so a dashboard refresh never spawns a git process on every tick.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from shikiru.dashboard.contract import (
    ContainerProblemView,
    ContainerView,
    DiagnosticSampleState,
    DiskView,
    DockerSummaryView,
    DockerView,
    ElsewhereView,
    RepoStatus,
    ResourceView,
)
from shikiru.domain.runs import RunStatus
from shikiru.domain.tasks import Role, Status
from shikiru.engine.turn_processes import read_turn_pid, turn_pid_path
from shikiru.errors import ShikiruError
from shikiru.operator.lifecycle import task_dashboard_command
from shikiru.paths import run_dir
from shikiru.process import ProcessIdentity, identify_pid
from shikiru.telemetry.resources import ResourceProcessRoots
from shikiru.tmux.client import (
    ENV_VARIABLE_ASSIGNMENT_SEPARATOR,
    FORMAT_PANE_ID,
    FORMAT_PANE_PID,
    FORMAT_PANE_START_COMMAND,
    FORMAT_WINDOW_ID,
    HOME_PANE_OPTION,
    HOME_PANE_OPTION_VALUE,
    LAYOUT_PANE_OPTION,
    SYSTEM_LAYOUT_PANE,
    TMUX_ENV_COMMAND,
    TMUX_ENV_UNSET_OPTION,
    TMUX_ENV_UNSET_OPTION_ARGUMENT_COUNT,
)
from shikiru.tmux.session import home_window_target
from shikiru.ui.age import format_age, format_short_age

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import RepoConfig
    from shikiru.domain.docker import DockerSummary
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.reader import StateReader
    from shikiru.telemetry.disk import DiskSampler, DiskSnapshot
    from shikiru.telemetry.docker import DockerSampler
    from shikiru.telemetry.resources import ResourceSampler, ResourceSnapshot
    from shikiru.tmux.client import Tmux
    from shikiru.workspace.git import Git

_PANE_RESOURCE_FIELD_SEPARATOR = "\t"
_PANE_RESOURCE_FORMAT = _PANE_RESOURCE_FIELD_SEPARATOR.join(
    (
        FORMAT_PANE_ID,
        FORMAT_PANE_PID,
        FORMAT_WINDOW_ID,
        f"#{{{HOME_PANE_OPTION}}}",
        f"#{{{LAYOUT_PANE_OPTION}}}",
        FORMAT_PANE_START_COMMAND,
    )
)
_PANE_RESOURCE_FIELD_COUNT = 6
_PANE_RESOURCE_MAX_SPLITS = _PANE_RESOURCE_FIELD_COUNT - 1
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
    checked_at: datetime
    sampled_at: datetime | None
    status: RepoStatus


@dataclass(frozen=True)
class _PaneResource:
    pane_id: str
    pane_pid: int
    window_id: str
    home_marker: str
    layout_marker: str
    start_command: str


@dataclass(frozen=True)
class _PaneOwnership:
    project_root: Path
    pane_owners: dict[str, tuple[str, Role, str]]
    task_windows: dict[str, str]
    taken_over_runs: set[tuple[str, Role]]


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
        self._project_dir = project_dir.resolve()
        self._clock = clock
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: _CachedRepoStatus | None = None

    async def repo_status(self) -> RepoStatus:
        """Return the cached checkout identity, tracked line changes and observation state."""
        now = self._clock.now()
        if self._cache is not None:
            age = (now - self._cache.checked_at).total_seconds()
            if age < self._cache_ttl_seconds:
                return self._with_sample_age(self._cache)
        try:
            status = await self._status()
        except (OSError, ShikiruError) as error:
            if self._cache is None or self._cache.sampled_at is None:
                status = RepoStatus(
                    branch=None,
                    ahead=None,
                    behind=None,
                    dirty=None,
                    root=str(self._project_dir),
                    state=DiagnosticSampleState.FAILED,
                    error=str(error),
                )
                self._cache = _CachedRepoStatus(checked_at=now, sampled_at=None, status=status)
            else:
                status = replace(
                    self._cache.status,
                    state=DiagnosticSampleState.STALE,
                    error=str(error),
                )
                self._cache = _CachedRepoStatus(
                    checked_at=now,
                    sampled_at=self._cache.sampled_at,
                    status=status,
                )
            return self._with_sample_age(self._cache)
        self._cache = _CachedRepoStatus(checked_at=now, sampled_at=now, status=status)
        return self._with_sample_age(self._cache)

    async def _status(self) -> RepoStatus:
        repo = self._repo
        repo_dir = self._project_dir
        branch = await self._git.current_branch(repo_dir)
        line_changes = await self._git.working_tree_line_changes(repo_dir)
        dirty = len(await self._git.status_porcelain(repo_dir))
        ahead, behind = 0, 0
        if repo.base is not None and repo.base != branch:
            ahead = len(await self._git.log_oneline(repo_dir, f"{repo.base}..{branch}"))
            behind = len(await self._git.log_oneline(repo_dir, f"{branch}..{repo.base}"))
        return RepoStatus(
            branch=branch,
            ahead=ahead,
            behind=behind,
            dirty=dirty,
            root=str(repo_dir),
            added_lines=line_changes.added_lines,
            deleted_lines=line_changes.deleted_lines,
            binary_entries=line_changes.binary_entries,
            untracked_paths=line_changes.untracked_paths,
        )

    def _with_sample_age(self, cached: _CachedRepoStatus) -> RepoStatus:
        if cached.sampled_at is None:
            return cached.status
        age = (self._clock.now() - cached.sampled_at).total_seconds()
        return replace(cached.status, sampled_ago=format_age(age))


class DashboardServices:
    """The `WidgetServices` bootstrap wires into every `dash` process (06 §5.4)."""

    def __init__(
        self,
        repo_status_service: GitRepoStatusService,
        *,
        clock: Clock,
        resources_refresh_seconds: float,
        engine_up: Callable[[], bool],
        engine_identity: Callable[[], ProcessIdentity | None],
        project_root: Path,
        state: StateReader,
        tmux: Tmux,
        session_target: str,
        resources: ResourceSampler,
        docker: DockerSampler,
        disk: DiskSampler,
    ) -> None:
        """Delegate source reads and sampling through injected process boundaries."""
        self._repo_status_service = repo_status_service
        self._clock = clock
        self._resources_refresh_seconds = resources_refresh_seconds
        self._engine_up = engine_up
        self._engine_identity = engine_identity
        self._project_root = project_root
        self._state = state
        self._tmux = tmux
        self._session_target = session_target
        self._resources = resources
        self._docker = docker
        self._disk = disk
        self._resource_failure: tuple[datetime, str] | None = None

    async def repo_status(self) -> RepoStatus:
        """See `GitRepoStatusService.repo_status`."""
        return await self._repo_status_service.repo_status()

    def engine_up(self) -> bool:
        """Return whether the engine holds its liveness lock."""
        return self._engine_up()

    def engine_identity(self) -> ProcessIdentity | None:
        """Return the verified identity of the process holding the engine lock."""
        return self._engine_identity()

    async def resources(self) -> ResourceView:
        """Sample host usage and classified process trees belonging to this project."""
        try:
            previous = self._resources.latest()
            snapshot = previous
            if not self._resources.process_sample_is_fresh():
                pane_rows: list[str] = []
                if await self._tmux.target_exists(self._session_target):
                    pane_rows = await self._tmux.list_session_panes(
                        self._session_target, _PANE_RESOURCE_FORMAT
                    )
                roots = self._resource_roots(pane_rows)
                snapshot = self._resources.sample_process_roots(roots)
        except (OSError, RuntimeError, ShikiruError, ValueError) as error:
            failed_at = self._clock.now()
            self._resource_failure = (failed_at, str(error))
            latest = self._resources.latest()
            state = (
                DiagnosticSampleState.STALE
                if latest.sampled_at is not None
                else DiagnosticSampleState.FAILED
            )
            return _resource_view(latest, self._clock, state=state, error=str(error))
        if snapshot is not previous:
            self._resource_failure = None
        elif self._resource_failure is not None:
            latest = self._resources.latest()
            state = (
                DiagnosticSampleState.STALE
                if latest.sampled_at is not None
                else DiagnosticSampleState.FAILED
            )
            return _resource_view(
                latest,
                self._clock,
                state=state,
                error=self._resource_failure[1],
            )
        return _resource_view(snapshot, self._clock, state=DiagnosticSampleState.AVAILABLE)

    def _resource_roots(self, pane_rows: list[str]) -> ResourceProcessRoots:
        tasks = self._state.list_tasks()
        runs = self._state.list_all_runs()
        shikiru = _process_root_map((self._engine_identity(), identify_pid(os.getpid())))
        agents = _live_agent_process_roots(self._project_root, runs)
        pane_shikiru, pane_agents = _pane_process_roots(pane_rows, tasks, runs, self._project_root)
        shikiru.update(pane_shikiru)
        agents.update(pane_agents)
        return ResourceProcessRoots(tuple(shikiru.values()), tuple(agents.values()))

    def resource_sample(self) -> ResourceView | None:
        """Return the latest dashboard resource reading without sampling again."""
        snapshot = self._resources.latest()
        failure = self._resource_failure
        if failure is not None and (
            snapshot.sampled_at is None or failure[0] >= snapshot.sampled_at
        ):
            state = (
                DiagnosticSampleState.STALE
                if snapshot.sampled_at is not None
                else DiagnosticSampleState.FAILED
            )
            return _resource_view(snapshot, self._clock, state=state, error=failure[1])
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
        elif snapshot.summary is not None and snapshot.summary.is_empty:
            state = DiagnosticSampleState.EMPTY
        else:
            state = DiagnosticSampleState.AVAILABLE
        return DockerView(
            available=snapshot.available,
            state=state,
            sampled_ago=_sampled_ago(snapshot.sampled_at, self._clock),
            error=snapshot.error,
            summary=_docker_summary_view(snapshot.summary),
        )

    async def disk(self) -> DiskView:
        """Return the disk sample in the stable dashboard contract shape."""
        snapshot = await self._disk.sample_if_due()
        return DiskView(
            state=_disk_state(snapshot),
            free_bytes=snapshot.free_bytes,
            total_bytes=snapshot.total_bytes,
            runs_bytes=snapshot.runs_bytes,
            sampled_ago=_sampled_ago(snapshot.sampled_at, self._clock),
            error=snapshot.error,
        )


def _disk_state(snapshot: DiskSnapshot) -> DiagnosticSampleState:
    if snapshot.error is None:
        return DiagnosticSampleState.AVAILABLE
    if snapshot.sampled_at is not None:
        return DiagnosticSampleState.STALE
    return DiagnosticSampleState.FAILED if snapshot.failed else DiagnosticSampleState.UNAVAILABLE


def _docker_summary_view(summary: DockerSummary | None) -> DockerSummaryView | None:
    if summary is None:
        return None
    return DockerSummaryView(
        running=summary.running,
        total=summary.total,
        cpu_pct=summary.cpu_pct,
        memory_bytes=summary.memory_bytes,
        problems=tuple(
            ContainerProblemView(problem.name, problem.kind.value, problem.exit_code)
            for problem in summary.problems
        ),
        top=tuple(
            ContainerView(usage.name, usage.cpu_pct, usage.memory_bytes) for usage in summary.top
        ),
        elsewhere=ElsewhereView(
            summary.elsewhere.running, summary.elsewhere.cpu_pct, summary.elsewhere.memory_bytes
        ),
        usage_available=summary.usage_available,
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
        target = await home_window_target(self._tmux, self._session_target)
        if target is None:
            return HOME_WINDOW_MISSING
        try:
            await self._tmux.select_window(target[0])
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
        host_ram_used_bytes=snapshot.host_ram_used_bytes,
        host_ram_total_bytes=snapshot.host_ram_total_bytes,
        shikiru_cpu_pct=snapshot.shikiru_cpu_pct,
        shikiru_rss_gb=snapshot.shikiru_rss_gb,
        agents_cpu_pct=snapshot.agents_cpu_pct,
        agents_rss_gb=snapshot.agents_rss_gb,
        shikiru_process_tree_empty=snapshot.shikiru_process_tree_empty,
        agents_process_tree_empty=snapshot.agents_process_tree_empty,
    )


def _sampled_ago(sampled_at: datetime | None, clock: Clock) -> str | None:
    if sampled_at is None:
        return None
    age = clock.now() - sampled_at
    return format_short_age(age) if age.total_seconds() >= 0 else None


def _pane_resource(line: str) -> _PaneResource:
    fields = line.split(_PANE_RESOURCE_FIELD_SEPARATOR, _PANE_RESOURCE_MAX_SPLITS)
    if len(fields) != _PANE_RESOURCE_FIELD_COUNT:
        message = f"tmux pane resource row is invalid: {line!r}"
        raise ValueError(message)
    pane_id, pane_pid_text, window_id, home_marker, layout_marker, start_command = fields
    return _PaneResource(
        pane_id,
        int(pane_pid_text),
        window_id,
        home_marker,
        layout_marker,
        start_command,
    )


def _process_root_map(
    identities: Iterable[ProcessIdentity | None],
) -> dict[tuple[int, float | None], ProcessIdentity]:
    return {
        (identity.pid, identity.started_at): identity
        for identity in identities
        if identity is not None
    }


def _live_agent_process_roots(
    project_root: Path,
    runs: list[Run],
) -> dict[tuple[int, float | None], ProcessIdentity]:
    roots: dict[tuple[int, float | None], ProcessIdentity] = {}
    for run in runs:
        if run.status is not RunStatus.LIVE:
            continue
        path = turn_pid_path(run_dir(project_root, run.task_id), run.role)
        try:
            identity = read_turn_pid(path)
        except FileNotFoundError:
            continue
        roots[(identity.pid, identity.started_at)] = identity
    return roots


def _pane_process_roots(
    pane_rows: list[str],
    tasks: list[Task],
    runs: list[Run],
    project_root: Path,
) -> tuple[
    dict[tuple[int, float | None], ProcessIdentity],
    dict[tuple[int, float | None], ProcessIdentity],
]:
    taken_over_runs = {
        (run.task_id, run.role) for run in runs if run.status is RunStatus.TAKEN_OVER
    }
    pane_owners = {
        pane_id: (task.id, role, task.window_id)
        for task in tasks
        if task.window_id is not None
        for role, pane_id in task.panes.items()
    }
    task_windows = {task.window_id: task.id for task in tasks if task.window_id is not None}
    ownership = _PaneOwnership(project_root, pane_owners, task_windows, taken_over_runs)
    shikiru: dict[tuple[int, float | None], ProcessIdentity] = {}
    agents: dict[tuple[int, float | None], ProcessIdentity] = {}
    for line in pane_rows:
        _add_pane_process_root(line, ownership, shikiru, agents)
    return shikiru, agents


def _add_pane_process_root(
    line: str,
    ownership: _PaneOwnership,
    shikiru: dict[tuple[int, float | None], ProcessIdentity],
    agents: dict[tuple[int, float | None], ProcessIdentity],
) -> None:
    pane = _pane_resource(line)
    identity = identify_pid(pane.pane_pid)
    if identity is None:
        return
    identity_key = (identity.pid, identity.started_at)
    if pane.home_marker == HOME_PANE_OPTION_VALUE:
        shikiru[identity_key] = identity
    owner = ownership.pane_owners.get(pane.pane_id)
    if owner is None:
        owner_task_id = ownership.task_windows.get(pane.window_id)
        if (
            owner_task_id is not None
            and pane.layout_marker == SYSTEM_LAYOUT_PANE
            and _is_task_dashboard(pane.start_command, ownership.project_root, owner_task_id)
        ):
            shikiru[identity_key] = identity
        return
    owner_task_id, owner_role, owner_window_id = owner
    if owner_window_id == pane.window_id:
        target = agents if (owner_task_id, owner_role) in ownership.taken_over_runs else shikiru
        target[identity_key] = identity


def _is_task_dashboard(start_command: str, project_root: Path, task_id: str) -> bool:
    try:
        command = tuple(shlex.split(start_command))
    except ValueError:
        return False
    expected = task_dashboard_command(project_root, task_id)
    if command[:1] != (TMUX_ENV_COMMAND,):
        return command == expected
    index = 1
    while index < len(command):
        value = command[index]
        if value == TMUX_ENV_UNSET_OPTION:
            if index + 1 == len(command):
                return False
            index += TMUX_ENV_UNSET_OPTION_ARGUMENT_COUNT
            continue
        if ENV_VARIABLE_ASSIGNMENT_SEPARATOR not in value or value.startswith(
            TMUX_ENV_UNSET_OPTION[0]
        ):
            return command[index:] == expected
        index += 1
    return False
