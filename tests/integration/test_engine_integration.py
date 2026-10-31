"""Integration strategy behaviour against real git repositories and state."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_agent.scripted import FAKE_CHANGE_FILENAME

from shikiru.clock import SystemClock
from shikiru.config.settings import RepoConfig
from shikiru.domain.commands import Resolve, RetryResolution
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    ReportKind,
)
from shikiru.domain.runs import RunStatus
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step, Task
from shikiru.engine.handlers.base import HandlerContext
from shikiru.engine.handlers.escalations import ResolveHandler
from shikiru.engine.integration import (
    ConflictRecovery,
    IntegrationBlocked,
    IntegrationMetadata,
    IntegrationSummary,
    Integrator,
    LocalMerge,
    MergeRequest,
    OffTaskBranch,
    UncommittedWork,
)
from shikiru.engine.notifier import Notifier
from shikiru.errors import CommandFailed, CommandTimeoutError
from shikiru.paths import turns_dir
from shikiru.process import SubprocessRunner
from shikiru.store.database import transaction
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog, fetch_timeline_events
from shikiru.store.tasks import TaskStore
from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.engine import set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo
from tests.support.store import seed_task
from tests.support.waiting import wait_until
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.clock import Clock
    from shikiru.domain.tasks import RepoState, Task
    from tests.support.engine import EngineEnv
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_RUNNER = SubprocessRunner()
TIMELINE_EVENT_LIMIT = 40
_STALE_CONFLICT_TURN = "01-integrate-i0"


async def _repo(path: Path, filename: str = "README.md") -> Path:
    await init_repo(_RUNNER, path)
    (path / filename).write_text("base\n")
    await commit_all(_RUNNER, path, "initial")
    return path


def _record_completed_conflict_turn(
    project_root: Path, events: EventLog, task: Task, label: str
) -> None:
    turn_dir = turns_dir(project_root, task.id) / label
    turn_dir.mkdir(parents=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
    (turn_dir / REPORT_FILENAME).write_text(
        ImplementationReport(
            status="completed",
            summary="conflict resolution submitted",
            commits=[],
            tests=[],
            trackers_updated=True,
        ).model_dump_json()
    )
    for kind, message in (
        (EventKind.TURN_STARTED, "conflict turn started"),
        (EventKind.TURN_COMPLETED, "conflict turn completed"),
    ):
        events.append(
            kind,
            task_id=task.id,
            message=message,
            data={
                "turn": label,
                "step": Step.INTEGRATE.value,
                "role": Role.IMPLEMENTER.value,
                **({"outcome": "completed"} if kind is EventKind.TURN_COMPLETED else {}),
            },
        )


def _local_merge(
    project_root: Path, clock: Clock, integration_wait: int, events: EventLog
) -> LocalMerge:
    return LocalMerge(
        Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        clock,
        events=events,
        project_root=project_root,
        integration_wait=integration_wait,
    )


def _request(task: Task, state: RepoState, task_root: Path) -> MergeRequest:
    return MergeRequest(
        task_id=task.id,
        title=task.title,
        repo=state,
        task_root=task_root,
        summary=task.title,
        held=lambda: False,
    )


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
    state = await task_root_builder(_RUNNER, project_root, RepoConfig(base=base)).build(
        task.id, task.title
    )
    tasks.set_repo(task.id, state)
    current = tasks.get(task.id)
    assert current is not None
    return current, state


async def test_local_merge_fast_forwards_base_and_reports_commits(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")

    result = await _local_merge(
        project_root, SystemClock(), 1, EventLog(connection, SystemClock())
    ).merge(_request(task, state, task_root))

    assert (project_root / "README.md").read_text() == "task\n"
    assert result.commits
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).current_branch(project_root)
        == "main"
    )


async def test_local_merge_updates_a_base_branch_that_is_not_checked_out(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    await _RUNNER.run(["git", "branch", "release"], cwd=project_root, timeout=10, check=True)
    task, state = await _task_with_worktree(connection, project_root, base="release")
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("release task\n")
    await commit_all(_RUNNER, task_root, "release change")

    result = await _local_merge(
        project_root, SystemClock(), 1, EventLog(connection, SystemClock())
    ).merge(_request(task, state, task_root))

    assert result.commits
    assert await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        project_root, "release"
    ) == await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(task_root, state.branch)
    assert (project_root / "README.md").read_text() == "base\n"


async def test_local_merge_retries_dirty_overlap_until_checkout_is_clean(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    merge_task = asyncio.create_task(
        _local_merge(project_root, clock, 300, EventLog(connection, clock)).merge(
            _request(task, state, task_root)
        )
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


async def test_local_merge_records_one_notified_event_naming_the_blocking_paths(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    merge_task = asyncio.create_task(
        _local_merge(project_root, clock, 600, events).merge(_request(task, state, task_root))
    )

    for _ in range(3):
        await wait_until(clock.has_pending_sleep, timeout=5)
        clock.advance(120)
    await wait_until(clock.has_pending_sleep, timeout=5)

    waiting = events.since(0, kinds=[EventKind.INTEGRATION_WAITING])
    assert len(waiting) == 1
    assert waiting[0].task_id == task.id
    assert waiting[0].data["paths"] == ["README.md"]
    assert "README.md" in waiting[0].message
    assert Notifier.wants(waiting[0])
    (project_root / "README.md").write_text("base\n")
    await _RUNNER.run(
        ["git", "restore", "--", "README.md"], cwd=project_root, timeout=10, check=True
    )
    clock.advance(120)
    assert await merge_task
    assert len(events.since(0, kinds=[EventKind.INTEGRATION_WAITING])) == 1


async def test_local_merge_escalates_when_dirty_overlap_never_clears(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    integration_wait_seconds = 120
    merge_task = asyncio.create_task(
        _local_merge(
            project_root, clock, integration_wait_seconds, EventLog(connection, clock)
        ).merge(_request(task, state, task_root))
    )

    await wait_until(clock.has_pending_sleep, timeout=5)
    clock.advance(integration_wait_seconds)

    with pytest.raises(IntegrationBlocked):
        await merge_task
    assert (project_root / "README.md").read_text() == "operator\n"
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).current_branch(project_root)
        == "main"
    )


async def test_update_ref_rejects_a_stale_expected_sha_without_moving_the_branch(
    tmp_path: Path,
) -> None:
    project_root = await _repo(tmp_path / "project")
    git = Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)
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
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    task_directory = task_root / "nested"
    task_directory.mkdir()
    (task_directory / "task.txt").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task directory change")
    dirty_directory = project_root / "nested"
    await init_repo(_RUNNER, dirty_directory)
    clock = FakeClock(TASK_CREATED_AT)
    merge_task = asyncio.create_task(
        _local_merge(project_root, clock, 300, EventLog(connection, clock)).merge(
            _request(task, state, task_root)
        )
    )
    await wait_until(clock.has_pending_sleep, timeout=5)
    shutil.rmtree(dirty_directory)
    clock.advance(120)

    result = await merge_task

    assert result.commits
    assert (project_root / "nested" / "task.txt").read_text() == "task\n"


async def test_local_merge_blocks_when_base_moved_past_the_rebased_branch(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "task.txt").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("base advanced\n")
    await commit_all(_RUNNER, project_root, "advance base")
    base_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        project_root, "main"
    )

    with pytest.raises(IntegrationBlocked):
        await _local_merge(
            project_root, SystemClock(), 0, EventLog(connection, SystemClock())
        ).merge(_request(task, state, task_root))

    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(project_root, "main")
        == base_sha
    )


async def test_integrator_rebases_conflict_then_finishes_terminal_state(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root, title="conflict task")
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
    builder = task_root_builder(_RUNNER, project_root, RepoConfig())

    async def finish_task(task_value: Task) -> None:
        terminal = tasks.get(task_value.id)
        assert terminal is not None
        assert terminal.status is Status.DONE
        order.append("sessions_exited")
        await builder.cleanup(task_value.id, state)
        order.append("worktrees_cleaned")
        order.append("window_closed")

    async def resolve(
        task_value: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        recovery: ConflictRecovery,
    ) -> tuple[Task, IntegrationMetadata]:
        assert not recovery.resumed_report
        task_path = task_root / "README.md"
        task_path.write_text("resolved\n")
        await commit_all(_RUNNER, task_root, "resolve conflict")
        await continue_rebase()
        return task_value, IntegrationMetadata("accepted summary", ("residual risk",))

    result = await Integrator(
        git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        strategy=_local_merge(project_root, SystemClock(), 1, events),
        base_refs=LocalBaseRefs(),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finish_task=finish_task,
    ).run(task, resolve, IntegrationMetadata("stale summary"))

    final = TaskStore(connection, SystemClock()).get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.summary == "accepted summary"
    assert result.commits
    assert result.residual_risks == ("residual risk",)
    assert order == ["sessions_exited", "worktrees_cleaned", "window_closed"]
    assert not task_root.exists()
    assert not await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).branch_exists(
        project_root, state.branch
    )
    assert (project_root / "README.md").read_text() == "resolved\n"
    done_events = events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert done_events
    assert done_events[-1].data == {
        "summary": "accepted summary",
        "commits": list(result.commits),
        "residual_risks": ["residual risk"],
    }


def _integrator(connection: Connection, project_root: Path) -> Integrator:
    async def keep_worktree(task: Task) -> None:
        del task

    return Integrator(
        git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        strategy=_local_merge(project_root, SystemClock(), 0, EventLog(connection, SystemClock())),
        base_refs=LocalBaseRefs(),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=TaskStore(connection, SystemClock()),
        events=EventLog(connection, SystemClock()),
        finish_task=keep_worktree,
    )


async def _resolve_nothing(
    task: Task,
    continue_rebase: Callable[[], Awaitable[None]],
    recovery: ConflictRecovery,
) -> tuple[Task, IntegrationMetadata]:
    del continue_rebase, recovery
    return task, IntegrationMetadata("resolved")


async def _dirty_task_worktree(
    connection: Connection, project_root: Path, *, tracked: bool, ahead: bool
) -> tuple[Task, Path, Path]:
    task, _ = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    if ahead:
        (task_root / "committed.txt").write_text("committed\n")
        await commit_all(_RUNNER, task_root, "task change")
    output = task_root / ("README.md" if tracked else "plan-output.md")
    output.write_text("uncommitted\n")
    return task, task_root, output


@pytest.mark.parametrize("ahead", [False, True], ids=["no-op", "ahead"])
@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
async def test_integrator_refuses_uncommitted_work_and_keeps_the_worktree(
    connection: Connection, tmp_path: Path, *, tracked: bool, ahead: bool
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, task_root, output = await _dirty_task_worktree(
        connection, project_root, tracked=tracked, ahead=ahead
    )
    main_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        project_root, "main"
    )

    with pytest.raises(UncommittedWork) as refused:
        await _integrator(connection, project_root).run(
            task, _resolve_nothing, IntegrationMetadata("summary")
        )

    assert refused.value.reason is EscalationReason.UNCOMMITTED_WORK
    assert output.name in str(refused.value)
    final = TaskStore(connection, SystemClock()).get(task.id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    assert output.read_text() == "uncommitted\n"
    assert task_root.is_dir()
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(project_root, "main")
        == main_sha
    )


_OFF_BRANCH = "feature/agent-chose-this"
_LEAVE_BRANCH = {
    "switched_branch": ["switch", "-c", _OFF_BRANCH],
    "detached_head": ["switch", "--detach"],
}


@pytest.mark.parametrize("leave", _LEAVE_BRANCH.values(), ids=_LEAVE_BRANCH.keys())
async def test_integrator_refuses_a_worktree_that_left_its_task_branch_and_keeps_its_commits(
    connection: Connection, tmp_path: Path, leave: list[str]
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    await _RUNNER.run(["git", *leave], cwd=task_root, timeout=10, check=True)
    (task_root / "work.txt").write_text("real work\n")
    await commit_all(_RUNNER, task_root, "real work")
    work_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(task_root, "HEAD")
    main_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        project_root, "main"
    )

    with pytest.raises(OffTaskBranch) as refused:
        await _integrator(connection, project_root).run(
            task, _resolve_nothing, IntegrationMetadata("summary")
        )

    assert refused.value.reason is EscalationReason.OFF_TASK_BRANCH
    assert state.branch in str(refused.value)
    assert work_sha[:7] in str(refused.value)
    final = TaskStore(connection, SystemClock()).get(task.id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(task_root, "HEAD")
        == work_sha
    )
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(project_root, "main")
        == main_sha
    )


_OFF_BRANCH_FLOWS = {
    "switched_branch": "branch",
    "detached_head": "detach",
}


@pytest.mark.parametrize("leave", _OFF_BRANCH_FLOWS.values(), ids=_OFF_BRANCH_FLOWS.keys())
async def test_a_task_whose_agent_left_its_branch_never_reaches_done_and_keeps_its_work(
    engine_env: EngineEnv, tmux_server: TmuxServer, leave: str
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "leave_branch": leave}, {"kind": "accept"}, {"kind": "accept"}],
    )
    main_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        engine_env.project_root, "main"
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert not flow_env.events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    assert [event.data["decision"] for event in verdicts][:2] == ["accept", "changes_requested"]
    assert final.repo is not None
    assert final.repo.branch in verdicts[1].data["issues"][0]["description"]
    worktree = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    assert await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).log_oneline(
        worktree, "main..HEAD"
    )
    assert await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).branch_exists(
        engine_env.project_root, final.repo.branch
    )
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
            engine_env.project_root, "main"
        )
        == main_sha
    )


async def test_integrator_finishes_a_clean_no_op_task_as_done(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, _ = await _task_with_worktree(connection, project_root)

    result = await _integrator(connection, project_root).run(
        task, _resolve_nothing, IntegrationMetadata("nothing to change")
    )

    final = TaskStore(connection, SystemClock()).get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert result.commits == ()


async def test_a_plan_task_with_uncommitted_output_escalates_and_keeps_the_worktree(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.PLAN)
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "accept", "uncommitted": "plan-output.md"}]
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert final.step is Step.INTEGRATE
    reasons = [escalation.reason for escalation in fetch_open_escalations(flow_env.env.connection)]
    assert reasons == [EscalationReason.UNCOMMITTED_WORK]
    worktree = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    assert (worktree / "plan-output.md").is_file()
    assert not (engine_env.project_root / "plan-output.md").exists()


async def test_post_rebase_acceptance_with_uncommitted_changes_gets_rework_not_a_merge(
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
    dirty_accept: dict[str, object] = {"kind": "accept", "uncommitted": "leader_edit.py"}
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "accept"}, dirty_accept, dirty_accept]
    )
    main_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
        engine_env.project_root, "main"
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert final.counters.conflict_rounds == 2
    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    assert [event.data["decision"] for event in verdicts][-2:] == ["accept", "changes_requested"]
    assert "leader_edit.py" in verdicts[-1].data["issues"][0]["description"]
    worktree = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    assert (worktree / "leader_edit.py").is_file()
    assert (
        await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(
            engine_env.project_root, "main"
        )
        == main_sha
    )


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
    assert task.repo is not None
    assert not await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).branch_exists(
        engine_env.project_root, task.repo.branch
    )
    assert not await engine_env.tmux.target_exists(window_id)
    assert all(run.status is RunStatus.CLOSED for run in engine_env.runs.for_task(task.id))
    done_events = flow_env.events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert done_events
    assert done_events[-1].data["summary"] == final.summary
    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    integration_verdicts = 0
    for turn in {event.data["turn"] for event in verdicts}:
        turn_verdicts = [event for event in verdicts if event.data["turn"] == turn]
        filtered_verdicts = fetch_timeline_events(
            engine_env.connection,
            task.id,
            limit=TIMELINE_EVENT_LIMIT,
            kinds=(EventKind.VERDICT,),
            role=Role.LEADER.value,
            turn=turn,
        )
        [turn_start] = fetch_timeline_events(
            engine_env.connection,
            task.id,
            limit=TIMELINE_EVENT_LIMIT,
            kinds=(EventKind.TURN_STARTED,),
            role=Role.LEADER.value,
            turn=turn,
        )
        assert {event.id for event in filtered_verdicts} == {event.id for event in turn_verdicts}
        assert all(
            event.data["iteration"] == turn_start.data["iteration"] for event in turn_verdicts
        )
        assert all(event.data["role"] == Role.LEADER.value for event in turn_verdicts)
        integration_verdicts += turn_start.data["step"] == Step.INTEGRATE.value
    assert integration_verdicts >= 1


async def test_rebase_uses_fresh_fake_turns_for_each_unmerged_commit(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    task_root = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    engine_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    for content, subject in (("task first\n", "task first"), ("task second\n", "task second")):
        (task_root / "README.md").write_text(content)
        await commit_all(_RUNNER, task_root, subject)
    (engine_env.project_root / "README.md").write_text("base advanced\n")
    await commit_all(_RUNNER, engine_env.project_root, "advance base")

    resolution_count = tmp_path / "resolution-count"
    pre_commit = engine_env.project_root / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text(
        "#!/bin/sh\n"
        "git show :README.md | grep -q '^<<<<<<<' || exit 0\n"
        f"count=0\n[ ! -f '{resolution_count}' ] || count=$(cat '{resolution_count}')\n"
        "count=$((count + 1))\n"
        f"printf '%s' \"$count\" > '{resolution_count}'\n"
        "printf 'resolved-%s\\n' \"$count\" > README.md\n"
        "git add README.md\n"
    )
    pre_commit.chmod(pre_commit.stat().st_mode | stat.S_IXUSR)

    git = _git()
    with pytest.raises(CommandFailed):
        await git.rebase(task_root, "main")
    assert await git.unmerged_paths(task_root) == ["README.md"]

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    _record_completed_conflict_turn(
        engine_env.project_root, flow_env.events, task, _STALE_CONFLICT_TURN
    )
    flow_env.events.append(
        EventKind.INTEGRATION_STARTED,
        task_id=task.id,
        message=f"{task.id} integration started",
        data={"branch": task.repo.branch, "base": task.repo.base},
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}, {"kind": "accept"}])
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert (engine_env.project_root / "README.md").read_text() == "resolved-2\n"
    assert "<<<<<<<" not in (engine_env.project_root / "README.md").read_text()
    assert resolution_count.read_text() == "2"
    implementer_turns = [
        event
        for event in flow_env.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=task.id)
        if event.data.get("step") == Step.INTEGRATE.value
        and event.data.get("role") == Role.IMPLEMENTER.value
    ]
    assert [event.data["turn"] for event in implementer_turns] == [
        _STALE_CONFLICT_TURN,
        "02-integrate-i0",
        "03-integrate-i0",
    ]


async def test_noncontent_continue_failure_keeps_rebase_and_retry_finishes_after_remedy(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    task_root = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task change\n")
    await commit_all(_RUNNER, task_root, "task change")
    (engine_env.project_root / "README.md").write_text("base change\n")
    await commit_all(_RUNNER, engine_env.project_root, "base change")
    git_dir = await _RUNNER.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=task_root,
    )
    index_lock = Path(git_dir.stdout.strip()) / "index.lock"
    resolution_marker = tmp_path / "resolved-conflict"
    pre_commit = engine_env.project_root / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text(
        "#!/bin/sh\n"
        "git show :README.md | grep -q '^<<<<<<<' || exit 0\n"
        "printf 'resolved by the fake agent\\n' > README.md\n"
        "git add README.md\n"
        f": > '{resolution_marker}'\n"
    )
    pre_commit.chmod(pre_commit.stat().st_mode | stat.S_IXUSR)
    post_commit = engine_env.project_root / ".git" / "hooks" / "post-commit"
    post_commit.write_text(
        "#!/bin/sh\n"
        f"[ -f '{resolution_marker}' ] || exit 0\n"
        f"rm '{resolution_marker}'\n"
        f": > '{index_lock}'\n"
    )
    post_commit.chmod(post_commit.stat().st_mode | stat.S_IXUSR)
    await _RUNNER.run(
        ["git", "config", "core.hooksPath", str(engine_env.project_root / ".git" / "hooks")],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=engine_env.project_root,
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}, {"kind": "accept"}])
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    [blocked] = fetch_open_escalations(engine_env.connection)
    assert blocked.reason is EscalationReason.INTEGRATION_BLOCKED
    packet = await asyncio.to_thread(Path(blocked.packet_path).read_text)
    assert "git rebase --continue" in packet
    assert "index.lock" in packet
    question = packet.split("## Question\n\n", 1)[1].split("\n\n## Agent alternatives", 1)[0]
    assert len(question) < 4500
    assert engine_env.tasks.require(task.id).status is Status.ESCALATED
    assert await _git().is_rebase_in_progress(task_root)
    assert await _git().unmerged_paths(task_root) == []
    assert index_lock.is_file()
    assert {option.label.value for option in blocked.options} == {"retry", "cancel"}

    index_lock.unlink()

    async def ignore_task_id(task_id: str) -> None:
        del task_id

    async def ignore_finished_task(task_value: Task) -> None:
        del task_value

    context = HandlerContext(
        tasks=engine_env.tasks,
        escalations=flow_env.escalations,
        events=flow_env.events,
        runs=engine_env.runs,
        project_root=engine_env.project_root,
        cancel_task=ignore_task_id,
        start_task=ignore_task_id,
        finish_task=ignore_finished_task,
        stop_engine=lambda: None,
        refresh_titles=ignore_finished_task,
        atomically=lambda: transaction(engine_env.connection),
    )
    await ResolveHandler(context).handle(
        Resolve(
            escalation_id=blocked.id,
            resolution=RetryResolution(),
            command_id=1,
        )
    )
    retried = engine_env.tasks.require(task.id)
    engine_env.tasks.apply_transition(
        task.id,
        Transition.stay(retried, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.require(task.id)
    assert final.status is Status.DONE
    assert (engine_env.project_root / "README.md").read_text() == "resolved by the fake agent\n"
    assert len(fetch_open_escalations(engine_env.connection)) == 0
    resolved_events = flow_env.events.since(
        0, kinds=[EventKind.ESCALATION_RESOLVED], task_id=task.id
    )
    assert resolved_events[-1].data["resolution"] == "retry"


async def test_completed_conflict_reports_cannot_hide_unmerged_paths_beyond_the_turn_bound(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    task_root = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    engine_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    (task_root / "README.md").write_text("task change\n")
    await commit_all(_RUNNER, task_root, "task change")
    (engine_env.project_root / "README.md").write_text("base change\n")
    await commit_all(_RUNNER, engine_env.project_root, "base change")

    git = _git()
    with pytest.raises(CommandFailed):
        await git.rebase(task_root, "main")
    unmerged_index = await _RUNNER.run(
        ["git", "ls-files", "-u", "--", "README.md"],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=task_root,
    )
    assert unmerged_index.stdout
    post_commit = engine_env.project_root / ".git" / "hooks" / "post-commit"
    post_commit.write_text(
        "#!/bin/sh\n"
        "git update-index --force-remove README.md\n"
        "git update-index --index-info <<'SHIKIRU_INDEX'\n"
        f"{unmerged_index.stdout.rstrip()}\n"
        "SHIKIRU_INDEX\n"
    )
    post_commit.chmod(post_commit.stat().st_mode | stat.S_IXUSR)
    await _RUNNER.run(
        ["git", "config", "core.hooksPath", str(engine_env.project_root / ".git" / "hooks")],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=engine_env.project_root,
    )
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    flow_env.events.append(
        EventKind.INTEGRATION_STARTED,
        task_id=task.id,
        message=f"{task.id} integration started before interruption",
        data={"branch": task.repo.branch, "base": task.repo.base},
    )
    _record_completed_conflict_turn(
        engine_env.project_root, flow_env.events, task, _STALE_CONFLICT_TURN
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.require(task.id)
    assert final.status is Status.ESCALATED
    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_CONFLICT
    packet = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    assert "README.md" in packet
    assert "2 conflict turns" in packet
    assert task_root.is_dir()
    assert await git.is_rebase_in_progress(task_root)
    assert await git.unmerged_paths(task_root) == ["README.md"]
    implementer_turns = [
        event
        for event in flow_env.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=task.id)
        if event.data.get("step") == Step.INTEGRATE.value
        and event.data.get("role") == Role.IMPLEMENTER.value
    ]
    assert [event.data["turn"] for event in implementer_turns] == [
        _STALE_CONFLICT_TURN,
        "02-integrate-i0",
    ]


async def test_uncommitted_changes_at_integration_send_the_task_back_to_implement(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "accept", "uncommitted": "leader_edit.py"}, {"kind": "accept"}],
    )

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.iteration == 2
    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    assert [event.data["decision"] for event in verdicts] == [
        "accept",
        "changes_requested",
        "accept",
    ]
    assert "leader_edit.py" in verdicts[1].data["issues"][0]["description"]


async def test_a_pending_conflict_report_is_reviewed_after_a_clean_rebase_onto_a_moved_base(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    task_root = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    engine_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    (task_root / "task.txt").write_text("task change\n")
    await commit_all(_RUNNER, task_root, "task change")
    (engine_env.project_root / "base.txt").write_text("base advanced\n")
    await commit_all(_RUNNER, engine_env.project_root, "advance base")
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    flow_env.events.append(
        EventKind.INTEGRATION_STARTED,
        task_id=task.id,
        message=f"{task.id} integration started",
        data={"branch": task.repo.branch, "base": task.repo.base},
    )
    _record_completed_conflict_turn(
        engine_env.project_root, flow_env.events, task, _STALE_CONFLICT_TURN
    )
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.require(task.id)
    assert final.status is Status.DONE
    assert (engine_env.project_root / "task.txt").read_text() == "task change\n"
    integration_implementer_turns = [
        event
        for event in flow_env.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=task.id)
        if event.data.get("step") == Step.INTEGRATE.value
        and event.data.get("role") == Role.IMPLEMENTER.value
    ]
    assert [event.data["turn"] for event in integration_implementer_turns] == [_STALE_CONFLICT_TURN]
    leader_turns = [
        event
        for event in flow_env.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=task.id)
        if event.data.get("step") == Step.INTEGRATE.value
        and event.data.get("role") == Role.LEADER.value
    ]
    [done] = flow_env.events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert len(leader_turns) == 1
    assert leader_turns[0].id < done.id
    assert (
        len(flow_env.events.since(0, kinds=[EventKind.INTEGRATION_STARTED], task_id=task.id)) == 1
    )


async def test_a_new_integration_attempt_does_not_reuse_a_report_authorized_by_an_old_retry(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    task_root = engine_env.project_root / ".shikiru" / "worktrees" / task.id
    engine_env.tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    (task_root / "README.md").write_text("task change\n")
    await commit_all(_RUNNER, task_root, "task change")
    (engine_env.project_root / "README.md").write_text("base change\n")
    await commit_all(_RUNNER, engine_env.project_root, "base change")
    git = _git()
    with pytest.raises(CommandFailed):
        await git.rebase(task_root, "main")
    assert await git.unmerged_paths(task_root) == ["README.md"]
    (task_root / "README.md").write_text("old resolution\n")
    await _RUNNER.run(["git", "add", "README.md"], cwd=task_root, timeout=10, check=True)
    assert await git.unmerged_paths(task_root) == []

    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    for message in ("first integration attempt", "superseding integration attempt"):
        flow_env.events.append(
            EventKind.INTEGRATION_STARTED,
            task_id=task.id,
            message=f"{task.id} {message}",
            data={"branch": task.repo.branch, "base": task.repo.base},
        )
        if message == "first integration attempt":
            _record_completed_conflict_turn(
                engine_env.project_root, flow_env.events, task, _STALE_CONFLICT_TURN
            )
            flow_env.events.append(
                EventKind.ESCALATION,
                task_id=task.id,
                message=f"{task.id} integration blocked",
                data={
                    "escalation_id": "E-previous",
                    "reason": EscalationReason.INTEGRATION_BLOCKED.value,
                },
            )
            flow_env.events.append(
                EventKind.ESCALATION_RESOLVED,
                task_id=task.id,
                message="E-previous retried",
                data={
                    "escalation_id": "E-previous",
                    "resolution": "retry",
                },
            )
    pre_commit = engine_env.project_root / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text(
        "#!/bin/sh\n"
        "git show :README.md | grep -q '^<<<<<<<' || exit 0\n"
        "printf 'fresh resolution\\n' > README.md\n"
        "git add README.md\n"
    )
    pre_commit.chmod(pre_commit.stat().st_mode | stat.S_IXUSR)
    await _RUNNER.run(
        ["git", "config", "core.hooksPath", str(engine_env.project_root / ".git" / "hooks")],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=engine_env.project_root,
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=60)

    final = engine_env.tasks.require(task.id)
    assert final.status is Status.DONE
    assert (engine_env.project_root / "README.md").read_text() == "fresh resolution\n"
    implementer_turns = [
        event
        for event in flow_env.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=task.id)
        if event.data.get("step") == Step.INTEGRATE.value
        and event.data.get("role") == Role.IMPLEMENTER.value
    ]
    assert [event.data["turn"] for event in implementer_turns] == [
        _STALE_CONFLICT_TURN,
        "02-integrate-i0",
    ]


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
    open_escalations = fetch_open_escalations(flow_env.env.connection)
    assert open_escalations[-1].reason is EscalationReason.INTEGRATION_CONFLICT


_STUCK_ONCE_HOOK = """#!/bin/sh
[ "$1" = committed ] || exit 0
branch_ref='{branch_ref}'
matched=0
while read old new ref; do
    if [ "$ref" = "$branch_ref" ]; then matched=1; fi
