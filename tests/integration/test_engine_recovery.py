"""TaskFlow recovery actions against the fake agent (03 §7)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung, role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.events import EventKind
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME, FACTS_FILENAME, PROMPT_FILENAME
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from shikiru.store.quotas import QuotaStore
from shikiru.workspace.roots import run_dir
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer


async def _drive(task: asyncio.Task[None], clock: FakeClock) -> None:
    def done() -> bool:
        if clock.has_pending_sleep():
            clock.advance(1)
        return task.done()

    await wait_until(done, timeout=30)
    await task


def _short_stall(settings: Settings, *, seconds: int = 30) -> Settings:
    timeouts = settings.orchestration.timeouts.model_copy(update={"stall": seconds})
    orchestration = settings.orchestration.model_copy(update={"timeouts": timeouts})
    return settings.model_copy(update={"orchestration": orchestration})


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
    flow = build_flow(engine_env, settings, clock=clock)
    flow_task = asyncio.create_task(flow.flow.run(task.id))

    def session_started() -> bool:
        run = engine_env.runs.get_live(task.id, role)
        return run is not None and run.session_id is not None

    await wait_until(session_started, timeout=10)
    return flow_task, task.id


async def test_quota_failure_blocks_then_continues_the_same_turn(
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
    implementer_run = next(
        run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER
    )
    assert implementer_run.usage.input_tokens == 120


async def test_initial_fresh_turn_does_not_collect_handoff_facts(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "accept"}],
        mode=Mode.PLAN,
    )

    await _drive(flow_task, clock)

    current_turn = role_dir(run_dir(engine_env.project_root, task_id), Role.LEADER.value)
    assert not (current_turn / CURRENT_TURN_LINK_NAME / FACTS_FILENAME).exists()


async def test_unavailable_failure_uses_the_first_backoff_then_retries(
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
    runs = engine_env.runs.for_task(task_id)
    assert {run.role for run in runs} == {Role.IMPLEMENTER, Role.LEADER}
    implementer_run = next(run for run in runs if run.role is Role.IMPLEMENTER)
    assert implementer_run.usage.input_tokens == 120


async def test_context_failure_starts_a_fresh_handoff_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "context"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    runs = engine_env.runs.for_task(task_id)
    implementer_runs = [run for run in runs if run.role is Role.IMPLEMENTER]
    assert len(implementer_runs) == 2
    assert implementer_runs[0].session_id != implementer_runs[1].session_id
    assert implementer_runs[0].usage.input_tokens == 120


async def test_auth_failure_raises_a_recovery_escalation(
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
    escalation = build_flow(engine_env, settings, clock=clock).escalations.list_open()
    assert escalation[0].reason is EscalationReason.AGENT_AUTH
    packet = Path(escalation[0].packet_path)
    assert "## Evidence" in await asyncio.to_thread(packet.read_text)


async def test_other_failure_retries_the_same_turn_once(
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

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    implementer_run = next(
        run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER
    )
    assert implementer_run.usage.input_tokens == 120


async def test_pane_death_relaunches_with_the_same_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
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
    assert final.step is Step.INTEGRATE
    implementer_runs = [
        run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER
    ]
    assert len(implementer_runs) == 2
    assert implementer_runs[0].session_id == implementer_runs[1].session_id
    assert implementer_runs[0].usage.input_tokens == 120


async def test_pane_death_moves_to_the_next_same_agent_rung_after_relaunches(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort=None))
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "crash"}, {"kind": "crash"}, {"kind": "crash"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    implementer_runs = [
        run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER
    ]
    assert len(implementer_runs) == 4
    assert {run.session_id for run in implementer_runs} == {implementer_runs[0].session_id}
    assert implementer_runs[0].model == "fake-2"
    assert implementer_runs[0].usage.input_tokens == 120


async def test_quota_exhaustion_moves_to_a_new_agent_with_a_handoff_prompt(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None))
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(days=1)).isoformat(),
            }
        ],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    implementer_runs = [
        run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER
    ]
    assert len(implementer_runs) == 2
    assert implementer_runs[0].agent == FAKE_AGENT_ALIAS
    assert implementer_runs[0].session_id != implementer_runs[1].session_id
    assert implementer_runs[0].usage.input_tokens == 120
    handoff_prompt = (
        role_dir(run_dir(engine_env.project_root, task_id), Role.IMPLEMENTER.value)
        / CURRENT_TURN_LINK_NAME
        / PROMPT_FILENAME
    ).read_text()
    assert "fresh session" in handoff_prompt
    assert QuotaStore(engine_env.connection).block_for(FAKE_AGENT_NAME) is not None


async def test_protocol_error_raises_a_recovery_escalation(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _short_stall(
        flow_settings().model_copy(
            update={
                "agents": {"fake": AgentConfig(options={"asks": [{"unrecoverable": "no report"}]})}
            }
        )
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
    escalation = build_flow(engine_env, settings, clock=clock).escalations.list_open()
    assert escalation[0].reason is EscalationReason.AGENT_PROTOCOL


async def test_dialog_stall_sends_only_helper_approved_keys_and_resumes(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _short_stall(
        flow_settings().model_copy(
            update={
                "agents": {
                    "fake": AgentConfig(options={"asks": [{"state": "dialog", "keys": ["Enter"]}]})
                }
            }
        )
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "dialog", "message": "confirm"}],
        mode=Mode.PLAN,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    events = build_flow(engine_env, settings, clock=clock).events.since(0, task_id=task_id)
    helper_events = [event for event in events if event.kind is EventKind.HELPER_ACTION]
    assert helper_events[0].data["state"] == "dialog"
    leader_run = next(run for run in engine_env.runs.for_task(task_id) if run.role is Role.LEADER)
    assert leader_run.usage.input_tokens == 120


async def test_leader_question_stall_escalates_as_decision_required(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _short_stall(
        flow_settings().model_copy(
            update={
                "agents": {
                    "fake": AgentConfig(options={"asks": [{"state": "question", "text": "choose"}]})
                }
            }
        )
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        script=[{"kind": "dialog", "message": "question"}],
        clock=clock,
        mode=Mode.PLAN,
    )
    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = build_flow(engine_env, settings, clock=clock).escalations.list_open()
    assert escalation[0].reason is EscalationReason.DECISION_REQUIRED


async def test_working_stalls_rearm_until_agent_stuck(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _short_stall(
        flow_settings().model_copy(
            update={
                "agents": {
                    "fake": AgentConfig(
                        options={"asks": [{"state": "working"}, {"state": "working"}]}
                    )
                }
            }
        )
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        script=[{"kind": "stall"}],
        clock=clock,
        mode=Mode.PLAN,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = build_flow(engine_env, settings, clock=clock).escalations.list_open()
    assert escalation[0].reason is EscalationReason.AGENT_STUCK
    events = build_flow(engine_env, settings, clock=clock).events.since(0, task_id=task_id)
    helper_events = [event for event in events if event.kind is EventKind.HELPER_ACTION]
    assert len(helper_events) == 2


async def test_implementer_question_stall_consults_then_returns_to_implement(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _short_stall(
        flow_settings().model_copy(
            update={
                "agents": {
                    "fake": AgentConfig(options={"asks": [{"state": "question", "text": "choose"}]})
                }
            }
        ),
        seconds=120,
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        script=[{"kind": "dialog", "message": "question"}],
        clock=clock,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    runs = engine_env.runs.for_task(task_id)
    assert len([run for run in runs if run.role is Role.IMPLEMENTER]) == 1
    leader_run = next(run for run in runs if run.role is Role.LEADER)
    assert leader_run.usage.input_tokens >= 240
    events = build_flow(engine_env, settings, clock=clock).events.since(0, task_id=task_id)
    helper_events = [event for event in events if event.kind is EventKind.HELPER_ACTION]
    assert helper_events[0].data["state"] == "question"
