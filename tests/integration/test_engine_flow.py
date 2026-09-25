"""Integration tests for `TaskFlow`: full review loops on the fake agent (03 §2-3)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, StatusReason, Step
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.flow import (
    FlowEnv,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0


async def _run_flow(env: EngineEnv, task_id: str, settings: Settings) -> FlowEnv:
    flow_env = build_flow(env, settings)
    await asyncio.wait_for(flow_env.flow.run(task_id), timeout=_TIMEOUT_SECONDS)
    return flow_env


@dataclass(frozen=True)
class _ReviewLoopCase:
    implementer_steps: list[dict[str, object]] | None
    leader_steps: list[dict[str, object]] | None
    expected_step: Step
    expected_status: Status


_REVIEW_LOOP_CASES = {
    "implement_accept": _ReviewLoopCase(None, None, Step.INTEGRATE, Status.INTEGRATING),
    "changes_requested_then_accept": _ReviewLoopCase(
        None,
        [{"kind": "changes_requested"}, {"kind": "accept"}],
        Step.INTEGRATE,
        Status.INTEGRATING,
    ),
    "needs_decision_then_consult_then_accept": _ReviewLoopCase(
        [{"kind": "needs_decision"}, {"kind": "accept"}],
        None,
        Step.INTEGRATE,
        Status.INTEGRATING,
    ),
}


@pytest.mark.parametrize("case", _REVIEW_LOOP_CASES.values(), ids=_REVIEW_LOOP_CASES.keys())
async def test_review_loop_scenarios(
    engine_env: EngineEnv, tmux_server: TmuxServer, case: _ReviewLoopCase
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    if case.implementer_steps is not None:
        await set_role_script(tmux_server, Role.IMPLEMENTER, case.implementer_steps)
    if case.leader_steps is not None:
        await set_role_script(tmux_server, Role.LEADER, case.leader_steps)

    await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is case.expected_step
    assert final.status is case.expected_status


async def test_flow_events_mark_step_turn_and_verdict_boundaries(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "no_report"}, {"kind": "accept"}],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    events = flow_env.events.since(0, task_id=task.id)
    kinds = [event.kind for event in events]
    assert EventKind.STEP_STARTED in kinds
    assert EventKind.TURN_STARTED in kinds
    assert EventKind.TURN_COMPLETED in kinds
    assert EventKind.VERDICT in kinds
    assert EventKind.NUDGE in kinds
    completed = next(event for event in events if event.kind is EventKind.TURN_COMPLETED)
    usage_delta = completed.data["usage_delta"]
    assert usage_delta["input_tokens"] == 240
    assert usage_delta["output_tokens"] == 120
    assert usage_delta["cache_read_tokens"] == 40
    assert usage_delta["cache_write_tokens"] == 20


async def test_consult_reads_operator_answer_after_flow_restarts(
    engine_env: EngineEnv,
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    before_restart = build_flow(engine_env, settings)
    escalation = before_restart.escalations.open(
        task.id,
        step=Step.IMPLEMENT,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Operator guidance needed",
        packet_path="operator-guidance.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    answer = "keep the parser strict after restart"
    before_restart.escalations.resolve(escalation.id, f"{ResolutionKind.ANSWER.value} {answer}")
    engine_env.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.CONSULT, status=Status.RUNNING)
    )
    after_restart = build_flow(engine_env, settings)

    await asyncio.wait_for(after_restart.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    consult_prompt = next(
        (run_dir(engine_env.project_root, task.id) / "turns").glob("*-consult-*/prompt.md")
    )
    assert answer in consult_prompt.read_text()


async def test_later_consult_uses_implementer_question_after_flow_restarts(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    before_restart = build_flow(engine_env, settings)
    escalation = before_restart.escalations.open(
        task.id,
        step=Step.IMPLEMENT,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Operator guidance needed",
        packet_path="operator-guidance.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    operator_answer = "keep the parser strict after restart"
    before_restart.escalations.resolve(
        escalation.id, f"{ResolutionKind.ANSWER.value} {operator_answer}"
    )
    engine_env.tasks.apply_transition(task.id, first_step(task))
    implementer_question = "Should the retry preserve the original input?"
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [
            {"kind": "needs_decision", "question": implementer_question},
            {"kind": "accept"},
        ],
    )
    after_restart = build_flow(engine_env, settings)

    await asyncio.wait_for(after_restart.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    consult_prompt = next(
        (run_dir(engine_env.project_root, task.id) / "turns").glob("*-consult-*/prompt.md")
    )
    prompt_text = consult_prompt.read_text()
    assert implementer_question in prompt_text
    assert operator_answer not in prompt_text


async def test_changes_requested_persists_iteration_rung_and_counter_resets(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings(escalate_after=1)
    second_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort=None)
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, second_rung]})
    roles = settings.roles.model_copy(update={"implementer": implementer})
    settings = settings.model_copy(update={"roles": roles})
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "changes_requested"}, {"kind": "accept"}],
    )

    await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    assert final.step is Step.INTEGRATE
    assert final.iteration == 2
    assert final.rung_index[Role.IMPLEMENTER] == 1
    assert final.counters.rejections_on_rung == 0
    implementer_runs = [
        run for run in engine_env.runs.for_task(task.id) if run.role is Role.IMPLEMENTER
    ]
    assert len(implementer_runs) == 1
    assert implementer_runs[0].model == "fake-2"
    assert implementer_runs[0].session_id is not None
    view_path = (
        role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value) / "view.jsonl"
    )
    starts = [
        json.loads(line)["session_id"]
        for line in view_path.read_text().splitlines()
        if json.loads(line)["signal"] == "SessionStarted"
    ]
    assert starts == [implementer_runs[0].session_id, implementer_runs[0].session_id]


async def test_different_agent_rung_hands_off_prior_reports_in_a_fresh_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings(escalate_after=1)
    next_rung = Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None)
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, next_rung]})
    roles = settings.roles.model_copy(update={"implementer": implementer})
    agents = dict(settings.agents)
    agents[FAKE_AGENT_ALIAS] = AgentConfig()
    settings = settings.model_copy(update={"roles": roles, "agents": agents})
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "changes_requested"}, {"kind": "accept"}],
    )

    await _run_flow(engine_env, task.id, settings)

    implementer_runs = sorted(
        (run for run in engine_env.runs.for_task(task.id) if run.role is Role.IMPLEMENTER),
        key=lambda run: run.id,
    )
    assert [run.agent for run in implementer_runs] == [FAKE_AGENT_NAME, FAKE_AGENT_ALIAS]
    assert implementer_runs[0].session_id != implementer_runs[1].session_id
    current = role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    handoff_turn = current / "current_turn"
    assert "fresh session" in handoff_turn.resolve().joinpath("prompt.md").read_text()
    assert "fake-agent" in handoff_turn.resolve().joinpath("prompt.md").read_text()


async def test_scope_change_on_an_unknown_task_escalates_without_creating_tasks(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, title="invalid scope", mode=Mode.PLAN)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {
                "kind": "accept",
                "new_tasks": [
                    {
                        "title": "invalid child",
                        "request": "invalid child",
                        "depends_on": ["T-999"],
                    }
                ],
            }
        ],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert final.step is Step.PLAN
    escalation = next(escalation for escalation in flow_env.escalations.list_open())
    assert escalation.reason is EscalationReason.AGENT_PROTOCOL
    assert [child for child in engine_env.tasks.list_all() if child.parent_id == task.id] == []


async def test_a_plan_report_enqueues_its_new_tasks(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.PLAN)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "accept", "new_tasks": [{"title": "execute step one", "request": "do it"}]}],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    children = [t for t in engine_env.tasks.list_all() if t.parent_id == task.id]
    assert [child.title for child in children] == ["execute step one"]
    scope_events = flow_env.events.since(0, kinds=[EventKind.SCOPE_CHANGE], task_id=task.id)
    assert len(scope_events) == 1


async def test_a_supersede_verdict_cancels_the_task_and_enqueues_new_tasks(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "supersede", "new_tasks": [{"title": "redo it properly", "request": "redo"}]}],
    )

    await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.CANCELLED
    assert final.status_reason is StatusReason.SUPERSEDED
    children = [t for t in engine_env.tasks.list_all() if t.parent_id == task.id]
    assert [child.title for child in children] == ["redo it properly"]
