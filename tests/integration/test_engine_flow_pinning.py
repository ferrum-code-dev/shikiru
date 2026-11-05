"""Integration tests for `TaskFlow`: full review loops on the fake agent."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.domain.commands import SetRung
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Counters, Mode, Role, Step
from tests.support.engine import FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.flow import (
    FlowEnv,
    apply_set_rung,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0
_HIGH_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-high", effort=None)


_PINNED_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-pinned", effort=None)


def _role_models(flow_env: FlowEnv, task_id: str, role: Role) -> list[str]:
    started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
    return [str(e.data["model"]) for e in started if e.data["role"] == role.value]


def _implementer_models(flow_env: FlowEnv, task_id: str) -> list[str]:
    return _role_models(flow_env, task_id, Role.IMPLEMENTER)


async def test_a_rung_set_during_a_turn_is_kept_and_used_by_the_next_turn(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    release = tmp_path / "release-implement"
    os.mkfifo(release)
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "release_fifo": str(release)}, {"kind": "accept"}],
    )
    await set_role_script(
        tmux_server, Role.LEADER, [{"kind": "changes_requested"}, {"kind": "accept"}]
    )
    flow_env = build_flow(engine_env, settings)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: bool(_implementer_models(flow_env, task.id)), timeout=30)

    await apply_set_rung(
        engine_env,
        flow_env,
        SetRung(task_id=task.id, role=Role.IMPLEMENTER, rung=_PINNED_RUNG),
    )
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_TIMEOUT_SECONDS)

    assert _implementer_models(flow_env, task.id) == ["fake-1", "fake-pinned"]
    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.overrides[Role.IMPLEMENTER] == _PINNED_RUNG


async def test_a_rung_set_during_verify_survives_the_rejection_that_moves_the_ladder(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    pinned_role = Role.IMPLEMENTER
    release = tmp_path / "release-verify"
    os.mkfifo(release)
    settings = flow_settings(escalate_after=1)
    second_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort=None)
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, second_rung]})
    settings = settings.model_copy(
        update={"roles": settings.roles.model_copy(update={"implementer": implementer})}
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}, {"kind": "accept"}])
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "changes_requested", "release_fifo": str(release)}, {"kind": "accept"}],
    )
    flow_env = build_flow(engine_env, settings)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: bool(_role_models(flow_env, task.id, Role.LEADER)), timeout=30)

    await apply_set_rung(
        engine_env, flow_env, SetRung(task_id=task.id, role=pinned_role, rung=_PINNED_RUNG)
    )
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.require(task.id)
    assert final.overrides == {pinned_role: _PINNED_RUNG}
    assert final.rung_index.get(Role.IMPLEMENTER, 0) == 0
    assert _role_models(flow_env, task.id, pinned_role)[-1] == "fake-pinned"


@dataclass(frozen=True)
class _InFlightPinCase:
    gated_role: Role
    escalate_after: int
    max_iterations: int
    starting_iteration: int
    implementer_steps: list[dict[str, object]]
    leader_steps: list[dict[str, object]]
    implementer_models: list[str]


_GATE: dict[str, object] = {"release_fifo": "{fifo}"}
_ACCEPT: dict[str, object] = {"kind": "accept"}
_REJECT: dict[str, object] = {"kind": "changes_requested"}
_IN_FLIGHT_PIN_CASES = {
    "accepted_implementation": _InFlightPinCase(
        gated_role=Role.IMPLEMENTER,
        escalate_after=5,
        max_iterations=5,
        starting_iteration=0,
        implementer_steps=[{**_ACCEPT, **_GATE}],
        leader_steps=[_ACCEPT],
        implementer_models=["fake-1"],
    ),
    "rejected_verification": _InFlightPinCase(
        gated_role=Role.LEADER,
        escalate_after=5,
        max_iterations=5,
        starting_iteration=0,
        implementer_steps=[_ACCEPT, _ACCEPT],
        leader_steps=[{**_REJECT, **_GATE}, _ACCEPT],
        implementer_models=["fake-1", "fake-pinned"],
    ),
    "rung_escalating_rejection": _InFlightPinCase(
        gated_role=Role.LEADER,
        escalate_after=1,
        max_iterations=5,
        starting_iteration=0,
        implementer_steps=[_ACCEPT, _ACCEPT],
        leader_steps=[{**_REJECT, **_GATE}, _ACCEPT],
        implementer_models=["fake-1", "fake-pinned"],
    ),
    "iterations_exhausted_escalation": _InFlightPinCase(
        gated_role=Role.LEADER,
        escalate_after=5,
        max_iterations=1,
        starting_iteration=1,
        implementer_steps=[_ACCEPT],
        leader_steps=[{**_REJECT, **_GATE}],
        implementer_models=["fake-1"],
    ),
}


@pytest.mark.parametrize("case", _IN_FLIGHT_PIN_CASES.values(), ids=_IN_FLIGHT_PIN_CASES.keys())
async def test_a_rung_set_during_a_turn_is_never_undone_by_the_turn_completing(
    engine_env: EngineEnv, tmux_server: TmuxServer, tmp_path: Path, case: _InFlightPinCase
) -> None:
    release = tmp_path / "release-turn"
    os.mkfifo(release)
    settings = flow_settings(escalate_after=case.escalate_after, max_iterations=case.max_iterations)
    second_rung = Rung(agent=FAKE_AGENT_NAME, model="fake-2", effort=None)
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, second_rung]})
    settings = settings.model_copy(
        update={"roles": settings.roles.model_copy(update={"implementer": implementer})}
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    engine_env.tasks.apply_transition(
        task.id,
        dataclasses.replace(
            Transition.stay(task, step=Step.IMPLEMENT),
            iteration=case.starting_iteration,
            counters=Counters(rejections_on_rung=1),
        ),
    )

    def gated(steps: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {key: str(release) if value == "{fifo}" else value for key, value in step.items()}
            for step in steps
        ]

    await set_role_script(tmux_server, Role.IMPLEMENTER, gated(case.implementer_steps))
    await set_role_script(tmux_server, Role.LEADER, gated(case.leader_steps))
    flow_env = build_flow(engine_env, settings)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    await wait_until(lambda: bool(_role_models(flow_env, task.id, case.gated_role)), timeout=30)

    await apply_set_rung(
        engine_env, flow_env, SetRung(task_id=task.id, role=Role.IMPLEMENTER, rung=_PINNED_RUNG)
    )
    await asyncio.to_thread(release.write_text, "go")
    await asyncio.wait_for(flow_task, timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.require(task.id)
    assert final.overrides == {Role.IMPLEMENTER: _PINNED_RUNG}
    assert final.counters.rejections_on_rung == 0
    assert final.rung_index.get(Role.IMPLEMENTER, 0) == 0
    assert _implementer_models(flow_env, task.id) == case.implementer_models


_REJECTIONS_BEFORE_PIN = 3
_SET_RUNG_COUNTER_CASES = {
    "new_implementer_pin_resets_the_count": (Role.IMPLEMENTER, _HIGH_RUNG, 0),
    "leader_pin_keeps_the_count": (Role.LEADER, _HIGH_RUNG, _REJECTIONS_BEFORE_PIN),
    "repeating_the_implementer_pin_keeps_the_count": (
        Role.IMPLEMENTER,
        _PINNED_RUNG,
        _REJECTIONS_BEFORE_PIN,
    ),
}


@pytest.mark.parametrize(
    ("role", "rung", "expected"),
    _SET_RUNG_COUNTER_CASES.values(),
    ids=_SET_RUNG_COUNTER_CASES.keys(),
)
async def test_set_rung_resets_rejections_only_when_the_implementer_rung_changes(
    engine_env: EngineEnv, tmux_server: TmuxServer, role: Role, rung: Rung, expected: int
) -> None:
    del tmux_server
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    flow_env = build_flow(engine_env, flow_settings())
    counters = task.counters.model_copy(update={"rejections_on_rung": _REJECTIONS_BEFORE_PIN})
    engine_env.tasks.apply_transition(
        task.id,
        dataclasses.replace(
            Transition.stay(task, step=Step.IMPLEMENT),
            counters=counters,
            set_overrides={Role.IMPLEMENTER: _PINNED_RUNG},
        ),
    )

    await apply_set_rung(engine_env, flow_env, SetRung(task_id=task.id, role=role, rung=rung))

    assert engine_env.tasks.require(task.id).counters.rejections_on_rung == expected
