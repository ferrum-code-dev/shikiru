from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    MAX_RETRY_DELAY_SECONDS,
    ComplexityLadder,
    DashboardConfig,
    GaugesConfig,
    RetriesConfig,
    RoleConfig,
    RolesConfig,
    Settings,
    TitlesConfig,
)
from tests.support.factories import make_settings

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


@pytest.mark.parametrize(
    ("short", "table", "rung"),
    [
        pytest.param(
            "codex:gpt-5.6-sol:high",
            {"agent": "codex", "model": "gpt-5.6-sol", "effort": "high"},
            Rung(agent="codex", model="gpt-5.6-sol", effort="high"),
            id="with-effort",
        ),
        pytest.param(
            "claude:opus",
            {"agent": "claude", "model": "opus"},
            Rung(agent="claude", model="opus"),
            id="without-effort",
        ),
    ],
)
def test_short_and_table_rungs_validate_to_the_same_rung_when_mixed_in_one_ladder(
    short: str, table: dict[str, str], rung: Rung
) -> None:
    ladder = RoleConfig.model_validate({"ladder": [short, table]}).ladder

    assert ladder == [rung, rung]


def test_settings_rejects_an_empty_helper_ladder() -> None:
    settings = make_settings()
    roles = RolesConfig(
        leader=settings.roles.leader,
        implementer=settings.roles.implementer,
        helper=RoleConfig(ladder=[]),
    )

    with pytest.raises(ValidationError):
        Settings.model_validate({**dict(settings), "roles": roles})


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unavailable_initial_seconds", 0),
        ("unavailable_initial_seconds", float("inf")),
        ("unavailable_initial_seconds", float("nan")),
        ("unavailable_initial_seconds", MAX_RETRY_DELAY_SECONDS * 2),
        ("unavailable_multiplier", 1),
        ("unavailable_multiplier", float("inf")),
        ("unavailable_multiplier", float("nan")),
        ("unavailable_max_seconds", 0),
        ("unavailable_max_seconds", float("inf")),
        ("unavailable_max_seconds", MAX_RETRY_DELAY_SECONDS * 2),
        ("unavailable_max_retries", -1),
        ("unavailable_max_retries", 21),
    ],
)
def test_unavailable_retries_reject_invalid_values(field: str, value: object) -> None:
    retries = make_settings().orchestration.retries

    with pytest.raises(ValidationError):
        RetriesConfig.model_validate({**dict(retries), field: value})


def test_unavailable_retries_reject_a_cap_below_the_initial_delay() -> None:
    retries = make_settings().orchestration.retries

    with pytest.raises(ValidationError, match="must be at least"):
        RetriesConfig.model_validate(
            {
                **dict(retries),
                "unavailable_initial_seconds": 10,
                "unavailable_max_seconds": 9,
            }
        )


def test_maximum_unavailable_retry_delay_is_convertible_to_timedelta() -> None:
    retries = make_settings().orchestration.retries
    maximum = MAX_RETRY_DELAY_SECONDS

    accepted = RetriesConfig.model_validate(
        {
            **dict(retries),
            "unavailable_initial_seconds": maximum,
            "unavailable_max_seconds": maximum,
        }
    )

    assert timedelta(seconds=accepted.unavailable_max_seconds) <= timedelta.max
