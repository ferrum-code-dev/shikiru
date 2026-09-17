"""Minimal `Task`/`Settings` factories shared by domain tests."""

from __future__ import annotations

from typing import Any

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    ComplexityLadder,
    ComplexityName,
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
from shikiru.domain.tasks import Status, Task, TaskOrigin

LEADER_RUNG = Rung(agent="claude", model="opus", effort="high")
LEADER_RUNG_2 = Rung(agent="codex", model="sol", effort="high")
IMPLEMENTER_RUNGS = [
    Rung(agent="codex", model="luna", effort="xhigh"),
    Rung(agent="codex", model="sol", effort="high"),
    Rung(agent="claude", model="opus", effort="high"),
]
HELPER_RUNG = Rung(agent="claude", model="haiku", effort="low")

MAX_ITERATIONS = 5
ESCALATE_AFTER = 2


def make_task(**overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "id": "T-1",
        "seq": 1,
        "origin": TaskOrigin.OPERATOR,
        "title": "Add retry to parser",
        "request": "Add retry to parser",
        "status": Status.RUNNING,
    }
    fields.update(overrides)
    return Task(**fields)


def make_settings(
    *,
    max_iterations: int = MAX_ITERATIONS,
    escalate_after: int = ESCALATE_AFTER,
    leader_ladder: list[Rung] | None = None,
    implementer_ladder: list[Rung] | None = None,
    implementer_complexity: dict[ComplexityName, ComplexityLadder] | None = None,
    helper_ladder: list[Rung] | None = None,
) -> Settings:
    return Settings(
        project=ProjectConfig(name="demo"),
        orchestration=OrchestrationConfig(
            max_parallel=2,
            max_iterations=max_iterations,
            integration="merge",
            poll_seconds=2,
            resources=ResourcesConfig(max_cpu_pct=85, min_free_ram_gb=4),
            timeouts=TimeoutsConfig(
                launch=90, stall=1200, tool=7200, max_quota_wait=28800, integration_wait=1800
            ),
        ),
        roles=RolesConfig(
            leader=RoleConfig(ladder=leader_ladder or [LEADER_RUNG, LEADER_RUNG_2]),
            implementer=ImplementerConfig(
                ladder=implementer_ladder or list(IMPLEMENTER_RUNGS),
                escalate_after=escalate_after,
                complexity=implementer_complexity or {},
            ),
            helper=RoleConfig(ladder=helper_ladder or [HELPER_RUNG]),
        ),
        dashboard=DashboardConfig(
            animations=True,
            widgets=["header"],
            layout=LayoutSplit(split="h", panes=[PaneLeaf(pane="system")]),
            titles=TitlesConfig(window="", leader="", implementer="", system="", shell=""),
        ),
        notify=NotifyConfig(command=""),
    )
