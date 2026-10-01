"""Task holds stop every execution path, integration included (03 §6, §9)."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
from fake_agent.scripted import FAKE_CHANGE_FILENAME

from shikiru.domain.events import EventKind
from shikiru.domain.reports import ReportKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.engine.turns import TurnHeld, TurnRequest
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptName
from shikiru.store.escalations import fetch_open_escalations
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.engine import set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import (
    CountingLock,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.flow import FlowEnv
    from tests.support.tmux import TmuxServer

_RUNNER = SubprocessRunner()
_GIT = Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)
_FLOW_TIMEOUT_SECONDS = 60
_WAIT_SECONDS = 30
_RETRY_ADVANCE_SECONDS = 120
_LOCK_CONTENDED = 2
_TASK_FILE = "task-output.txt"
_HOLDS = {"paused": Hold.PAUSED, "intervention": Hold.INTERVENTION}


def _worktree(env: EngineEnv, task: Task) -> Path:
    return env.project_root / ".shikiru" / "worktrees" / task.id


async def _committed_integrating_task(env: EngineEnv, filename: str = _TASK_FILE) -> Task:
    await init_project_repo(env.project_root)
    task = await build_flow_task(env, mode=Mode.IMPLEMENT)
    (_worktree(env, task) / filename).write_text("task\n")
    await commit_all(_RUNNER, _worktree(env, task), "task change")
    env.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    current = env.tasks.get(task.id)
    assert current is not None
    return current


def _integration_started(flow_env: FlowEnv, task: Task) -> int:
    return len(flow_env.events.since(0, kinds=[EventKind.INTEGRATION_STARTED], task_id=task.id))


async def _main_sha(env: EngineEnv) -> str:
    return await _GIT.rev_parse(env.project_root, "main")


@pytest.mark.parametrize("hold", _HOLDS.values(), ids=_HOLDS.keys())
async def test_a_held_integrating_task_integrates_nothing_until_it_is_resumed(
    engine_env: EngineEnv, hold: Hold
) -> None:
    task = await _committed_integrating_task(engine_env)
    engine_env.tasks.set_holds(task.id, frozenset({hold}))
    main_sha = await _main_sha(engine_env)
    branch_sha = await _GIT.rev_parse(_worktree(engine_env, task), "HEAD")
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)

    await asyncio.wait_for(flow_env.flow.run(task.id, resumed=True), timeout=_FLOW_TIMEOUT_SECONDS)

    held = engine_env.tasks.get(task.id)
    assert held is not None
    assert held.status is Status.INTEGRATING
    assert _integration_started(flow_env, task) == 0
    assert await _main_sha(engine_env) == main_sha
    assert await _GIT.rev_parse(_worktree(engine_env, task), "HEAD") == branch_sha

    engine_env.tasks.set_holds(task.id, frozenset())
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    done = engine_env.tasks.get(task.id)
    assert done is not None
    assert done.status is Status.DONE
    assert (engine_env.project_root / _TASK_FILE).is_file()


async def test_a_hold_set_while_integration_waits_for_access_is_honoured_before_the_merge(
    engine_env: EngineEnv,
) -> None:
    task = await _committed_integrating_task(engine_env, filename="README.md")
    (engine_env.project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    flow_env = build_flow(engine_env, flow_settings(), clock=clock, local_integration=True)
    main_sha = await _main_sha(engine_env)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(clock.has_pending_sleep, timeout=_WAIT_SECONDS)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    await _RUNNER.run(
        ["git", "restore", "--", "README.md"], cwd=engine_env.project_root, timeout=10, check=True
    )
    clock.advance(_RETRY_ADVANCE_SECONDS)
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    held = engine_env.tasks.get(task.id)
    assert held is not None
    assert held.status is Status.INTEGRATING
    assert await _main_sha(engine_env) == main_sha

    engine_env.tasks.set_holds(task.id, frozenset())
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    done = engine_env.tasks.get(task.id)
    assert done is not None
    assert done.status is Status.DONE
    assert (engine_env.project_root / "README.md").read_text() == "task\n"


async def test_a_pause_during_verify_stops_the_task_before_it_integrates(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    release = tmp_path / "release-verify"
    os.mkfifo(release)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "accept", "release_fifo": str(release)}]
    )
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    main_sha = await _main_sha(engine_env)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))

    def verifying() -> bool:
        started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task.id)
        return any(event.data["step"] == Step.VERIFY.value for event in started)

    await wait_until(verifying, timeout=_WAIT_SECONDS)
    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    held = engine_env.tasks.get(task.id)
    assert held is not None
    assert held.status is Status.INTEGRATING
    assert _integration_started(flow_env, task) == 0
    assert await _main_sha(engine_env) == main_sha


def _turn_started(flow_env: FlowEnv, task: Task, step: Step) -> list[object]:
    started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task.id)
    return [event for event in started if event.data["step"] == step.value]


async def test_a_pause_during_a_turn_without_a_valid_report_starts_no_nudge(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    release = tmp_path / "release-implement"
    os.mkfifo(release)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "corrupt_report": True, "release_fifo": str(release)}],
    )
    flow_env = build_flow(engine_env, flow_settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: bool(_turn_started(flow_env, task, Step.IMPLEMENT)), _WAIT_SECONDS)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    assert flow_env.events.since(0, kinds=[EventKind.NUDGE], task_id=task.id) == []
    held = engine_env.tasks.get(task.id)
    assert held is not None
    assert held.status is Status.RUNNING
    assert held.step is Step.IMPLEMENT


@pytest.mark.parametrize("hold", _HOLDS.values(), ids=_HOLDS.keys())
async def test_a_hold_set_just_before_the_agent_process_spawns_starts_no_process(
    engine_env: EngineEnv, tmux_server: TmuxServer, hold: Hold
) -> None:
    del tmux_server
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    turns = engine_env.turn_system.turns
    selection = engine_env.turn_system.sessions.session_for(task, Role.IMPLEMENTER)
    request = TurnRequest(
        step=Step.IMPLEMENT,
        iteration=1,
        kind=ReportKind.IMPLEMENTATION,
        template=PromptName.IMPLEMENT,
        context={
            "request": task.request,
            "workspace": str(_worktree(engine_env, task)),
            "trackers": [],
            "reports": [],
        },
        facts="",
    )
    engine_env.tasks.set_holds(task.id, frozenset({hold}))

    with pytest.raises(TurnHeld):
        await turns.run(task, Role.IMPLEMENTER, selection, request, on_nudge=lambda: None)

    run = engine_env.runs.get_live(task.id, Role.IMPLEMENTER)
    assert run is not None
    assert run.session_id is None


async def test_a_pause_during_a_conflict_turn_leaves_the_rebase_unfinished(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    release = tmp_path / "release-conflicts"
    os.mkfifo(release)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    (engine_env.project_root / FAKE_CHANGE_FILENAME).write_text("main\n")
    await commit_all(_RUNNER, engine_env.project_root, "main change")
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept", "release_fifo": str(release)}],
    )
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True)
    main_sha = await _main_sha(engine_env)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: bool(_turn_started(flow_env, task, Step.INTEGRATE)), _WAIT_SECONDS)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    assert await _GIT.is_rebase_in_progress(_worktree(engine_env, task))
    assert len(_turn_started(flow_env, task, Step.INTEGRATE)) == 1
    assert await _main_sha(engine_env) == main_sha


async def test_a_task_paused_while_waiting_for_the_integration_lock_emits_no_integration_started(
    engine_env: EngineEnv,
) -> None:
    task = await _committed_integrating_task(engine_env)
    lock = CountingLock()
    flow_env = build_flow(engine_env, flow_settings(), local_integration=True, lock=lock)
    main_sha = await _main_sha(engine_env)
    await lock.acquire()
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: lock.acquisitions == _LOCK_CONTENDED, timeout=_WAIT_SECONDS)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    lock.release()
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    assert _integration_started(flow_env, task) == 0
    assert await _main_sha(engine_env) == main_sha


async def test_a_paused_task_never_escalates_when_the_base_access_wait_budget_runs_out(
    engine_env: EngineEnv,
) -> None:
    task = await _committed_integrating_task(engine_env, filename="README.md")
    (engine_env.project_root / "README.md").write_text("operator\n")
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_env = build_flow(engine_env, settings, clock=clock, local_integration=True)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(clock.has_pending_sleep, timeout=_WAIT_SECONDS)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    clock.advance(settings.orchestration.timeouts.integration_wait + _RETRY_ADVANCE_SECONDS)
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    held = engine_env.tasks.get(task.id)
    assert held is not None
    assert held.status is Status.INTEGRATING
    assert fetch_open_escalations(engine_env.connection) == []
