"""Integration tests for `TaskFlow`: full review loops on the fake agent."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, role_dir
from shikiru.config.settings import AgentConfig, ComplexityLadder
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import options_for, transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.steps import CONSULT_LIMIT, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, StatusReason, Step
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.workspace.git import Git
from shikiru.workspace.roots import run_dir
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.flow import (
    FlowEnv,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)
from tests.support.git_repo import GIT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from shikiru.config.settings import ComplexityName, Settings
    from shikiru.domain.escalation import Escalation
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0
_OPERATOR_DECISION_HEADING = "## Decision from the Operator"
_IMPLEMENTER_QUESTION_HEADING = "## Implementer's question"


async def _run_flow(env: EngineEnv, task_id: str, settings: Settings) -> FlowEnv:
    flow_env = build_flow(env, settings)
    await asyncio.wait_for(flow_env.flow.run(task_id), timeout=_TIMEOUT_SECONDS)
    return flow_env


def _consult_prompt_text(env: EngineEnv, task_id: str) -> str:
    consult_prompts = sorted(
        (run_dir(env.project_root, task_id) / "turns").glob("*-consult-*/prompt.md")
    )
    return consult_prompts[-1].read_text()


def _section(prompt_text: str, heading: str) -> str:
    start = prompt_text.find(heading)
    if start == -1:
        return ""
    body = prompt_text[start + len(heading) :]
    end = body.find("\n## ")
    return body if end == -1 else body[:end]


def _apply_resolution(
    flow_env: FlowEnv, escalation: Escalation, resolution: ResolutionKind, text: str
) -> None:
    task = flow_env.env.tasks.require(escalation.task_id)
    transition = transition_for_resolution(
        task, escalation.reason, step=escalation.step, resolution=resolution
    )
    flow_env.escalations.resolve(escalation.id, text)
    flow_env.env.tasks.apply_transition(
        task.id, dataclasses.replace(transition, status=Status.RUNNING)
    )


def _answer_escalation(flow_env: FlowEnv, escalation: Escalation, answer: str) -> None:
    _apply_resolution(
        flow_env, escalation, ResolutionKind.ANSWER, f"{ResolutionKind.ANSWER.value} {answer}"
    )


def _retry_escalation(flow_env: FlowEnv, escalation: Escalation) -> None:
    _apply_resolution(flow_env, escalation, ResolutionKind.RETRY, ResolutionKind.RETRY.value)


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

    prompt_text = _consult_prompt_text(engine_env, task.id)
    assert answer in _section(prompt_text, _OPERATOR_DECISION_HEADING)
    assert _IMPLEMENTER_QUESTION_HEADING not in prompt_text


async def test_consult_caused_by_the_implementer_alone_has_no_operator_section(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env)
    question = "Should the retry preserve the original input?"
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "needs_decision", "question": question}, {"kind": "accept"}],
    )

    await _run_flow(engine_env, task.id, settings)

    prompt_text = _consult_prompt_text(engine_env, task.id)
    assert question in _section(prompt_text, _IMPLEMENTER_QUESTION_HEADING)
    assert _OPERATOR_DECISION_HEADING not in prompt_text


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


async def test_fresh_session_consult_keeps_the_operator_decision_and_the_implementer_question(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env)
    question = "Which retry library should I use?"
    implementer_steps: list[dict[str, object]] = [
        {"kind": "needs_decision", "question": question}
    ] * (CONSULT_LIMIT + 1)
    await set_role_script(tmux_server, Role.IMPLEMENTER, implementer_steps)
    escalated = await _run_flow(engine_env, task.id, settings)
    [escalation] = escalated.escalations.list_open()
    leader_run = engine_env.runs.get_live(task.id, Role.LEADER)
    assert leader_run is not None
    engine_env.runs.close(leader_run.id)
    answer = "use tenacity"
    _answer_escalation(escalated, escalation, answer)

    await _run_flow(engine_env, task.id, settings)

    prompt_text = _consult_prompt_text(engine_env, task.id)
    assert "fresh session" in prompt_text
    assert answer in _section(prompt_text, _OPERATOR_DECISION_HEADING)
    assert question in _section(prompt_text, _IMPLEMENTER_QUESTION_HEADING)


async def test_retried_consult_keeps_the_operator_decision(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    before_failure = build_flow(engine_env, settings)
    escalation = before_failure.escalations.open(
        task.id,
        step=Step.IMPLEMENT,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Operator guidance needed",
        packet_path="operator-guidance.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    answer = "keep the parser strict"
    _answer_escalation(before_failure, escalation, answer)
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "fail", "failure_kind": "auth"}])
    failed = await _run_flow(engine_env, task.id, settings)
    [auth_escalation] = failed.escalations.list_open()
    assert auth_escalation.reason is EscalationReason.AGENT_AUTH
    _retry_escalation(failed, auth_escalation)

    await _run_flow(engine_env, task.id, settings)

    prompt_text = _consult_prompt_text(engine_env, task.id)
    assert answer in _section(prompt_text, _OPERATOR_DECISION_HEADING)


async def test_leader_escalation_keeps_the_implementer_question_beside_the_decision(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    question = "Should the retry preserve the original input?"
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "needs_decision", "question": question}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "escalate", "question": "Which retry budget does the Operator want?"}],
    )
    escalated = await _run_flow(engine_env, task.id, settings)
    [escalation] = escalated.escalations.list_open()
    assert escalation.reason is EscalationReason.DECISION_REQUIRED
    answer = "three attempts"
    _answer_escalation(escalated, escalation, answer)

    await _run_flow(engine_env, task.id, settings)

    prompt_text = _consult_prompt_text(engine_env, task.id)
    assert answer in _section(prompt_text, _OPERATOR_DECISION_HEADING)
    assert question in _section(prompt_text, _IMPLEMENTER_QUESTION_HEADING)


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


async def test_verify_rejections_emit_rung_changed_only_on_the_move(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings(escalate_after=2)
    second_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort=None)
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, second_rung]})
    roles = settings.roles.model_copy(update={"implementer": implementer})
    settings = settings.model_copy(update={"roles": roles})
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept"}, {"kind": "accept"}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "changes_requested"}, {"kind": "changes_requested"}, {"kind": "accept"}],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    assert final.step is Step.INTEGRATE
    assert final.iteration == 3
    assert final.rung_index[Role.IMPLEMENTER] == 1
    assert final.counters.rejections_on_rung == 0
    changes = flow_env.events.since(0, task_id=task.id, kinds=[EventKind.RUNG_CHANGED])
    assert len(changes) == 1
    [changed] = changes
    assert changed.data["role"] == Role.IMPLEMENTER.value
    assert changed.data["from"] == FAKE_RUNG.model
    assert changed.data["to"] == second_rung.model
    assert changed.data["reason"] == "changes_requested"
    assert (
        changed.message
        == f"{task.id} implementer moved from {FAKE_RUNG.model} to {second_rung.model}"
    )


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
    prompt = handoff_turn.resolve().joinpath("prompt.md").read_text()
    assert "fresh session" in prompt
    assert "fake-agent" in prompt
    assert "target project's instructions and documentation" in prompt
    assert "Conventional Commits" in prompt
    assert f"Task: {task.id}" in prompt
    assert "every implementation and rework report" in " ".join(prompt.split())


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
    escalation = next(escalation for escalation in fetch_open_escalations(flow_env.env.connection))
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


async def test_a_supersede_verdict_on_a_dirty_worktree_is_sent_back_and_keeps_the_edit(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    replacement = {"title": "redo it properly", "request": "redo"}
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {"kind": "supersede", "new_tasks": [replacement], "uncommitted": "TODO.md"},
            {"kind": "supersede", "new_tasks": [replacement]},
        ],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    verdicts = flow_env.events.since(0, kinds=[EventKind.VERDICT], task_id=task.id)
    assert [event.data["decision"] for event in verdicts] == [
        "supersede",
        "changes_requested",
        "supersede",
    ]
    assert "TODO.md" in verdicts[1].data["issues"][0]["description"]
    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status_reason is StatusReason.SUPERSEDED
    assert final.repo is not None
    committed = await Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT).diff_name_only(
        engine_env.project_root, f"{final.repo.base}...{final.repo.branch}"
    )
    assert "TODO.md" in committed


_HIGH_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-high", effort=None)
_PINNED_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-pinned", effort=None)


def _role_models(flow_env: FlowEnv, task_id: str, role: Role) -> list[str]:
    started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
    return [str(e.data["model"]) for e in started if e.data["role"] == role.value]


def _implementer_models(flow_env: FlowEnv, task_id: str) -> list[str]:
    return _role_models(flow_env, task_id, Role.IMPLEMENTER)


_TRIAGE_COMPLEXITY_CASES = {
    "unset_complexity_takes_the_triaged_one": (None, "high", "fake-high"),
    "explicit_normal_is_kept": ("normal", "normal", "fake-1"),
}


@pytest.mark.parametrize(
    ("submitted", "expected_complexity", "expected_model"),
    _TRIAGE_COMPLEXITY_CASES.values(),
    ids=_TRIAGE_COMPLEXITY_CASES.keys(),
)
async def test_triage_persists_title_and_complexity_for_the_next_turn(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    submitted: ComplexityName | None,
    expected_complexity: ComplexityName,
    expected_model: str,
) -> None:
    settings = flow_settings()
    implementer = settings.roles.implementer.model_copy(
        update={"complexity": {"high": ComplexityLadder(ladder=[_HIGH_RUNG])}}
    )
    settings = settings.model_copy(
        update={"roles": settings.roles.model_copy(update={"implementer": implementer})}
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, complexity=submitted)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "accept", "triage_title": "Triaged title", "triage_complexity": "high"}],
    )

    flow_env = await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.title == "Triaged title"
    assert final.complexity == expected_complexity
    assert _implementer_models(flow_env, task.id) == [expected_model]
    implementer_prompts = sorted(
        (run_dir(engine_env.project_root, task.id) / "turns").glob("*-implement-i1/prompt.md")
    )
    assert len(implementer_prompts) == 1
    implementer_prompt = implementer_prompts[0].read_text()
    assert "fresh session" in implementer_prompt
    assert "target project's instructions and documentation" in implementer_prompt
    assert "Conventional Commits" in implementer_prompt
    assert f"Task: {task.id}" in implementer_prompt
    assert "every implementation and rework report" in " ".join(implementer_prompt.split())
