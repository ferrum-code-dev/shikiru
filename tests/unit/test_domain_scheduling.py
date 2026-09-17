from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.domain.scheduling import SchedulingGates, TaskAgents, pick_ready
from shikiru.domain.tasks import Hold, Status
from tests.support.factories import make_task

if TYPE_CHECKING:
    from collections.abc import Mapping

_NO_AGENTS: dict[str, TaskAgents] = {}


def _gates(
    *,
    free_slots: frozenset[int] = frozenset({0, 1}),
    resources_ok: bool = True,
    blocked_agents: frozenset[str] = frozenset(),
    agents: Mapping[str, TaskAgents] = _NO_AGENTS,
) -> SchedulingGates:
    return SchedulingGates(
        free_slots=free_slots,
        resources_ok=resources_ok,
        blocked_agents=blocked_agents,
        agents=agents,
    )


def test_only_queued_unpaused_tasks_with_finished_dependencies_are_ready() -> None:
    queued = make_task(id="T-1", seq=1, status=Status.QUEUED)
    running = make_task(id="T-2", seq=2, status=Status.RUNNING)
    paused = make_task(id="T-3", seq=3, status=Status.QUEUED, holds=frozenset({Hold.PAUSED}))
    waiting = make_task(id="T-4", seq=4, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([queued, running, paused, waiting], _gates())

    assert [d.task_id for d in schedule.dispatches] == ["T-1"]


def test_a_task_becomes_ready_once_its_dependency_is_done() -> None:
    done = make_task(id="T-1", seq=1, status=Status.DONE)
    dependent = make_task(id="T-2", seq=2, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([done, dependent], _gates())

    assert [d.task_id for d in schedule.dispatches] == ["T-2"]


def test_a_cancelled_dependency_is_reported_and_excludes_the_task() -> None:
    cancelled = make_task(id="T-1", seq=1, status=Status.CANCELLED)
    dependent = make_task(id="T-2", seq=2, status=Status.QUEUED, depends_on=["T-1"])

    schedule = pick_ready([cancelled, dependent], _gates())

    assert schedule.dependency_cancelled == ("T-2",)
    assert schedule.dispatches == ()


def test_requeued_tasks_dispatch_before_others_regardless_of_priority() -> None:
    high_priority = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=10)
    requeued = make_task(
        id="T-2",
        seq=2,
        status=Status.QUEUED,
        priority=0,
        requeued_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    schedule = pick_ready([high_priority, requeued], _gates())

    assert [d.task_id for d in schedule.dispatches] == ["T-2", "T-1"]


def test_ties_break_by_priority_then_by_seq() -> None:
    low_priority_first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=1)
    high_priority = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=5)
    same_priority_later = make_task(id="T-3", seq=3, status=Status.QUEUED, priority=1)

    schedule = pick_ready(
        [low_priority_first, high_priority, same_priority_later],
        _gates(free_slots=frozenset({0, 1, 2})),
    )

    assert [d.task_id for d in schedule.dispatches] == ["T-2", "T-1", "T-3"]


def test_the_slot_gate_stops_dispatch_and_shares_its_reason_with_later_tasks() -> None:
    first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=2)
    second = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=1)

    schedule = pick_ready([first, second], _gates(free_slots=frozenset()))

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": "slot", "T-2": "slot"}


def test_the_resources_gate_stops_dispatch_before_any_slot_is_used() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)

    schedule = pick_ready([task], _gates(resources_ok=False))

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": "resources"}


def test_the_quota_gate_blocks_a_task_whose_leader_agent_is_blocked() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)
    gates = _gates(
        blocked_agents=frozenset({"claude"}),
        agents={"T-1": TaskAgents(leader="claude", implementer="codex")},
    )

    schedule = pick_ready([task], gates)

    assert schedule.dispatches == ()
    assert schedule.wait_reasons == {"T-1": "quota"}


def test_the_quota_gate_blocks_a_task_whose_implementer_agent_is_blocked() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)
    gates = _gates(
        blocked_agents=frozenset({"codex"}),
        agents={"T-1": TaskAgents(leader="claude", implementer="codex")},
    )

    schedule = pick_ready([task], gates)

    assert schedule.wait_reasons == {"T-1": "quota"}


def test_a_task_with_no_blocked_agents_dispatches_normally() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED)
    gates = _gates(
        blocked_agents=frozenset({"claude"}),
        agents={"T-1": TaskAgents(leader="codex", implementer="codex")},
    )

    schedule = pick_ready([task], gates)

    assert [d.task_id for d in schedule.dispatches] == ["T-1"]


def test_dispatch_prefers_the_tasks_previous_slot_when_free() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED, slot=1)

    schedule = pick_ready([task], _gates(free_slots=frozenset({0, 1})))

    assert schedule.dispatches[0].slot == 1


def test_dispatch_falls_back_to_the_lowest_free_slot() -> None:
    task = make_task(id="T-1", seq=1, status=Status.QUEUED, slot=5)

    schedule = pick_ready([task], _gates(free_slots=frozenset({0, 2})))

    assert schedule.dispatches[0].slot == 0


def test_dispatch_consumes_slots_across_multiple_tasks() -> None:
    first = make_task(id="T-1", seq=1, status=Status.QUEUED, priority=2)
    second = make_task(id="T-2", seq=2, status=Status.QUEUED, priority=1)

    schedule = pick_ready([first, second], _gates(free_slots=frozenset({0})))

    assert [d.task_id for d in schedule.dispatches] == ["T-1"]
    assert schedule.wait_reasons == {"T-2": "slot"}
