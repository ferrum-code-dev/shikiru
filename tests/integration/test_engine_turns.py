from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.events import EventKind
from shikiru.domain.reports import HELPER_REPORT_FILENAME, REPORT_FILENAME
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Role, Status, Step
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.engine.turns import NUDGE_LIMIT
from shikiru.paths import PROMPT_OVERRIDES_RELATIVE_PATH, turns_dir
from shikiru.store.events import fetch_timeline_events
from shikiru.workspace.messages import OperatorMessages
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_NAME, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.permissions import requires_enforced_permissions
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60
_READ_ONLY_DIRECTORY = 0o500
_WRITABLE_DIRECTORY = 0o700
TIMELINE_EVENT_LIMIT = 40


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
    current = role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    turn_name = (current / "current_turn").resolve().name
    turn_starts = [
        event
        for event in events
        if event.kind is EventKind.TURN_STARTED and event.data["role"] == "implementer"
    ]
    assert len(turn_starts) == 1
    assert turn_starts[0].data["turn"] == turn_name
    assert nudges[0].data["turn"] == turn_name
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
    assert implementer_completion.data["turn"] == turn_name
    assert implementer_completion.data["usage_delta"]["uncached_input_tokens"] == 240
    filtered_nudges = fetch_timeline_events(
        engine_env.connection,
        task.id,
        limit=TIMELINE_EVENT_LIMIT,
        kinds=(EventKind.NUDGE,),
        role=Role.IMPLEMENTER.value,
        turn=turn_name,
    )
    assert [event.id for event in filtered_nudges] == [nudges[0].id]


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
                    options={
                        "asks": [
                            {
                                "summary": "rebuilt by helper",
                                "commit_subject": "docs: rebuild the plan",
                                "commits": [],
                            }
                        ]
                    }
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
    failed_turns = fetch_timeline_events(
        engine_env.connection,
        task.id,
        limit=TIMELINE_EVENT_LIMIT,
        kinds=(EventKind.TURN_FAILED,),
        role=Role.IMPLEMENTER.value,
        turn=failed.name,
    )
    assert len(failed_turns) == 1
    [failed_event] = failed_turns
    [failed_start] = fetch_timeline_events(
        engine_env.connection,
        task.id,
        limit=TIMELINE_EVENT_LIMIT,
        kinds=(EventKind.TURN_STARTED,),
        role=Role.IMPLEMENTER.value,
        turn=failed.name,
    )
    assert failed_event.data["iteration"] == failed_start.data["iteration"]


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


@requires_enforced_permissions
def test_a_failed_requeue_leaves_the_delivered_messages_where_they_were(tmp_path: Path) -> None:
    messages = OperatorMessages(tmp_path)
    messages.queue("T-1", Role.IMPLEMENTER, "first guidance", "c1")
    turn_dir = turns_dir(tmp_path, "T-1") / "01-implement-i1"
    messages.deliver("T-1", Role.IMPLEMENTER, turn_dir)
    queued = messages.queue("T-1", Role.IMPLEMENTER, "newer guidance", "c2")
    queue_dir = queued.parent
    queue_dir.chmod(_READ_ONLY_DIRECTORY)
    try:
        with pytest.raises(PermissionError):
            messages.requeue("T-1", Role.IMPLEMENTER, turn_dir)
    finally:
        queue_dir.chmod(_WRITABLE_DIRECTORY)

    assert [path.read_text() for path in (turn_dir / "messages").iterdir()] == ["first guidance"]

    messages.requeue("T-1", Role.IMPLEMENTER, turn_dir)

    restored = sorted(queue_dir.iterdir())
    assert [path.read_text() for path in restored] == ["first guidance", "newer guidance"]
    assert [path.name for path in restored] == ["001-c1.md", "002-c2.md"]


async def test_messages_delivered_before_a_failed_prompt_render_reach_the_next_turn(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    OperatorMessages(engine_env.project_root).queue(
        task.id, Role.IMPLEMENTER, "use the helper", "c1"
    )
    override = engine_env.project_root / PROMPT_OVERRIDES_RELATIVE_PATH / "implement.md.j2"
    override.parent.mkdir(parents=True)
    override.write_text("{{ undefined_variable }}")
    flow_env = build_flow(engine_env, flow_settings())
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)
    override.unlink()
    failed = engine_env.tasks.require(task.id)
    engine_env.tasks.apply_transition(
        task.id, Transition.stay(failed, step=Step.IMPLEMENT, status=Status.RUNNING)
    )

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    turns = run_dir(engine_env.project_root, task.id) / "turns"
    *_, replacement = sorted(path for path in turns.iterdir() if "-implement-i1" in path.name)
    [delivered] = (replacement / "messages").iterdir()
    assert delivered.read_text() == "use the helper"
    assert str(delivered) in (replacement / "prompt.md").read_text()
