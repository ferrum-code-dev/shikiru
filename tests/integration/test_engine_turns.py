from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.events import EventKind
from shikiru.domain.reports import HELPER_REPORT_FILENAME, REPORT_FILENAME
from shikiru.domain.tasks import Mode, Role, Status, Step
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.engine.turns import NUDGE_LIMIT
from shikiru.workspace.messages import OperatorMessages
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_NAME, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60


async def test_fake_cli_accept_writes_the_report_and_turn_files(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    turns = run_dir(engine_env.project_root, task.id) / "turns"
    [turn_dir] = [path for path in turns.iterdir() if path.name.endswith("-implement-i1")]
    report = json.loads((turn_dir / REPORT_FILENAME).read_text())
    assert report["status"] == "completed"
    current = role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    assert (current / "current_turn").resolve() == turn_dir.resolve()
    assert (current / "turn.pid").exists() is False
    assert (turn_dir / "report.schema.json").is_file()


async def test_reportless_fake_turn_is_nudged_and_resumed_on_its_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "no_report"}, {"kind": "accept"}],
    )
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    events = flow_env.events.since(0, task_id=task.id)
    nudges = [event for event in events if event.kind is EventKind.NUDGE]
    assert len(nudges) == 1
    implementer_runs = [
        run for run in engine_env.runs.for_task(task.id) if run.role is Role.IMPLEMENTER
    ]
    assert len(implementer_runs) == 1
    assert implementer_runs[0].session_id is not None
    view_path = (
        role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value) / "view.jsonl"
    )
    view = [json.loads(line) for line in view_path.read_text().splitlines()]
    starts = [row["session_id"] for row in view if row["signal"] == "SessionStarted"]
    assert starts == [implementer_runs[0].session_id, implementer_runs[0].session_id]
    completed = [event for event in events if event.kind is EventKind.TURN_COMPLETED]
    implementer_completion = next(
        event for event in completed if event.data["role"] == "implementer"
    )
    assert implementer_completion.data["usage_delta"]["input_tokens"] == 240


async def test_invalid_fake_report_is_logged_and_nudged(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="shikiru.engine.turns")
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "corrupt_report": True}, {"kind": "accept"}],
    )
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    events = flow_env.events.since(0, task_id=task.id)
    assert len([event for event in events if event.kind is EventKind.NUDGE]) == 1
    turn_dir = run_dir(engine_env.project_root, task.id) / "turns" / "01-implement-i1"
    report = json.loads((turn_dir / REPORT_FILENAME).read_text())
    assert report["status"] == "completed"
    assert any("invalid report in completed turn" in record.message for record in caplog.records)


async def test_helper_rebuilds_after_both_report_nudges(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"summary": "rebuilt by helper", "commits": []}]}
                )
            }
        }
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.PLAN)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "no_report"}] * (NUDGE_LIMIT + 1),
    )
    flow_env = build_flow(engine_env, settings)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    events = flow_env.events.since(0, task_id=task.id)
    assert len([event for event in events if event.kind is EventKind.NUDGE]) == NUDGE_LIMIT
    turn_dir = run_dir(engine_env.project_root, task.id) / "turns" / "01-plan-i0"
    rebuilt = json.loads((turn_dir / HELPER_REPORT_FILENAME).read_text())
    assert rebuilt["summary"] == "rebuilt by helper"
    assert not (turn_dir / REPORT_FILENAME).exists()


async def test_queued_operator_message_reaches_only_its_roles_next_prompt(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    messages = OperatorMessages(engine_env.project_root)
    for_implementer = messages.queue(task.id, Role.IMPLEMENTER, "use the new helper", "c1")
    for_leader = messages.queue(task.id, Role.LEADER, "keep scope small", "c2")
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    turns = run_dir(engine_env.project_root, task.id) / "turns"
    for role_message, text, step in (
        (for_implementer, "use the new helper", "-implement-i1"),
        (for_leader, "keep scope small", "-verify-i1"),
    ):
        [turn_dir] = [path for path in turns.iterdir() if path.name.endswith(step)]
        [delivered] = (turn_dir / "messages").iterdir()
        assert delivered.read_text() == text
        assert not role_message.exists()
        prompt = (turn_dir / "prompt.md").read_text()
        assert str(delivered) in prompt


async def test_operator_message_is_redelivered_when_its_turn_fails(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "fail", "failure_kind": "other"}, {"kind": "accept"}],
    )
    OperatorMessages(engine_env.project_root).queue(
        task.id, Role.IMPLEMENTER, "use the helper", "c1"
    )
    flow_env = build_flow(engine_env, flow_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    turns = run_dir(engine_env.project_root, task.id) / "turns"
    failed, retried = sorted(
        path for path in turns.iterdir() if path.name.endswith("-implement-i1")
    )
    assert not (failed / "messages").exists()
    [delivered] = (retried / "messages").iterdir()
    assert delivered.read_text() == "use the helper"
    assert str(delivered) in (retried / "prompt.md").read_text()


async def test_messages_delivered_to_a_killed_turn_lead_the_replacement_prompt(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    release = tmp_path / "release"
    os.mkfifo(release)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "release_fifo": str(release)}, {"kind": "accept"}],
    )
    messages = OperatorMessages(engine_env.project_root)
    messages.queue(task.id, Role.IMPLEMENTER, "first guidance", "c1")
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"kind": "other", "confidence": 0.95}]}
                )
            }
        }
    )
    flow_env = build_flow(engine_env, settings)
    role_path = role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))

    def started() -> bool:
        run = engine_env.runs.get_live(task.id, Role.IMPLEMENTER)
        return (
            (role_path / TURN_PID_NAME).is_file() and run is not None and run.session_id is not None
        )

    await wait_until(started, timeout=_FLOW_TIMEOUT_SECONDS)

    messages.queue(task.id, Role.IMPLEMENTER, "newer guidance", "c2")
    os.kill(read_turn_pid(role_path / TURN_PID_NAME).pid, signal.SIGKILL)
    await asyncio.wait_for(flow_task, timeout=_FLOW_TIMEOUT_SECONDS)

    turns = run_dir(engine_env.project_root, task.id) / "turns"
    *_, replacement = sorted(path for path in turns.iterdir() if "-implement-i1" in path.name)
    delivered = sorted((replacement / "messages").iterdir())
    assert [path.read_text() for path in delivered] == ["first guidance", "newer guidance"]
    prompt = (replacement / "prompt.md").read_text()
    assert prompt.index(str(delivered[0])) < prompt.index(str(delivered[1]))
