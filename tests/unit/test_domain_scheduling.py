from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.scheduling import (
    SchedulingGates,
    TaskAgents,
    WaitReason,
    find_dependency_cycle,
    occupied_slot_ids,
    pick_ready,
)
from shikiru.domain.tasks import Hold, Status
from tests.support.factories import make_task

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_AGENTS = TaskAgents(leader="claude", implementer="codex")


def test_occupied_slots_only_include_distinct_running_and_integrating_slots() -> None:
    tasks = [
        make_task(id="T-1", seq=1, status=Status.RUNNING, slot=0),
        make_task(id="T-2", seq=2, status=Status.INTEGRATING, slot=1),
        make_task(id="T-3", seq=3, status=Status.ESCALATED, slot=2),
        make_task(id="T-4", seq=4, status=Status.RUNNING, slot=1),
        make_task(id="T-5", seq=5, status=Status.RUNNING),
        make_task(id="T-6", seq=6, status=Status.QUEUED, slot=3),
    ]

    assert occupied_slot_ids(tasks) == frozenset({0, 1})


def _agents_for(*task_ids: str) -> dict[str, TaskAgents]:
    return dict.fromkeys(task_ids, _DEFAULT_AGENTS)


def _gates(
    *,
    free_slots: frozenset[int] = frozenset({0, 1}),
    resources_ok: bool = True,
    blocked_agents: frozenset[str] = frozenset(),
    agents: Mapping[str, TaskAgents] | None = None,
) -> SchedulingGates:
    return SchedulingGates(
        free_slots=free_slots,
        resources_ok=resources_ok,
        blocked_agents=blocked_agents,
        agents=agents if agents is not None else {},
    )


@pytest.mark.parametrize("hold", list(Hold))
def test_only_queued_unheld_tasks_with_finished_dependencies_are_ready(hold: Hold) -> None:
    queued = make_task(id="T-1", seq=1, status=Status.QUEUED)
    running = make_task(id="T-2", seq=2, status=Status.RUNNING)
    held = make_task(id="T-3", seq=3, status=Status.QUEUED, holds=frozenset({hold}))
    waiting = make_task(id="T-4", seq=4, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([queued, running, held, waiting], _gates(agents=_agents_for("T-1")))

    assert [d.task_id for d in schedule.dispatches] == ["T-1"]
    assert schedule.wait_reasons["T-3"] is WaitReason.PAUSED
    assert schedule.wait_reasons["T-4"] is WaitReason.DEPENDENCY


def test_a_task_becomes_ready_once_its_dependency_is_done() -> None:
    done = make_task(id="T-1", seq=1, status=Status.DONE)
    dependent = make_task(id="T-2", seq=2, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([done, dependent], _gates(agents=_agents_for("T-2")))

    assert [d.task_id for d in schedule.dispatches] == ["T-2"]


def test_a_cancelled_dependency_is_reported_and_excludes_the_task() -> None:
    cancelled = make_task(id="T-1", seq=1, status=Status.CANCELLED)
    dependent = make_task(id="T-2", seq=2, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([cancelled, dependent], _gates())

    assert schedule.dependency_cancelled == ("T-2",)
    assert schedule.dispatches == ()


def test_an_unknown_dependency_id_fails_loudly_instead_of_being_ignored() -> None:
    dependent = make_task(id="T-2", seq=2, status=Status.QUEUED, depends_on=["T-does-not-exist"])

    with pytest.raises(KeyError):
        pick_ready([dependent], _gates())


def test_a_ready_task_missing_from_gates_agents_fails_loudly() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)

    with pytest.raises(KeyError):
        pick_ready([task], _gates(agents={}))


def test_requeued_tasks_dispatch_before_others_regardless_of_priority() -> None:
    high_priority = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=10)
    requeued = make_task(
        id="T-2",
        seq=2,
        status=Status.QUEUED,
        priority=0,
        requeued_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    schedule = pick_ready([high_priority, requeued], _gates(agents=_agents_for("T-1", "T-2")))

    assert [d.task_id for d in schedule.dispatches] == ["T-2", "T-1"]


def test_ties_break_by_priority_then_by_seq() -> None:
    low_priority_first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=1)
    high_priority = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=5)
    same_priority_later = make_task(id="T-3", seq=3, status=Status.QUEUED, priority=1)

    schedule = pick_ready(
        [low_priority_first, high_priority, same_priority_later],
        _gates(free_slots=frozenset({0, 1, 2}), agents=_agents_for("T-1", "T-2", "T-3")),
    )

    assert [d.task_id for d in schedule.dispatches] == ["T-2", "T-1", "T-3"]


