"""A submitted report hands the turn over to the engine while the agent process still lives."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Mode, Role, Status, StatusReason, Step
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.process import SubprocessRunner
from shikiru.view_log import view_log_path
from shikiru.workspace.git import Git
from shikiru.workspace.roots import run_dir
from tests.support.engine import set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import GIT_COMMAND_TIMEOUT
from tests.support.waiting import finish_within, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.flow import FlowEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60
_WAIT_SECONDS = 30
_GRACE_SECONDS = 3
_LINGER = {"stall_after_report": True}
_REPLACEMENT = {"title": "redo it properly", "request": "redo"}


def _settings(*, grace: float = 0, shutdown: float | None = None) -> Settings:
    settings = flow_settings()
    update: dict[str, float] = {"report_grace_seconds": grace}
    if shutdown is not None:
        update["shutdown_seconds"] = shutdown
    orchestration = settings.orchestration.model_copy(update=update)
    return settings.model_copy(update={"orchestration": orchestration})


def _turn_pid_path(engine_env: EngineEnv, task: Task, role: Role) -> Path:
    return run_dir(engine_env.project_root, task.id) / role.value / TURN_PID_NAME


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _turns_started(flow_env: FlowEnv, task: Task, step: Step) -> int:
    started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task.id)
    return sum(event.data["step"] == step.value for event in started)


async def _live_pid(engine_env: EngineEnv, task: Task, role: Role) -> int:
    path = _turn_pid_path(engine_env, task, role)
    await wait_until(path.is_file, _WAIT_SECONDS)
    return read_turn_pid(path).pid


async def test_a_report_ends_a_turn_whose_agent_process_stays_alive(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept", **_LINGER}])
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)

    await finish_within(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert _turns_started(flow_env, task, Step.IMPLEMENT) == 1
    assert flow_env.events.since(0, kinds=[EventKind.NUDGE], task_id=task.id) == []
    assert not _is_alive(pid)


async def test_a_needs_decision_report_ends_a_lingering_turn_and_is_consulted(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "needs_decision", **_LINGER}])
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)

    await finish_within(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    assert _turns_started(flow_env, task, Step.CONSULT) >= 1
    assert flow_env.events.since(0, kinds=[EventKind.NUDGE], task_id=task.id) == []
    assert not _is_alive(pid)


async def test_a_report_corrupted_after_the_handoff_is_nudged_not_consumed(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "corrupt_report": True}, {"kind": "accept", **_LINGER}],
    )
    flow_env = build_flow(engine_env, _settings())

    await finish_within(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert len(flow_env.events.since(0, kinds=[EventKind.NUDGE], task_id=task.id)) == 1


async def test_a_rejected_report_submission_leaves_the_turn_running_until_cancelled(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "failed_report_submission"}])
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)
    view = view_log_path(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    await wait_until(
        lambda: view.is_file() and '"outcome": "failure"' in view.read_text(), _WAIT_SECONDS
    )

    assert _is_alive(pid)
    flow_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flow_task

    assert not _is_alive(pid)
    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.IMPLEMENT
    assert not (
        run_dir(engine_env.project_root, task.id) / "turns" / "01-implement-i1" / "report.json"
    ).exists()


async def test_a_dirty_worktree_is_still_refused_after_the_handoff(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {"kind": "supersede", "new_tasks": [_REPLACEMENT], "uncommitted": "TODO.md", **_LINGER},
            {"kind": "supersede", "new_tasks": [_REPLACEMENT]},
        ],
    )
    flow_env = build_flow(engine_env, _settings())

    await finish_within(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    assert [event.data["decision"] for event in verdicts] == [
        "supersede",
        "changes_requested",
        "supersede",
    ]
    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.CANCELLED
    assert final.status_reason is StatusReason.SUPERSEDED
    assert final.repo is not None
    committed = await Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT).diff_name_only(
        engine_env.project_root, f"{final.repo.base}...{final.repo.branch}"
    )
    assert "TODO.md" in committed


async def test_a_failure_arriving_with_the_report_is_not_masked_by_the_handoff(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "fail_after_report": "auth", **_LINGER}],
    )
    flow_env = build_flow(engine_env, _settings())

    await finish_within(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.IMPLEMENT
    assert final.status is not Status.INTEGRATING
    assert _turns_started(flow_env, task, Step.INTEGRATE) == 0


async def test_a_report_ends_a_turn_whose_agent_keeps_emitting_signals(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server, Role.IMPLEMENTER, [{"kind": "accept", "chatter_after_report": True}]
    )
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)

    await finish_within(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert not _is_alive(pid)


async def test_an_agent_ignoring_the_stop_is_killed_and_its_report_still_consumed(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "ignore_stop_after_report": True, **_LINGER}],
    )
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)

    await finish_within(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert not _is_alive(pid)


async def test_cancelling_while_a_handed_off_turn_is_stopping_still_ends_its_process(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "ignore_stop_after_report": True, **_LINGER}],
    )
    flow_env = build_flow(engine_env, _settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    pid = await _live_pid(engine_env, task, Role.IMPLEMENTER)
    report = run_dir(engine_env.project_root, task.id) / "turns" / "01-implement-i1" / "report.json"
    await wait_until(report.is_file, _WAIT_SECONDS)

    flow_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flow_task

    assert not _is_alive(pid)
    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.IMPLEMENT
    assert final.repo is not None
    assert final.status is Status.RUNNING


async def test_a_turn_ending_on_its_own_after_submitting_keeps_its_usage_and_message(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, _settings(grace=_GRACE_SECONDS))

    await finish_within(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    [run] = [run for run in engine_env.runs.for_task(task.id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens > 0
    assert run.last_message
    view = view_log_path(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    signals = [json.loads(line)["signal"] for line in view.read_text().splitlines()]
    assert signals[-2:] == ["UsageReported", "TurnEnded"]


async def test_a_stream_that_stays_open_after_the_stop_does_not_advance_on_the_report(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    holder_pid_file = tmp_path / "stream-holder.pid"
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [
            {"kind": "accept", "hold_stream_pid_file": str(holder_pid_file), **_LINGER},
            {"kind": "accept"},
        ],
    )
    flow_env = build_flow(engine_env, _settings(shutdown=1))
    try:
        await finish_within(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)
    finally:
        os.kill(int(holder_pid_file.read_text()), signal.SIGKILL)

    assert _turns_started(flow_env, task, Step.IMPLEMENT) == 2
