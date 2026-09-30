from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.commands import SetRung
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Step
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.quotas import QuotaStore
from shikiru.workspace.roots import run_dir
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import (
    apply_set_rung,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60


async def _drive(flow_task: asyncio.Task[None], clock: FakeClock) -> None:
    def done() -> bool:
        if clock.has_pending_sleep():
            clock.advance(1)
        return flow_task.done()

    await wait_until(done, timeout=_FLOW_TIMEOUT_SECONDS)
    await flow_task


def _rung_settings(rung: Rung) -> Settings:
    settings = flow_settings()
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, rung]})
    roles = settings.roles.model_copy(update={"implementer": implementer})
    agents = dict(settings.agents)
    agents.setdefault(FAKE_AGENT_ALIAS, AgentConfig())
    return settings.model_copy(update={"roles": roles, "agents": agents})


async def _start_flow(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    *,
    settings: Settings,
    script: list[dict[str, object]],
    clock: FakeClock,
    mode: Mode = Mode.IMPLEMENT,
) -> tuple[asyncio.Task[None], str]:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=mode)
    role = Role.LEADER if mode is Mode.PLAN else Role.IMPLEMENTER
    await set_role_script(tmux_server, role, script)
    flow_env = build_flow(
        engine_env,
        settings,
        clock=clock,
        turn_clock=FakeClock(TASK_CREATED_AT),
    )
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))

    async def session_started() -> bool:
        run = engine_env.runs.get_live(task.id, role)
        return run is not None and run.session_id is not None

    await wait_until(session_started, timeout=10)
    return flow_task, task.id


async def test_quota_failure_blocks_then_continues_on_the_same_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(minutes=1)).isoformat(),
            }
        ],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    events = build_flow(engine_env, settings, clock=clock).events.since(0, task_id=task_id)
    kinds = [event.kind for event in events]
    assert EventKind.QUOTA_BLOCKED in kinds
    assert EventKind.QUOTA_CLEARED in kinds
    assert QuotaStore(engine_env.connection).block_for(FAKE_RUNG.agent) is None
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert run.session_id is not None
    turn_dirs = run_dir(engine_env.project_root, task_id) / "turns"
    continue_dirs = [
        path
        for path in turn_dirs.iterdir()
        if path.is_dir() and "Continue this task's current turn" in (path / "prompt.md").read_text()
    ]
    assert len(continue_dirs) == 1


async def test_quota_wait_deadline_moves_to_a_different_agent(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None))
    timeouts = settings.orchestration.timeouts.model_copy(update={"max_quota_wait": 120})
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts})}
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(seconds=30)).isoformat(),
            }
        ],
    )
    quota = QuotaStore(engine_env.connection)
    await wait_until(lambda: quota.block_for(FAKE_RUNG.agent) is not None, timeout=10)
    extended_until = TASK_CREATED_AT + timedelta(minutes=5)
    quota.block(FAKE_RUNG.agent, extended_until, "another task")
    deadline = TASK_CREATED_AT + timedelta(seconds=120)

    async def advance_to_deadline() -> bool:
        if clock.now() >= deadline or flow_task.done():
            return True
        if clock.has_pending_sleep():
            clock.advance(1)
        return clock.now() >= deadline or flow_task.done()

    await wait_until(advance_to_deadline, timeout=10)
    await wait_until(
        lambda: (
            len([run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER])
            == 2
        ),
        timeout=10,
    )
    await _drive(flow_task, clock)

    runs = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert [run.agent for run in runs] == [FAKE_AGENT_ALIAS, FAKE_AGENT_NAME]
    assert runs[0].session_id != runs[1].session_id
    assert quota.block_for(FAKE_AGENT_NAME) is not None


async def test_unavailable_failure_uses_backoff_then_resumes_the_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "unavailable"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    rows = [
        json.loads(line)
        for line in (
            role_dir(run_dir(engine_env.project_root, task_id), Role.IMPLEMENTER.value)
            / "view.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    starts = [row["session_id"] for row in rows if row["signal"] == "SessionStarted"]
    assert starts == [run.session_id, run.session_id]


async def test_auth_failure_escalates_with_the_agent_auth_reason(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "auth"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_AUTH
    assert await asyncio.to_thread(Path(escalation[0].packet_path).is_file)


async def test_other_failure_retries_once_on_the_same_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "other"}],
    )

    await _drive(flow_task, clock)

    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert (
        len(
            [
                row
                for row in (
                    role_dir(run_dir(engine_env.project_root, task_id), Role.IMPLEMENTER.value)
                    / "view.jsonl"
                )
                .read_text()
                .splitlines()
                if json.loads(row)["signal"] == "SessionStarted"
            ]
        )
        == 2
    )


