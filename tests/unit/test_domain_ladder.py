from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityLadder
from shikiru.domain.ladder import (
    move_up,
    pick_rung,
    reset_for_integration_attempt,
    reset_for_new_iteration,
    reset_for_rung_change,
)
from shikiru.domain.tasks import Counters, Role
from tests.support.factories import (
    IMPLEMENTER_RUNGS,
    LEADER_RUNG_2,
    make_settings,
    make_task,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_SETTINGS = make_settings()


def test_pick_rung_uses_the_ladder_at_rung_index_by_default() -> None:
    task = make_task(rung_index={Role.LEADER: 1})

    assert pick_rung(task, Role.LEADER, _SETTINGS) == LEADER_RUNG_2


def test_pick_rung_prefers_an_operator_override() -> None:
    override = Rung(agent="claude", model="haiku", effort=None)
    task = make_task(overrides={Role.LEADER: override})

    assert pick_rung(task, Role.LEADER, _SETTINGS) == override


def test_pick_rung_resolves_the_implementer_ladder_by_complexity() -> None:
    settings = make_settings(
        implementer_complexity={"high": ComplexityLadder(ladder=[IMPLEMENTER_RUNGS[1]])}
    )
    task = make_task(complexity="high")

    assert pick_rung(task, Role.IMPLEMENTER, settings) == IMPLEMENTER_RUNGS[1]


def test_pick_rung_resolves_the_helper_ladder() -> None:
    task = make_task()

    rung = pick_rung(task, Role.HELPER, _SETTINGS)

    assert rung.agent == "claude"
    assert rung.model == "haiku"


def test_move_up_returns_the_following_rung() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: 0})

    move = move_up(task, Role.IMPLEMENTER, _SETTINGS)

    assert move is not None
    assert move.rung_index[Role.IMPLEMENTER] == 1


def test_move_up_returns_none_when_the_ladder_is_exhausted() -> None:
    task = make_task(rung_index={Role.LEADER: 1})

    assert move_up(task, Role.LEADER, _SETTINGS) is None


def test_move_up_with_different_agent_skips_same_agent_rungs() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: 0})

    move = move_up(task, Role.IMPLEMENTER, _SETTINGS, different_agent=True)

    assert move is not None
    assert move.rung_index[Role.IMPLEMENTER] == 2


def test_move_up_with_different_agent_returns_none_when_none_remain() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: len(IMPLEMENTER_RUNGS) - 1})

    assert move_up(task, Role.IMPLEMENTER, _SETTINGS, different_agent=True) is None


def test_move_up_compares_the_different_agent_rule_against_an_active_override() -> None:
    # Ladder: 0=codex/luna, 1=codex/sol, 2=claude/opus. rung_index sits at 1 (codex), but the
    # active override is on claude, so "different agent" must skip claude/opus (index 2) too,
    # leaving nothing -- comparing against ladder[rung_index] instead would wrongly return it.
    override = Rung(agent="claude", model="haiku", effort=None)
    task = make_task(rung_index={Role.IMPLEMENTER: 1}, overrides={Role.IMPLEMENTER: override})

    move = move_up(task, Role.IMPLEMENTER, _SETTINGS, different_agent=True)

    assert move is None


def test_move_up_clears_the_role_override() -> None:
    override = Rung(agent="claude", model="haiku", effort=None)
    task = make_task(rung_index={Role.IMPLEMENTER: 0}, overrides={Role.IMPLEMENTER: override})

    move = move_up(task, Role.IMPLEMENTER, _SETTINGS)

    assert move is not None
    assert Role.IMPLEMENTER not in move.overrides


def test_move_up_resets_rejections_on_rung_for_the_implementer() -> None:
    task = make_task(
        rung_index={Role.IMPLEMENTER: 0}, counters=Counters(rejections_on_rung=3, consults=2)
    )

    move = move_up(task, Role.IMPLEMENTER, _SETTINGS)

    assert move is not None
    assert move.counters.rejections_on_rung == 0
    assert move.counters.consults == 2


def test_move_up_does_not_reset_counters_for_the_leader() -> None:
    task = make_task(rung_index={Role.LEADER: 0}, counters=Counters(rejections_on_rung=3))

    move = move_up(task, Role.LEADER, _SETTINGS)

    assert move is not None
    assert move.counters.rejections_on_rung == 3


@pytest.mark.parametrize(
    ("reset", "field"),
    [
        (reset_for_rung_change, "rejections_on_rung"),
        (reset_for_new_iteration, "consults"),
        (reset_for_integration_attempt, "conflict_rounds"),
    ],
    ids=["rung-change", "new-iteration", "integration-attempt"],
)
def test_counter_resets_zero_only_their_own_field(
    reset: Callable[[Counters], Counters], field: str
) -> None:
    counters = Counters(rejections_on_rung=3, consults=2, conflict_rounds=1)

    result = reset(counters)

    assert getattr(result, field) == 0
    for other in ("rejections_on_rung", "consults", "conflict_rounds"):
        if other != field:
            assert getattr(result, other) == getattr(counters, other)
