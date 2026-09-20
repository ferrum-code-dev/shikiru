"""Integration strategy behaviour against real git repositories and state."""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

import pytest
from fake_agent.repl import FAKE_CHANGE_FILENAME

from shikiru.clock import SystemClock
from shikiru.config.settings import RepoConfig
from shikiru.domain.events import EventKind
from shikiru.domain.runs import RunStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationMetadata,
    IntegrationSummary,
    Integrator,
    LocalMerge,
)
from shikiru.errors import CommandFailed
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder
from tests.support.clock import FakeClock
from tests.support.engine import set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import commit_all, init_repo
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.domain.tasks import RepoState, Task
    from tests.support.engine import EngineEnv
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_RUNNER = SubprocessRunner()


async def _repo(path: Path, filename: str = "README.md") -> Path:
    await init_repo(_RUNNER, path)
    (path / filename).write_text("base\n")
    await commit_all(_RUNNER, path, "initial")
    return path


async def _task_with_worktree(
    connection: Connection,
    project_root: Path,
    *,
    title: str = "merge task",
    base: str = "main",
):
    task = seed_task(connection, SystemClock(), title=title, mode=Mode.IMPLEMENT)
    tasks = TaskStore(connection, SystemClock())
    transition = Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    tasks.apply_transition(task.id, transition)
    states = await TaskRootBuilder(Git(_RUNNER), project_root).build(
        task.id, task.title, [RepoConfig(path=".", base=base)]
    )
    tasks.set_repos(task.id, states)
    current = tasks.get(task.id)
    assert current is not None
    return current, states


