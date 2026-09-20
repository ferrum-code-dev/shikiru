"""Integration tests for `TaskFlow`: full review loops on the fake agent (03 §2-3)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from tests.support.engine import set_role_script
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


_NEEDS_DECISION_STEP: dict[str, object] = {"kind": "needs_decision"}

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
    "consult_limit_exceeded": _ReviewLoopCase(
        [_NEEDS_DECISION_STEP] * 4, None, Step.CONSULT, Status.ESCALATED
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
    assert {
        "agent",
        "model",
        "outcome",
        "summary",
        "question",
        "usage_delta",
    } <= completed.data.keys()


async def test_a_final_round_still_requesting_changes_raises_iterations_exhausted(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings(max_iterations=1)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "changes_requested"}])

    flow_env = await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert final.step is Step.VERIFY
    matching = [row for row in flow_env.escalations.list_open() if row.task_id == task.id]
    assert matching[0].reason is EscalationReason.ITERATIONS_EXHAUSTED


async def test_a_triage_split_repoints_dependents_and_completes_the_parent(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    parent = await build_flow_task(engine_env, title="parent to split", mode=None)
    dependent = await build_flow_task(engine_env, title="depends on parent", mode=Mode.PLAN)
    engine_env.tasks.set_depends_on(dependent.id, [parent.id])
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {
                "kind": "split",
                "subtasks": [
                    {"title": "part one", "request": "part one"},
                    {"title": "part two", "request": "part two", "depends_on": ["#1"]},
                ],
            }
        ],
    )

    await _run_flow(engine_env, parent.id, settings)

    final = engine_env.tasks.get(parent.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.status_reason == "split"
    children = [t for t in engine_env.tasks.list_all() if t.parent_id == parent.id]
    assert len(children) == 2
    second = next(t for t in children if t.title == "part two")
    first = next(t for t in children if t.title == "part one")
    assert second.depends_on == [first.id]
    dependent_now = engine_env.tasks.get(dependent.id)
    assert dependent_now is not None
    assert set(dependent_now.depends_on) == {child.id for child in children}


async def test_a_submitted_split_mode_runs_triage_and_completes_with_children(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, title="submitted split", mode=Mode.SPLIT)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "split", "subtasks": [{"title": "child", "request": "child"}]}],
    )

    await _run_flow(engine_env, task.id, settings)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.status_reason == "split"
    assert [child.title for child in engine_env.tasks.list_all() if child.parent_id == task.id] == [
        "child"
    ]


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
    assert final.status_reason == "superseded"
    children = [t for t in engine_env.tasks.list_all() if t.parent_id == task.id]
    assert [child.title for child in children] == ["redo it properly"]