def test_the_slot_gate_stops_dispatch_and_shares_its_reason_with_later_tasks() -> None:
    first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=2)
    second = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=1)

    schedule = pick_ready([first, second], _gates(free_slots=frozenset()))

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": WaitReason.SLOT, "T-2": WaitReason.SLOT}


def test_the_resources_gate_stops_dispatch_before_any_slot_is_used() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)

    schedule = pick_ready([task], _gates(resources_ok=False))

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": WaitReason.RESOURCES}


def test_the_quota_gate_blocks_a_task_whose_leader_agent_is_blocked() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)
    gates = _gates(
        blocked_agents=frozenset({"claude"}),
        agents={"T-1": TaskAgents(leader="claude", implementer="codex")},
    )

    schedule = pick_ready([task], gates)

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": WaitReason.QUOTA}


def test_a_quota_blocked_task_does_not_stop_a_later_task_on_a_different_agent() -> None:
    blocked = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=2)
    other_agent = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=1)
    gates = _gates(
        blocked_agents=frozenset({"claude"}),
        agents={
            "T-1": TaskAgents(leader="claude", implementer="codex"),
            "T-2": TaskAgents(leader="codex", implementer="codex"),
        },
    )

    schedule = pick_ready([blocked, other_agent], gates)

    assert [d.task_id for d in schedule.dispatches] == ["T-2"]
    assert schedule.wait_reasons["T-1"] is WaitReason.QUOTA
    assert "T-2" not in schedule.wait_reasons


def test_dispatch_prefers_the_tasks_previous_slot_when_free() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED, slot=1)

    schedule = pick_ready([task], _gates(free_slots=frozenset({0, 1}), agents=_agents_for("T-1")))

    assert schedule.dispatches[0].slot == 1


def test_dispatch_falls_back_to_the_lowest_free_slot() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED, slot=5)

    schedule = pick_ready([task], _gates(free_slots=frozenset({0, 2}), agents=_agents_for("T-1")))

    assert schedule.dispatches[0].slot == 0


def test_dispatch_consumes_slots_across_multiple_tasks() -> None:
    first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=2)
    second = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=1)

    schedule = pick_ready(
        [first, second],
        _gates(free_slots=frozenset({0}), agents=_agents_for("T-1", "T-2")),
    )

    assert [d.task_id for d in schedule.dispatches] == ["T-1"]
    assert schedule.wait_reasons == {"T-2": WaitReason.SLOT}


@pytest.mark.parametrize(
    ("graph", "cycle_ids"),
    [
        pytest.param({"T-1": ["T-1"]}, {"T-1"}, id="self-edge"),
        pytest.param({"T-2": ["T-3"], "T-3": ["T-2"]}, {"T-2", "T-3"}, id="sibling-cycle"),
        pytest.param(
            {"T-1": ["T-4"], "T-4": ["T-2"], "T-2": ["T-1"], "T-5": []},
            {"T-1", "T-2", "T-4"},
            id="repoint-induced-cycle",
        ),
        pytest.param(
            {"T-1": [], "T-2": ["T-1"], "T-3": ["T-1", "T-2"]}, frozenset[str](), id="acyclic"
        ),
        pytest.param(
            {"T-2": ["T-3", "T-1"], "T-3": ["T-1"], "T-1": []},
            frozenset[str](),
            id="forward-reference",
        ),
        pytest.param(
            {"T-1": [], "T-2": ["T-1", "T-3"], "T-3": ["T-1", "T-2"]},
            {"T-2", "T-3"},
            id="cycle-beside-implicit-parent-edge",
        ),
    ],
)
def test_dependency_cycle_names_the_tasks_that_wait_on_each_other(
    graph: Mapping[str, list[str]], cycle_ids: frozenset[str] | set[str]
) -> None:
    assert set(find_dependency_cycle(graph)) == cycle_ids