async def test_local_merge_fast_forwards_base_and_reports_commits(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, states = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")

    result = await LocalMerge(Git(_RUNNER), SystemClock(), integration_wait=1).merge(
        project_root, task_root, states
    )

    assert (project_root / "README.md").read_text() == "task\n"
    assert result.commits
    assert await Git(_RUNNER).current_branch(project_root) == "main"


async def test_local_merge_updates_a_base_branch_that_is_not_checked_out(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    await _RUNNER.run(["git", "branch", "release"], cwd=project_root, timeout=10, check=True)
    task, states = await _task_with_worktree(connection, project_root, base="release")
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("release task\n")
    await commit_all(_RUNNER, task_root, "release change")

    result = await LocalMerge(Git(_RUNNER), SystemClock(), integration_wait=1).merge(
        project_root, task_root, states
    )

    assert result.commits
    assert await Git(_RUNNER).rev_parse(project_root, "release") == await Git(_RUNNER).rev_parse(
        task_root, states[0].branch
    )
    assert (project_root / "README.md").read_text() == "base\n"


async def test_local_merge_retries_dirty_overlap_until_checkout_is_clean(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, states = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    merge_task = asyncio.create_task(
        LocalMerge(Git(_RUNNER), clock, integration_wait=300).merge(project_root, task_root, states)
    )
    await wait_until(clock.has_pending_sleep, timeout=5)
    (project_root / "README.md").write_text("base\n")
    await _RUNNER.run(
        ["git", "restore", "--", "README.md"], cwd=project_root, timeout=10, check=True
    )
    clock.advance(120)

    result = await merge_task

    assert result.commits
    assert (project_root / "README.md").read_text() == "task\n"


async def test_local_merge_escalates_when_dirty_overlap_never_clears(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, states = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    integration_wait_seconds = 120
    merge_task = asyncio.create_task(
        LocalMerge(Git(_RUNNER), clock, integration_wait=integration_wait_seconds).merge(
            project_root, task_root, states
        )
    )

    await wait_until(clock.has_pending_sleep, timeout=5)
    clock.advance(integration_wait_seconds)

    with pytest.raises(IntegrationBlocked):
        await merge_task
    assert (project_root / "README.md").read_text() == "operator\n"
    assert await Git(_RUNNER).current_branch(project_root) == "main"


async def test_update_ref_rejects_a_stale_expected_sha_without_moving_the_branch(
    tmp_path: Path,
) -> None:
    project_root = await _repo(tmp_path / "project")
    git = Git(_RUNNER)
    await _RUNNER.run(["git", "branch", "release"], cwd=project_root, timeout=10, check=True)
    old_sha = await git.rev_parse(project_root, "release")
    (project_root / "README.md").write_text("new\n")
    await commit_all(_RUNNER, project_root, "advance main")
    new_sha = await git.rev_parse(project_root, "main")
    stale_expected_sha = "0" * 40

    with pytest.raises(CommandFailed):
        await git.update_ref(project_root, "release", new_sha, stale_expected_sha)

    assert await git.rev_parse(project_root, "release") == old_sha


async def test_local_merge_waits_for_a_dirty_directory_ancestor(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, states = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    task_directory = task_root / "nested"
    task_directory.mkdir()
    (task_directory / "task.txt").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task directory change")
    dirty_directory = project_root / "nested"
    await init_repo(_RUNNER, dirty_directory)
    clock = FakeClock(TASK_CREATED_AT)
    merge_task = asyncio.create_task(
        LocalMerge(Git(_RUNNER), clock, integration_wait=300).merge(project_root, task_root, states)
    )
    await wait_until(clock.has_pending_sleep, timeout=5)
    shutil.rmtree(dirty_directory)
    clock.advance(120)

    result = await merge_task

    assert result.commits
    assert (project_root / "nested" / "task.txt").read_text() == "task\n"


async def test_local_merge_checks_all_repositories_before_merging_any(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    repo_one = await _repo(project_root / "one")
    repo_two = await _repo(project_root / "two")
    task = seed_task(connection, SystemClock(), title="multi repo", mode=Mode.IMPLEMENT)
    tasks = TaskStore(connection, SystemClock())
    tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    configs = [
        RepoConfig(path="one", base="main"),
        RepoConfig(path="two", base="main"),
    ]
    states = await TaskRootBuilder(Git(_RUNNER), project_root).build(task.id, task.title, configs)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "one" / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root / "one", "task change")
    (repo_two / "README.md").write_text("base advanced\n")
    await commit_all(_RUNNER, repo_two, "advance base")

    with pytest.raises(IntegrationBlocked):
        await LocalMerge(Git(_RUNNER), SystemClock(), integration_wait=0).merge(
            project_root, task_root, states
        )

    assert (repo_one / "README.md").read_text() == "base\n"


async def test_integrator_rebases_conflict_then_finishes_terminal_state(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, states = await _task_with_worktree(connection, project_root, title="conflict task")
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    task_dir = task_root / "README.md"
    task_dir.write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("main\n")
    await commit_all(_RUNNER, project_root, "main change")
    events = EventLog(connection, SystemClock())
    tasks = TaskStore(connection, SystemClock())
    tasks.set_window(task.id, "window")
    task = tasks.get(task.id)
    assert task is not None
    order: list[str] = []
    builder = TaskRootBuilder(Git(_RUNNER), project_root)

    async def finalizer(task_value: Task, _summary: IntegrationSummary) -> None:
        order.append("sessions_exited")
        await builder.cleanup(task_value.id, states)
        order.append("worktrees_cleaned")

    async def close_window(task_value: Task) -> None:
        done = tasks.get(task_value.id)
        assert done is not None
        assert done.status is Status.DONE
        assert done.summary == "accepted summary"
        order.append("window_closed")

    async def resolve(
        task_value: Task,
        _repo: RepoState,
        continue_rebase: Callable[[], Awaitable[None]],
    ) -> Task:
        task_path = task_root / "README.md"
        task_path.write_text("resolved\n")
        await commit_all(_RUNNER, task_root, "resolve conflict")
        await continue_rebase()
        return task_value

    result = await Integrator(
        git=Git(_RUNNER),
        strategy=LocalMerge(Git(_RUNNER), SystemClock(), integration_wait=1),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finalizer=finalizer,
        close_window=close_window,
    ).run(task, resolve, IntegrationMetadata("accepted summary", ("residual risk",)))

    final = TaskStore(connection, SystemClock()).get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.summary == "accepted summary"
    assert result.commits
    assert result.residual_risks == ("residual risk",)
    assert order == ["sessions_exited", "worktrees_cleaned", "window_closed"]
    assert not task_root.exists()
    assert not await Git(_RUNNER).branch_exists(project_root, states[0].branch)
    assert (project_root / "README.md").read_text() == "resolved\n"
    done_events = events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert done_events
    assert done_events[-1].data == {
        "summary": "accepted summary",
        "commits": list(result.commits),
        "residual_risks": ["residual risk"],
    }


async def test_taskflow_fake_agent_resolves_conflict_and_repeats_changes_requested_verify(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    window_id = task.window_id
    assert window_id is not None
    (engine_env.project_root / FAKE_CHANGE_FILENAME).write_text("main\n")
    await commit_all(_RUNNER, engine_env.project_root, "main change")
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept"}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "accept"}, {"kind": "changes_requested"}, {"kind": "accept"}],
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.step is None
    assert final.summary is not None
    assert not (engine_env.project_root / ".shikiru" / "worktrees" / task.id).exists()
    assert not await Git(_RUNNER).branch_exists(engine_env.project_root, task.repos[0].branch)
    assert not await engine_env.tmux.target_exists(window_id)
    assert all(run.status is RunStatus.CLOSED for run in engine_env.runs.for_task(task.id))
    done_events = flow_env.events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert done_events
    assert done_events[-1].data["summary"] == final.summary


async def test_taskflow_conflict_round_limit_escalates_after_two_changes_requested_verdicts(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    (engine_env.project_root / FAKE_CHANGE_FILENAME).write_text("main\n")
    await commit_all(_RUNNER, engine_env.project_root, "main change")
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept"}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "accept"}, {"kind": "changes_requested"}, {"kind": "changes_requested"}],
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert final.step is Step.INTEGRATE
    assert final.counters.conflict_rounds == 2
    open_escalations = flow_env.escalations.list_open()
    assert open_escalations[-1].reason is EscalationReason.INTEGRATION_CONFLICT