async def test_unknown_failure_with_high_confidence_retries(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"kind": "other", "confidence": 0.95}]}
                )
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "crash"}, {"kind": "accept"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    events = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert events[0].data["accepted"] is True
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert run.session_id is not None


async def test_unknown_failure_below_confidence_escalates_without_retry(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"kind": "other", "confidence": 0.79}]}
                )
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "crash"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_ERROR
    helper_events = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert helper_events[0].data["accepted"] is False
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 0


async def test_unrecoverable_report_escalates_as_agent_protocol(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(options={"asks": [{"unrecoverable": "no report"}]})
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "no_report"}, {"kind": "no_report"}, {"kind": "no_report"}],
        mode=Mode.PLAN,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_PROTOCOL
    [helper_event] = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert helper_event.data == {"role": Role.LEADER.value, "reason": "no report"}


async def test_a_pause_set_during_a_quota_wait_stops_the_continue_turn_until_resumed(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=flow_settings(),
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(minutes=1)).isoformat(),
            }
        ],
    )
    quota = QuotaStore(engine_env.connection)
    await wait_until(lambda: quota.block_for(FAKE_RUNG.agent) is not None, timeout=10)
    await wait_until(clock.has_pending_sleep, timeout=10)
    engine_env.tasks.set_holds(task_id, frozenset({Hold.PAUSED}))
    turns_before = len(list((run_dir(engine_env.project_root, task_id) / "turns").iterdir()))

    await _drive(flow_task, clock)

    held = engine_env.tasks.get(task_id)
    assert held is not None
    assert held.status is Status.RUNNING
    assert (
        len(list((run_dir(engine_env.project_root, task_id) / "turns").iterdir())) == turns_before
    )
    engine_env.tasks.set_holds(task_id, frozenset())
    flow_env = build_flow(engine_env, flow_settings(), clock=clock)
    resumed = asyncio.create_task(flow_env.flow.run(task_id))
    await _drive(resumed, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.INTEGRATING


_PINNED_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-pinned", effort=None)
_FAR_RESET = TASK_CREATED_AT + timedelta(hours=2)
_UNAVAILABLE = {"kind": "fail", "failure_kind": "unavailable"}
_RUNG_MOVE_CASES = {
    "up_a_rung": (
        [_UNAVAILABLE, _UNAVAILABLE, _UNAVAILABLE, {**_UNAVAILABLE, "release_fifo": "{fifo}"}],
        FAKE_AGENT_NAME,
    ),
    "to_a_different_agent": (
        [
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": _FAR_RESET.isoformat(),
                "release_fifo": "{fifo}",
            }
        ],
        FAKE_AGENT_ALIAS,
    ),
}


@pytest.mark.parametrize("pinned_role", [Role.LEADER, Role.IMPLEMENTER])
@pytest.mark.parametrize(
    ("failures", "moved_agent"), _RUNG_MOVE_CASES.values(), ids=_RUNG_MOVE_CASES.keys()
)
async def test_a_rung_set_during_a_failing_turn_survives_the_recovery_move(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    tmp_path: Path,
    *,
    failures: list[dict[str, object]],
    moved_agent: str,
    pinned_role: Role,
) -> None:
    release = tmp_path / "release-failure"
    os.mkfifo(release)
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=moved_agent, model="fake-2", effort=None))
    timeouts = settings.orchestration.timeouts.model_copy(update={"max_quota_wait": 120})
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts})}
    )
    script = [
        {key: str(release) if value == "{fifo}" else value for key, value in step.items()}
        for step in failures
    ]
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[*script, {"kind": "accept"}],
    )

    flow_env = build_flow(engine_env, settings, clock=clock)

    def gated_turn_running() -> bool:
        if clock.has_pending_sleep():
            clock.advance(1)
        started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
        return len(started) == len(failures)

    await wait_until(gated_turn_running, timeout=20)
    await apply_set_rung(
        engine_env,
        flow_env,
        SetRung(task_id=task_id, role=pinned_role, rung=_PINNED_RUNG),
    )
    await asyncio.to_thread(release.write_text, "go")
    await _drive(flow_task, clock)

    final = engine_env.tasks.require(task_id)
    assert final.overrides == {pinned_role: _PINNED_RUNG}
    assert final.rung_index[Role.IMPLEMENTER] == 1
