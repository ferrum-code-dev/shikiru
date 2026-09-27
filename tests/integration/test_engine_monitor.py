from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING

import psutil

from shikiru.clock import SystemClock
from shikiru.config.settings import AgentConfig
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Mode, Role, Status, Step
from shikiru.engine.turn_processes import read_turn_pid
from shikiru.store.quotas import fetch_quotas
from shikiru.workspace.roots import run_dir
from tests.support.clock import FakeClock
from tests.support.engine import set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60
_SHORT_STALL_SECONDS = 2


async def test_monitor_records_quota_compaction_usage_and_turn_view(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    observed_at = TASK_CREATED_AT.isoformat()
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [
            {
                "kind": "needs_decision",
                "quota_windows": [
                    {
                        "name": "weekly",
                        "used_pct": 47,
                        "resets_at": (TASK_CREATED_AT + timedelta(hours=1)).isoformat(),
                        "observed_at": observed_at,
                    }
                ],
            },
            {"kind": "accept"},
        ],
    )
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    [quota] = fetch_quotas(engine_env.connection, "fake")
    assert quota.name == "weekly"
    assert quota.used_pct == 47
    events = flow_env.events.since(0, kinds=[EventKind.COMPACTED], task_id=task.id)
    assert {event.data["role"] for event in events} == {"implementer", "leader"}
    assert all(event.data["pre_tokens"] == 2000 for event in events)
    assert all(event.data["post_tokens"] == 400 for event in events)
    role_path = run_dir(engine_env.project_root, task.id) / "implementer"
    rows = [json.loads(line) for line in (role_path / "view.jsonl").read_text().splitlines()]
    signals = [row["signal"] for row in rows]
    assert "QuotaReported" in signals
    assert "Compacted" in signals
    assert "UsageReported" in signals
    assert "TurnEnded" in signals
    assert not (role_path / "turn.pid").exists()


async def test_stalled_turn_is_stopped_then_resumed_with_continue(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    timeouts = settings.orchestration.timeouts.model_copy(update={"stall": _SHORT_STALL_SECONDS})
    orchestration = settings.orchestration.model_copy(update={"timeouts": timeouts})
    settings = settings.model_copy(update={"orchestration": orchestration})
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}, {"kind": "accept"}])
    turn_clock = FakeClock(TASK_CREATED_AT)
    flow_env = build_flow(engine_env, settings, clock=clock, turn_clock=turn_clock)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    role_path = run_dir(engine_env.project_root, task.id) / "implementer"

    async def first_session_started() -> bool:
        run = engine_env.runs.get_live(task.id, Role.IMPLEMENTER)
        return (role_path / "turn.pid").is_file() and run is not None and run.session_id is not None

    await wait_until(first_session_started, timeout=10)
    turn_clock.advance(_SHORT_STALL_SECONDS + 1)
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    rows = [json.loads(line) for line in (role_path / "view.jsonl").read_text().splitlines()]
    starts = [row["session_id"] for row in rows if row["signal"] == "SessionStarted"]
    assert len(starts) == 2
    assert starts[0] == starts[1]
    task_turns = run_dir(engine_env.project_root, task.id) / "turns"
    continue_prompts = [
        path / "prompt.md"
        for path in task_turns.iterdir()
        if path.is_dir()
        and "continue this task's current turn" in (path / "prompt.md").read_text().lower()
    ]
    assert len(continue_prompts) == 1
    assert not (role_path / "turn.pid").exists()


async def test_cancelling_a_turn_stops_its_child_process(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}])
    flow_env = build_flow(engine_env, flow_settings())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    role_path = run_dir(engine_env.project_root, task.id) / "implementer"

    async def process_started() -> bool:
        run = engine_env.runs.get_live(task.id, Role.IMPLEMENTER)
        return (role_path / "turn.pid").is_file() and run is not None and run.session_id is not None

    await wait_until(process_started, timeout=10)
    pid = read_turn_pid(role_path / "turn.pid").pid
    flow_task.cancel()
    with suppress(asyncio.CancelledError):
        await flow_task

    assert await asyncio.to_thread(psutil.pid_exists, pid) is False
    assert not (role_path / "turn.pid").exists()


async def test_turn_pid_tracks_the_continue_process_after_in_turn_compaction(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    base_settings = flow_settings()
    settings = base_settings.model_copy(
        update={
            "agents": {"fake": AgentConfig(options={"compact_at_pct": 50})},
            "orchestration": base_settings.orchestration.model_copy(update={"poll_seconds": 1}),
        }
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}])
    flow_env = build_flow(engine_env, settings, turn_clock=SystemClock())
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    role_path = run_dir(engine_env.project_root, task.id) / "implementer"
    pid_path = role_path / "turn.pid"

    async def prompt_is_active_after_compaction() -> bool:
        compactions = flow_env.events.since(0, kinds=[EventKind.COMPACTED], task_id=task.id)
        if not compactions or not pid_path.is_file():
            return False
        pid = read_turn_pid(pid_path).pid
        if not await asyncio.to_thread(psutil.pid_exists, pid):
            return False
        try:
            command = await asyncio.to_thread(psutil.Process(pid).cmdline)
        except psutil.Error:
            return False
        return "turn" in command

    try:
        await wait_until(prompt_is_active_after_compaction, timeout=20)
        pid = read_turn_pid(pid_path).pid
        command = await asyncio.to_thread(psutil.Process(pid).cmdline)
        assert "turn" in command
        assert await asyncio.to_thread(psutil.pid_exists, pid)
    finally:
        flow_task.cancel()
        with suppress(asyncio.CancelledError):
            await flow_task

    assert not pid_path.exists()
    assert await asyncio.to_thread(psutil.pid_exists, pid) is False