done
[ "$matched" = 1 ] || exit 0
[ ! -e '{seen}' ] || exit 0
touch '{seen}'
sleep 300
"""
_SHORT_GIT_TIMEOUT_SECONDS = 1.0
_SCENARIO_WAIT_SECONDS = 10.0


async def test_a_rebase_that_timed_out_is_undone_and_not_mistaken_for_a_conflict(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "task.txt").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "other.txt").write_text("other\n")
    await commit_all(_RUNNER, project_root, "base moves on")
    task_sha = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).rev_parse(task_root, "HEAD")
    hook = project_root / ".git" / "hooks" / "reference-transaction"
    hook.write_text(
        _STUCK_ONCE_HOOK.format(seen=tmp_path / "seen", branch_ref=f"refs/heads/{state.branch}")
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    git = Git(_RUNNER, command_timeout=_SHORT_GIT_TIMEOUT_SECONDS)
    events = EventLog(connection, SystemClock())
    conflicts_resolved: list[str] = []

    async def resolve(
        task_value: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        recovery: ConflictRecovery,
    ) -> tuple[Task, IntegrationMetadata]:
        del continue_rebase, recovery
        conflicts_resolved.append(task_value.id)
        return task_value, IntegrationMetadata("resolved")

    async def keep_worktree(task_value: Task) -> None:
        del task_value

    def integrator() -> Integrator:
        return Integrator(
            git=git,
            strategy=_local_merge(project_root, SystemClock(), 0, events),
            base_refs=LocalBaseRefs(),
            lock=asyncio.Lock(),
            project_root=project_root,
            tasks=TaskStore(connection, SystemClock()),
            events=events,
            finish_task=keep_worktree,
        )

    with pytest.raises(CommandTimeoutError):
        await integrator().run(task, resolve, IntegrationMetadata("summary"))

    assert not await git.is_rebase_in_progress(task_root)
    assert await git.head_branch(task_root) == state.branch
    assert await git.rev_parse(task_root, "HEAD") == task_sha

    result = await integrator().run(task, resolve, IntegrationMetadata("summary"))

    assert result.commits
    assert conflicts_resolved == []
    assert (project_root / "task.txt").read_text() == "task\n"


@dataclass
class _RebaseScenario:
    connection: Connection
    project_root: Path
    task: Task
    task_root: Path
    seen: Path
    task_sha: str
    git: Git
    conflicts_resolved: list[str]

    def integrator(self) -> Integrator:
        events = EventLog(self.connection, SystemClock())

        async def keep_worktree(task_value: Task) -> None:
            del task_value

        return Integrator(
            git=self.git,
            strategy=_local_merge(self.project_root, SystemClock(), 0, events),
            base_refs=LocalBaseRefs(),
            lock=asyncio.Lock(),
            project_root=self.project_root,
            tasks=TaskStore(self.connection, SystemClock()),
            events=events,
            finish_task=keep_worktree,
        )

    async def resolve(
        self,
        task_value: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        recovery: ConflictRecovery,
    ) -> tuple[Task, IntegrationMetadata]:
        del continue_rebase, recovery
        self.conflicts_resolved.append(task_value.id)
        return task_value, IntegrationMetadata("resolved")

    async def integrate(self) -> IntegrationSummary:
        return await self.integrator().run(self.task, self.resolve, IntegrationMetadata("summary"))

    async def assert_rebased_without_a_conflict_turn(self) -> None:
        result = await self.integrate()
        assert result.commits
        assert self.conflicts_resolved == []
        assert (self.project_root / "task.txt").read_text() == "task\n"


async def _rebase_scenario(connection: Connection, tmp_path: Path) -> _RebaseScenario:
    project_root = await _repo(tmp_path / "project")
    task, state = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "task.txt").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "other.txt").write_text("other\n")
    await commit_all(_RUNNER, project_root, "base moves on")
    git = Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)
    task_sha = await git.rev_parse(task_root, "HEAD")
    hook = project_root / ".git" / "hooks" / "reference-transaction"
    seen = tmp_path / "seen"
    hook.write_text(_STUCK_ONCE_HOOK.format(seen=seen, branch_ref=f"refs/heads/{state.branch}"))
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    return _RebaseScenario(connection, project_root, task, task_root, seen, task_sha, git, [])


async def test_a_cancelled_rebase_is_undone_and_not_mistaken_for_a_conflict(
    connection: Connection, tmp_path: Path
) -> None:
    scenario = await _rebase_scenario(connection, tmp_path)
    running = asyncio.create_task(scenario.integrate())
    await wait_until(scenario.seen.exists, _SCENARIO_WAIT_SECONDS)

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert not await scenario.git.is_rebase_in_progress(scenario.task_root)
    assert await scenario.git.rev_parse(scenario.task_root, "HEAD") == scenario.task_sha
    await scenario.assert_rebased_without_a_conflict_turn()


async def test_a_rebase_left_by_a_killed_engine_is_reset_and_redone(
    connection: Connection, tmp_path: Path
) -> None:
    scenario = await _rebase_scenario(connection, tmp_path)
    killed = await _RUNNER.spawn(["git", "rebase", "main"], cwd=scenario.task_root)
    await wait_until(scenario.seen.exists, _SCENARIO_WAIT_SECONDS)
    os.killpg(killed.pid, signal.SIGKILL)
    await killed.wait()
    await wait_until(lambda: not _RUNNER.group_is_running(killed.pid), _SCENARIO_WAIT_SECONDS)
    assert await scenario.git.is_rebase_in_progress(scenario.task_root)
    assert await scenario.git.unmerged_paths(scenario.task_root) == []
    lock_path = await _RUNNER.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "ORIG_HEAD.lock"],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=scenario.task_root,
        check=True,
    )
    assert not await asyncio.to_thread(Path(lock_path.stdout.strip()).exists)

    await scenario.assert_rebased_without_a_conflict_turn()


async def test_a_rebase_stopped_on_conflicts_is_still_handed_to_the_conflict_turn(
    connection: Connection, tmp_path: Path
) -> None:
    project_root = await _repo(tmp_path / "project")
    task, _ = await _task_with_worktree(connection, project_root)
    task_root = project_root / ".shikiru" / "worktrees" / task.id
    (task_root / "README.md").write_text("task\n")
    await commit_all(_RUNNER, task_root, "task change")
    (project_root / "README.md").write_text("main\n")
    await commit_all(_RUNNER, project_root, "main change")
    await _RUNNER.run(["git", "rebase", "main"], cwd=task_root, timeout=_SCENARIO_WAIT_SECONDS)
    conflicts_resolved: list[str] = []

    async def resolve(
        task_value: Task,
        continue_rebase: Callable[[], Awaitable[None]],
        recovery: ConflictRecovery,
    ) -> tuple[Task, IntegrationMetadata]:
        assert not recovery.resumed_report
        conflicts_resolved.append(task_value.id)
        (task_root / "README.md").write_text("resolved\n")
        await _RUNNER.run(
            ["git", "add", "README.md"], cwd=task_root, timeout=_SCENARIO_WAIT_SECONDS
        )
        await continue_rebase()
        return task_value, IntegrationMetadata("resolved")

    scenario = _RebaseScenario(
        connection, project_root, task, task_root, tmp_path, "", _git(), conflicts_resolved
    )
    await scenario.integrator().run(task, resolve, IntegrationMetadata("summary"))

    assert conflicts_resolved == [task.id]
    assert (project_root / "README.md").read_text() == "resolved\n"


def _git() -> Git:
    return Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)
