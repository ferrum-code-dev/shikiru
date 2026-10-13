from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    ComplexityLadder,
    DashboardConfig,
    GaugesConfig,
    Settings,
    TitlesConfig,
)
from tests.support.factories import HELPER_RUNG, make_settings

if TYPE_CHECKING:
    from collections.abc import Callable


_GHOST_RUNG = Rung(agent="ghost", model="m", effort=None)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: make_settings(leader_ladder=[_GHOST_RUNG]), id="leader"),
        pytest.param(lambda: make_settings(implementer_ladder=[_GHOST_RUNG]), id="implementer"),
        pytest.param(
            lambda: make_settings(
                implementer_complexity={"high": ComplexityLadder(ladder=[_GHOST_RUNG])}
            ),
            id="implementer-complexity-override",
        ),
        pytest.param(lambda: make_settings(helper_ladder=[_GHOST_RUNG]), id="helper"),
    ],
)
def test_settings_rejects_a_ladder_agent_with_no_agents_table(
    build: Callable[[], Settings],
) -> None:
    with pytest.raises(ValidationError):
        build()


def test_settings_rejects_multiple_helper_rungs() -> None:
    with pytest.raises(ValidationError):
        make_settings(helper_ladder=[HELPER_RUNG, Rung(agent="codex", model="sol", effort="high")])


def test_dashboard_rejects_a_recent_limit_smaller_than_its_window() -> None:
    dashboard = make_settings().dashboard

    with pytest.raises(ValidationError):
        DashboardConfig.model_validate(
            {**dict(dashboard), "recent_task_limit": 2, "recent_task_window": 3}
        )


def test_titles_reject_a_context_warn_threshold_not_below_the_alert_threshold() -> None:
    titles = make_settings().dashboard.titles

    with pytest.raises(ValidationError):
        TitlesConfig.model_validate(
            {**dict(titles), "context_warn_pct": 60, "context_alert_pct": 60}
        )


@pytest.mark.parametrize(
    ("warn_pct", "alert_pct"),
    [(0, 90), (90, 90), (95, 90), (50, 101)],
)
def test_gauges_reject_quota_thresholds_outside_zero_below_warn_below_alert_at_most_100(
    warn_pct: int, alert_pct: int
) -> None:
    gauges = make_settings().dashboard.gauges

    with pytest.raises(ValidationError):
        GaugesConfig.model_validate(
            {**dict(gauges), "quota_warn_pct": warn_pct, "quota_alert_pct": alert_pct}
        )


@pytest.mark.parametrize(
    ("warn_pct", "alert_pct"),
    [(10, 10), (5, 10), (10, 0), (101, 5)],
)
def test_dashboard_rejects_disk_thresholds_outside_alert_below_warn_at_most_100(
    warn_pct: int, alert_pct: int
) -> None:
    dashboard = make_settings().dashboard

    with pytest.raises(ValidationError):
        DashboardConfig.model_validate(
            {**dict(dashboard), "disk_warn_free_pct": warn_pct, "disk_alert_free_pct": alert_pct}
        )
