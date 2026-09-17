from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    ComplexityLadder,
    DashboardConfig,
    ImplementerConfig,
    LayoutSplit,
    NotifyConfig,
    OrchestrationConfig,
    PaneLeaf,
    ProjectConfig,
    ResourcesConfig,
    RoleConfig,
    RolesConfig,
    Settings,
    TimeoutsConfig,
    TitlesConfig,
)
from shikiru.domain.ladder import (
    next_rung,
    pick_rung,
    reset_for_integration_attempt,
    reset_for_new_iteration,
    reset_for_resolved_escalation,
    reset_for_rung_change,
)
from shikiru.domain.tasks import Counters, EscalationReason, Role
from tests.support.factories import make_task

if TYPE_CHECKING:
    from collections.abc import Callable

_LEADER_RUNG = Rung(agent="claude", model="opus", effort="high")
_LEADER_RUNG_2 = Rung(agent="codex", model="sol", effort="high")
_IMPLEMENTER_RUNGS = [
    Rung(agent="codex", model="luna", effort="xhigh"),
    Rung(agent="codex", model="sol", effort="high"),
    Rung(agent="claude", model="opus", effort="high"),
]


def _settings() -> Settings:
    return Settings(
        project=ProjectConfig(name="demo"),
        orchestration=OrchestrationConfig(
            max_parallel=2,
            max_iterations=5,
            integration="merge",
            poll_seconds=2,
            resources=ResourcesConfig(max_cpu_pct=85, min_free_ram_gb=4),
            timeouts=TimeoutsConfig(
                launch=90, stall=1200, tool=7200, max_quota_wait=28800, integration_wait=1800
            ),
        ),
        roles=RolesConfig(
            leader=RoleConfig(ladder=[_LEADER_RUNG, _LEADER_RUNG_2]),
            implementer=ImplementerConfig(
                ladder=_IMPLEMENTER_RUNGS,
                escalate_after=2,
                complexity={"high": ComplexityLadder(ladder=[_IMPLEMENTER_RUNGS[1]])},
            ),
            helper=RoleConfig(ladder=[Rung(agent="claude", model="haiku", effort="low")]),
        ),
        dashboard=DashboardConfig(
            animations=True,
            widgets=["header"],
            layout=LayoutSplit(split="h", panes=[PaneLeaf(pane="system")]),
            titles=TitlesConfig(window="", leader="", implementer="", system="", shell=""),
        ),
        notify=NotifyConfig(command=""),
    )


def test_pick_rung_uses_the_ladder_at_rung_index_by_default() -> None:
    task = make_task(rung_index={Role.LEADER: 1})

    assert pick_rung(task, Role.LEADER, _settings()) == _LEADER_RUNG_2


def test_pick_rung_prefers_an_operator_override() -> None:
    override = Rung(agent="claude", model="haiku", effort=None)
    task = make_task(overrides={Role.LEADER: override})

    assert pick_rung(task, Role.LEADER, _settings()) == override


def test_pick_rung_resolves_the_implementer_ladder_by_complexity() -> None:
    task = make_task(complexity="high")

    assert pick_rung(task, Role.IMPLEMENTER, _settings()) == _IMPLEMENTER_RUNGS[1]


def test_pick_rung_resolves_the_helper_ladder() -> None:
    task = make_task()

    rung = pick_rung(task, Role.HELPER, _settings())

    assert rung.agent == "claude"
    assert rung.model == "haiku"


def test_next_rung_returns_the_following_rung() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: 0})

    advance = next_rung(task, Role.IMPLEMENTER, _settings())

    assert advance is not None
    assert advance.rung == _IMPLEMENTER_RUNGS[1]
    assert advance.index == 1


def test_next_rung_returns_none_when_the_ladder_is_exhausted() -> None:
    task = make_task(rung_index={Role.LEADER: 1})

    assert next_rung(task, Role.LEADER, _settings()) is None


def test_next_rung_with_different_agent_skips_same_agent_rungs() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: 0})

    advance = next_rung(task, Role.IMPLEMENTER, _settings(), different_agent=True)

    assert advance is not None
    assert advance.rung.agent == "claude"


def test_next_rung_with_different_agent_returns_none_when_none_remain() -> None:
    task = make_task(rung_index={Role.IMPLEMENTER: len(_IMPLEMENTER_RUNGS) - 1})

    assert next_rung(task, Role.IMPLEMENTER, _settings(), different_agent=True) is None


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


@pytest.mark.parametrize(
    ("reason", "field"),
    [
        (EscalationReason.DECISION_REQUIRED, "consults"),
        (EscalationReason.ITERATIONS_EXHAUSTED, "rejections_on_rung"),
        (EscalationReason.INTEGRATION_CONFLICT, "conflict_rounds"),
    ],
    ids=["decision-required", "iterations-exhausted", "integration-conflict"],
)
def test_resolving_an_escalation_resets_the_counter_behind_its_reason(
    reason: EscalationReason, field: str
) -> None:
    counters = Counters(rejections_on_rung=3, consults=2, conflict_rounds=1)

    result = reset_for_resolved_escalation(counters, reason)

    assert getattr(result, field) == 0


def test_resolving_an_escalation_with_no_matching_counter_is_a_no_op() -> None:
    counters = Counters(rejections_on_rung=3, consults=2, conflict_rounds=1)

    result = reset_for_resolved_escalation(counters, EscalationReason.AGENT_CRASH)

    assert result == counters
