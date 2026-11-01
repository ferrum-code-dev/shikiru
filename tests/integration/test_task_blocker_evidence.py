"""The Blockers evidence disclosure: collapsed by default, factual, scoped to active blockers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from textual.widgets import Static

from shikiru.agents.contracts import QUOTA_WINDOW_FIVE_HOUR, QUOTA_WINDOW_SEVEN_DAY, QuotaWindow
from shikiru.clock import SystemClock
from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import ResourceView
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RAW_EVENTS,
    TASK_VIEW_BLOCKER_EVIDENCE,
    TASK_VIEW_RAW_EVENTS,
    TASK_VIEW_RELATIONSHIPS,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold
from shikiru.domain.quota_waits import ENGINE_RETRY_DIFFERS, QUOTA_NOT_REPORTED, quota_window_label
from shikiru.domain.recovery import QUOTA_MARGIN
from shikiru.domain.scheduling import WaitReason
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from tests.support.cells import rendered_lines
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import ManualFocusHost, StubServices, fold_headline, widget_context
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
NOW = START + timedelta(minutes=20)
QUOTA_BLOCK_UNTIL = START + timedelta(minutes=80)
EXPIRED_RESET = START + timedelta(minutes=10)
FIVE_HOUR_RESET = START + timedelta(hours=3)
FIVE_HOUR_RESET_TEXT = "Today 03:00Z"
WEEKLY_RESET = START + timedelta(days=2)
WEEKLY_RESET_TEXT = "Jan 03 00:00Z"
NEXT_DAY_RESET = START + timedelta(days=1, minutes=5)
NEXT_DAY_RESET_TEXT = "Jan 02 00:05Z"
RESOURCE_SAMPLE_CPU_PCT = 92.0
WEEKLY_DIFFERS = ENGINE_RETRY_DIFFERS.format(window=quota_window_label(QUOTA_WINDOW_SEVEN_DAY))
PANEL_WIDTH = 100
NARROW_WIDTH = 40
PANEL_HEIGHT = 40
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
TIMELINE_REFRESH_SECONDS = 1


def _leader(project_dir: Path) -> str:
    return load_settings(project_dir).roles.leader.ladder[0].agent


def _implementer(project_dir: Path) -> str:
    return load_settings(project_dir).roles.implementer.ladder[0].agent


def _seed_queued_quota_wait(
    connection: Connection,
    clock: FakeClock,
    agents: tuple[str, ...],
    *,
    until: datetime = QUOTA_BLOCK_UNTIL,
) -> str:
    task = seed_task(connection, clock, title="Waiting on quota")
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.QUOTA)
    quotas = QuotaStore(connection)
    for agent in agents:
        quotas.block(agent, until, "quota", LONG_AGO)
    return task.id


def _record_window(
    connection: Connection,
    agent: str,
    name: str,
    used_pct: float,
    resets_at: datetime | None,
    *,
    observed_at: datetime = START,
) -> None:
    QuotaStore(connection).record(
        agent,
        QuotaWindow(name=name, used_pct=used_pct, resets_at=resets_at, observed_at=observed_at),
    )


async def _loaded(panel: TaskDetailPanel, task_id: str) -> None:
    await wait_until(
        lambda: (
            task_id in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
            and bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content)
        ),
        timeout=UI_WAIT_SECONDS,
    )


@asynccontextmanager
async def _mounted(
    database: Database,
    project_dir: Path,
    clock: FakeClock,
    task_id: str,
    *,
    width: int = PANEL_WIDTH,
    services: StubServices | None = None,
) -> AsyncGenerator[TaskDetailPanel]:
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_dir, clock, services=services),
        task_id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(width, PANEL_HEIGHT)):
            await _loaded(panel, task_id)
            yield panel
    finally:
        reader.close()


def _evidence_fold(panel: TaskDetailPanel) -> DetailFold:
    return next(
        fold for fold in panel.query(DetailFold) if fold.view_id == TASK_VIEW_BLOCKER_EVIDENCE
    )


def _raw_fold(panel: TaskDetailPanel) -> DetailFold:
    return next(fold for fold in panel.query(DetailFold) if fold.view_id == TASK_VIEW_RAW_EVENTS)


def _body_lines(panel: TaskDetailPanel) -> list[str]:
    body = panel.query_one(f"#{TASK_DETAIL_EVIDENCE}", GatedStatic)
    return [
        "".join(char for char, _ in cells)
        for cells in rendered_lines(body)
        if "".join(char for char, _ in cells).strip()
    ]


def _body_text(panel: TaskDetailPanel) -> str:
    return "\n".join(_body_lines(panel))


def _row(panel: TaskDetailPanel, marker: str) -> str:
    return next(line for line in _body_lines(panel) if marker in line)


def _index_of(lines: list[str], marker: str) -> int:
    return next(index for index, line in enumerate(lines) if marker in line)


def _open_evidence(panel: TaskDetailPanel) -> None:
    panel.open_view(TASK_VIEW_BLOCKER_EVIDENCE)


async def _elapse(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
    clock.advance(seconds)
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)


async def test_evidence_starts_collapsed_with_its_freshness_and_expands_to_aligned_readings(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)
    _record_window(connection, leader, QUOTA_WINDOW_SEVEN_DAY, 100.0, WEEKLY_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        await wait_until(
            lambda: "20m" in fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE),
            timeout=UI_WAIT_SECONDS,
        )
        assert _evidence_fold(panel).is_open is False
        assert _body_lines(panel) == []

        _open_evidence(panel)
        await wait_until(lambda: "100%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        five_hour, weekly = _row(panel, "40%"), _row(panel, "100%")
        assert FIVE_HOUR_RESET_TEXT in five_hour
        assert WEEKLY_RESET_TEXT in weekly
        assert five_hour.index("%") == weekly.index("%")


async def test_freshness_shows_a_range_when_quota_windows_have_different_sample_times(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(
        connection,
        leader,
        QUOTA_WINDOW_FIVE_HOUR,
        40.0,
        FIVE_HOUR_RESET,
        observed_at=NOW - timedelta(minutes=5),
    )
    _record_window(
        connection,
        leader,
        QUOTA_WINDOW_SEVEN_DAY,
        100.0,
        WEEKLY_RESET,
        observed_at=START,
    )

    async with _mounted(database, project_dir, clock, task_id) as panel:
        headline = fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE)
        await wait_until(lambda: "5m" in headline and "20m" in headline, timeout=UI_WAIT_SECONDS)


@pytest.mark.parametrize(
    ("used_pct", "resets_at", "used_text", "reset_text"),
    [
        pytest.param(0.0, None, "0%", QUOTA_NOT_REPORTED, id="zero-used-reset-unreported"),
        pytest.param(40.4, FIVE_HOUR_RESET, "40%", FIVE_HOUR_RESET_TEXT, id="share-rounds-down"),
        pytest.param(
            99.6, NEXT_DAY_RESET, "100%", NEXT_DAY_RESET_TEXT, id="share-rounds-up-next-day-reset"
        ),
    ],
)
async def test_each_reading_shows_its_rounded_share_and_its_reset_or_that_none_is_reported(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    *,
    used_pct: float,
    resets_at: datetime | None,
    used_text: str,
    reset_text: str,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, used_pct, resets_at)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: used_text in _body_text(panel), timeout=UI_WAIT_SECONDS)

        assert reset_text in _row(panel, used_text)


async def test_readings_of_several_blocked_providers_sit_under_their_own_labels(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader, implementer = _leader(project_dir), _implementer(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader, implementer))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)
    _record_window(connection, implementer, QUOTA_WINDOW_FIVE_HOUR, 70.0, FIVE_HOUR_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "70%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        lines = _body_lines(panel)
        assert (
            _index_of(lines, leader)
            < _index_of(lines, "40%")
            < _index_of(lines, implementer)
            < _index_of(lines, "70%")
        )


async def test_a_provider_without_a_reading_gets_no_invented_share_or_reset(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader, implementer = _leader(project_dir), _implementer(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader, implementer))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "40%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        lines = _body_lines(panel)
        assert all("%" not in line for line in lines[_index_of(lines, implementer) :])
        assert "20m" in fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE)


async def test_an_expired_reading_leaves_the_readings_and_stays_in_raw_events(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, EXPIRED_RESET)
    _record_window(connection, leader, QUOTA_WINDOW_SEVEN_DAY, 60.0, WEEKLY_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "60%" in _body_text(panel), timeout=UI_WAIT_SECONDS)
        assert "40%" not in _body_text(panel)

        panel.open_view(TASK_VIEW_RAW_EVENTS)
        await wait_until(lambda: _raw_fold(panel).is_open, timeout=UI_WAIT_SECONDS)
        assert EXPIRED_RESET.isoformat() in plain_text(
            panel.query_one(f"#{TASK_DETAIL_RAW_EVENTS}", Static).content
        )


@pytest.mark.parametrize(
    ("until", "differs"),
    [
        pytest.param(WEEKLY_RESET + QUOTA_MARGIN, False, id="retry-follows-the-exhausted-reset"),
        pytest.param(QUOTA_BLOCK_UNTIL, True, id="retry-differs-from-the-exhausted-reset"),
    ],
)
async def test_the_engine_retry_is_named_against_the_reset_only_when_it_differs(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    *,
    until: datetime,
    differs: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,), until=until)
    _record_window(connection, leader, QUOTA_WINDOW_SEVEN_DAY, 100.0, WEEKLY_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "100%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        assert (WEEKLY_DIFFERS in _body_text(panel)) is differs


async def test_raw_events_stay_collapsed_under_the_evidence_and_open_to_exact_timestamps(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "40%" in _body_text(panel), timeout=UI_WAIT_SECONDS)
        assert _raw_fold(panel).is_open is False

        panel.open_view(TASK_VIEW_RAW_EVENTS)
        await wait_until(lambda: _raw_fold(panel).is_open, timeout=UI_WAIT_SECONDS)
        assert QUOTA_BLOCK_UNTIL.isoformat() in plain_text(
            panel.query_one(f"#{TASK_DETAIL_RAW_EVENTS}", Static).content
        )


async def test_a_refresh_keeps_the_disclosure_open_and_shows_the_newer_reading(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(
            lambda: "20m" in fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE),
            timeout=UI_WAIT_SECONDS,
        )
        _record_window(
            connection, leader, QUOTA_WINDOW_FIVE_HOUR, 55.0, FIVE_HOUR_RESET, observed_at=NOW
        )

        panel.refresh_after_resume()

        await wait_until(lambda: "55%" in _body_text(panel), timeout=UI_WAIT_SECONDS)
        assert "40%" not in _body_text(panel)
        assert "20m" not in fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE)
        assert _evidence_fold(panel).is_open


async def test_the_evidence_is_removed_with_its_blocker_when_the_block_lifts(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_FIVE_HOUR, 40.0, FIVE_HOUR_RESET)

    async with _mounted(database, project_dir, clock, task_id) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "40%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        await _elapse(clock, TIMELINE_REFRESH_SECONDS)
        await _elapse(clock, (QUOTA_BLOCK_UNTIL - NOW).total_seconds())

        await wait_until(lambda: "40%" not in _body_text(panel), timeout=UI_WAIT_SECONDS)

        TaskStore(connection, clock).set_wait_reason(task_id, None)
        panel.refresh_after_resume()

        await wait_until(lambda: not _evidence_fold(panel).display, timeout=UI_WAIT_SECONDS)
        assert fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE) == ""
        assert _evidence_fold(panel).is_open is False


@pytest.mark.parametrize("width", [PANEL_WIDTH, NARROW_WIDTH], ids=["ordinary", "narrow"])
async def test_readings_reflow_at_narrow_widths_without_clipping_a_value(
    connection: Connection, database: Database, tmp_path: Path, *, width: int
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    leader = _leader(project_dir)
    task_id = _seed_queued_quota_wait(connection, clock, (leader,))
    _record_window(connection, leader, QUOTA_WINDOW_SEVEN_DAY, 100.0, WEEKLY_RESET)

    async with _mounted(database, project_dir, clock, task_id, width=width) as panel:
        _open_evidence(panel)
        await wait_until(lambda: "100%" in _body_text(panel), timeout=UI_WAIT_SECONDS)

        flattened = " ".join(_body_text(panel).split())
        assert WEEKLY_RESET_TEXT in flattened
        assert "100%" in flattened
        assert ELLIPSIS not in flattened


async def test_a_resource_blocker_shows_the_sample_age_as_its_evidence_freshness(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(NOW)
    task = seed_task(connection, clock, title="Waiting on resources")
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.RESOURCES)
    services = StubServices()
    services.resource_observation = ResourceView(
        host_cpu_pct=RESOURCE_SAMPLE_CPU_PCT,
        host_ram_used_pct=88.0,
        free_ram_gb=0.4,
        instance_cpu_pct=None,
        instance_rss_gb=None,
        sampled_at=START,
    )

    async with _mounted(database, project_dir, clock, task.id, services=services) as panel:
        _open_evidence(panel)
        await wait_until(
            lambda: "20m" in fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE),
            timeout=UI_WAIT_SECONDS,
        )

        await wait_until(
            lambda: f"{RESOURCE_SAMPLE_CPU_PCT}%" in _body_text(panel),
            timeout=UI_WAIT_SECONDS,
        )
