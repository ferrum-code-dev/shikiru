"""Quota waits show in the task list, Usage banners and task details, worded once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from textual.widgets import Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.briefing_view import needed_from_you
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import Mode, Role
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.quotas import QuotaStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.glyphs import GLYPH_QUOTA
from shikiru.ui.tokens import AMBER
from tests.support.cells import foregrounds
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    open_usage_details,
    state_views,
    widget_context,
)
from tests.support.quota_waits import record_quota_wait
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
BLOCK_UNTIL = START + timedelta(minutes=20)
EXPIRED_UNTIL = START + timedelta(minutes=1)
EXPIRED_WAIT_SECONDS = 120
SHORT_BLOCK_SECONDS = 30
BLOCK_EXPIRY_STEP_SECONDS = 31
QUOTA_MARKER = f"{GLYPH_QUOTA} quota"


def _leader_agent(project_dir: Path) -> str:
    return load_settings(project_dir).roles.leader.ladder[0].agent


def _block_description(agent: str) -> str:
    return f"{agent} quota blocked until 00:20Z"


def _queued_quota_task(connection: Connection, clock: FakeClock, title: str) -> str:
    task = seed_task(connection, clock, title=title)
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.QUOTA)
    return task.id


async def _task_row(widget: QueuePanel, task_id: str) -> Static:
    await wait_until(lambda: bool(widget.query(f"#task-row-{task_id}")), timeout=UI_WAIT_SECONDS)
    return widget.query_one(f"#task-row-{task_id} > Static", Static)


async def _task_row_text(widget: QueuePanel, task_id: str) -> str:
    return plain_text((await _task_row(widget, task_id)).render())


async def _usage_text(widget: UsagePanel) -> str:
    await wait_until(lambda: bool(plain_text(widget.content)), timeout=UI_WAIT_SECONDS)
    return plain_text(widget.content)


async def test_queued_task_waiting_on_quota_is_marked_in_amber_while_its_leader_is_blocked(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _queued_quota_task(connection, clock, "Retry after quota")
    QuotaStore(connection).block(_leader_agent(project_dir), BLOCK_UNTIL, "quota")
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test():
        text = await _task_row_text(widget, task_id)

        assert QUOTA_MARKER in text
        assert set(foregrounds(await _task_row(widget, task_id), QUOTA_MARKER)) == {AMBER}


async def test_running_task_in_a_mid_turn_quota_wait_is_marked_in_the_task_list(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    leader_rung = load_settings(project_dir).roles.leader.ladder[0]
    store = TaskStore(connection, clock)
    task = seed_task(connection, clock, title="Plan under quota", mode=Mode.PLAN)
    store.apply_transition(task.id, Transition.stay(task, step=Step.PLAN))
    task = store.require(task.id)
    record_quota_wait(
        connection,
        clock,
        task,
        role=Role.LEADER,
        rung=leader_rung,
        until=BLOCK_UNTIL,
    )
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test():
        text = await _task_row_text(widget, task.id)

        assert QUOTA_MARKER in text


async def test_quota_marker_is_dropped_once_the_block_it_waited_on_has_expired(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _queued_quota_task(connection, clock, "Stale quota reason")
    QuotaStore(connection).block(_leader_agent(project_dir), EXPIRED_UNTIL, "quota")
    clock.advance(EXPIRED_WAIT_SECONDS)
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test():
        text = await _task_row_text(widget, task_id)

        assert "waiting on quota" in text
        assert GLYPH_QUOTA not in text


async def test_usage_banner_names_the_blocked_agent_and_the_tasks_waiting_behind_it(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _queued_quota_task(connection, clock, "Waiting behind the block")
    agent = _leader_agent(project_dir)
    QuotaStore(connection).block(agent, BLOCK_UNTIL, "quota")
    panel = UsagePanel(widget_context(reader, project_dir, clock))

    async with WidgetHostApp(panel).run_test(size=(100, 40)):
        text = await _usage_text(panel)

        assert f"{_block_description(agent)} · 1 task waiting" in text


async def test_usage_shows_no_banner_when_no_agent_is_blocked(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    seed_task(connection, clock, title="Unblocked work")
    panel = UsagePanel(widget_context(reader, project_dir, clock))

    async with WidgetHostApp(panel).run_test(size=(100, 40)):
        await _usage_text(panel)

        assert "quota blocked" not in plain_text(panel.content)


async def test_usage_banner_disappears_on_motion_once_the_block_expires(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    agent = _leader_agent(project_dir)
    _queued_quota_task(connection, clock, "Waiting until reset")
    QuotaStore(connection).block(agent, START + timedelta(seconds=SHORT_BLOCK_SECONDS), "quota")
    settings = load_settings(project_dir)

    async def open_palette(action: str) -> None:
        del action

    app = DashboardApp(
        focus_task=None,
        views=state_views(reader, project_dir, clock, StubServices(), settings=settings),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        open_palette=open_palette,
        widget_names=["usage"],
    )
    async with app.run_test(size=(180, 40)) as pilot:
        await open_usage_details(pilot)
        usage = pilot.app.screen.query_one("#usage", UsagePanel)
        await wait_until(
            lambda: "quota blocked" in plain_text(usage.content), timeout=UI_WAIT_SECONDS
        )

        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        clock.advance(BLOCK_EXPIRY_STEP_SECONDS)

        await wait_until(
            lambda: "quota blocked" not in plain_text(usage.content),
            timeout=UI_WAIT_SECONDS,
        )


async def test_task_details_name_the_blocked_agent_and_reset_time(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _queued_quota_task(connection, clock, "Detail under quota")
    agent = _leader_agent(project_dir)
    QuotaStore(connection).block(agent, BLOCK_UNTIL, "quota")
    views = state_views(reader, project_dir, clock, StubServices())

    detail = await views.task_detail(task_id)

    assert detail is not None
    assert needed_from_you(detail) == f"Waiting on {_block_description(agent)}."
