from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import ComplexityLadder, Settings
from shikiru.domain.ladder import (
    move_up,
    pick_rung,
    reset_for_integration_attempt,
    reset_for_new_iteration,
    reset_for_rung_change,
)
from shikiru.domain.tasks import Counters, Role, Task
from tests.support.factories import (
    HELPER_RUNG,
    IMPLEMENTER_RUNGS,
    LEADER_RUNG_2,
    make_settings,
    make_task,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_SETTINGS = make_settings()


@pytest.mark.parametrize(
    ("task", "role", "settings", "expected"),
    [
        pytest.param(
            make_task(rung_index={Role.LEADER: 1}),
            Role.LEADER,
            _SETTINGS,
            LEADER_RUNG_2,
            id="indexed-ladder-rung",
        ),
        pytest.param(
            make_task(overrides={Role.LEADER: Rung(agent="claude", model="haiku", effort=None)}),
            Role.LEADER,
            _SETTINGS,
            Rung(agent="claude", model="haiku", effort=None),
            id="operator-override",
        ),
        pytest.param(make_task(), Role.HELPER, _SETTINGS, HELPER_RUNG, id="helper-ladder"),
    ],
)
def test_pick_rung_uses_the_active_role_rung(
    task: Task, role: Role, settings: Settings, expected: Rung
) -> None:
    assert pick_rung(task, role, settings) == expected


@pytest.mark.parametrize(
    ("complexity", "expected"),
    [
        pytest.param("high", IMPLEMENTER_RUNGS[1], id="override"),
        pytest.param("normal", IMPLEMENTER_RUNGS[0], id="base-ladder-fallback"),
    ],
)
def test_implementer_complexity_selects_an_override_or_base_ladder(
    complexity: str, expected: Rung
) -> None:
    settings = make_settings(
        implementer_complexity={"high": ComplexityLadder(ladder=[IMPLEMENTER_RUNGS[1]])}
    )
    task = make_task(complexity=complexity)

    assert pick_rung(task, Role.IMPLEMENTER, settings) == expected


_ACTIVE_OVERRIDE = Rung(agent="claude", model="haiku", effort=None)


@dataclass(frozen=True)
class _MoveUpCase:
    task: Task
    role: Role
    different_agent: bool
    expected_index: int | None
    expected_overrides: dict[Role, Rung]
    expected_counters: Counters


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _MoveUpCase(
                make_task(rung_index={Role.IMPLEMENTER: 0}),
                Role.IMPLEMENTER,
                False,
                1,
                {},
                Counters(),
            ),
            id="next-rung",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(rung_index={Role.LEADER: 1}),
                Role.LEADER,
                False,
                None,
                {},
                Counters(),
            ),
            id="exhausted",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(rung_index={Role.IMPLEMENTER: 0}),
                Role.IMPLEMENTER,
                True,
                2,
                {},
                Counters(),
            ),
            id="next-different-agent",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(rung_index={Role.IMPLEMENTER: len(IMPLEMENTER_RUNGS) - 1}),
                Role.IMPLEMENTER,
                True,
                None,
                {},
                Counters(),
            ),
            id="no-different-agent-remains",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(
                    rung_index={Role.IMPLEMENTER: 1},
                    overrides={Role.IMPLEMENTER: _ACTIVE_OVERRIDE},
                ),
                Role.IMPLEMENTER,
                True,
                None,
                {},
                Counters(),
            ),
            id="different-agent-compares-with-active-override",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(
                    rung_index={Role.IMPLEMENTER: 0},
                    overrides={Role.IMPLEMENTER: _ACTIVE_OVERRIDE},
                    counters=Counters(rejections_on_rung=3, consults=2),
                ),
                Role.IMPLEMENTER,
                False,
                1,
                {},
                Counters(rejections_on_rung=0, consults=2),
            ),
            id="clears-override-and-resets-implementer-rejections",
        ),
        pytest.param(
            _MoveUpCase(
                make_task(
                    rung_index={Role.LEADER: 0},
                    counters=Counters(rejections_on_rung=3, consults=2),
                ),
                Role.LEADER,
                False,
                1,
                {},
                Counters(rejections_on_rung=3, consults=2),
            ),
            id="leader-keeps-counters",
        ),
    ],
)
def test_move_up_selects_the_next_allowed_rung_and_updates_role_state(case: _MoveUpCase) -> None:
    move = move_up(case.task, case.role, _SETTINGS, different_agent=case.different_agent)

    if case.expected_index is None:
        assert move is None
        return
    assert move is not None
    assert move.rung_index[case.role] == case.expected_index
    assert move.overrides == case.expected_overrides
    assert move.counters == case.expected_counters


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
