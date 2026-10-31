"""Quota waits show in the task list, Usage banners and task details, worded once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum, auto
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Static

from shikiru.config.loader import load_settings
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
    dashboard_app,
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

pytestmark = pytest.mark.usefixtures("colour_terminal")

START = datetime(2026, 1, 1, tzinfo=UTC)
LEADER_BLOCK_UNTIL = START + timedelta(minutes=20)
IMPLEMENTER_BLOCK_UNTIL = START + timedelta(minutes=30)
UNRELATED_BLOCK_UNTIL = START + timedelta(minutes=40)
LEADER_RESET = "00:20Z"
IMPLEMENTER_RESET = "00:30Z"
UNRELATED_RESET = "00:40Z"
UNRELATED_AGENT = "gemini"
EXPIRED_UNTIL = START + timedelta(minutes=1)
EXPIRED_WAIT_SECONDS = 120
AFTER_ALL_BLOCKS_SECONDS = 41 * 60
RUNNING_WAIT_ELAPSED_SECONDS = 60 * 60
EXTENDED_PAST_BUDGET_UNTIL = START + timedelta(hours=8, minutes=30)
EXTENDED_WITHIN_BUDGET_UNTIL = START + timedelta(hours=2)
ESCALATES_AT_LIMIT = "Escalates at max_quota_wait"
WITHIN_LIMIT = "the block lifts within max_quota_wait"
QUOTA_MARKER = f"{GLYPH_QUOTA} quota"
REROUTING_CONFIG = """
[project]
name = "demo"

[agents.claude]

[agents.codex]

[roles.leader]
ladder = [
  { agent = "claude", model = "opus", effort = "high" },
  { agent = "codex", model = "sol", effort = "high" },
]

[roles.implementer]
ladder = [ { agent = "codex", model = "sol", effort = "high" } ]

[roles.helper]
ladder = [ { agent = "claude", model = "haiku", effort = "low" } ]
"""
MOVES_FIRST = "Moves to another agent first"


class _BlockState(StrEnum):
    ACTIVE = auto()
    EXPIRED = auto()
    CLEARED = auto()


def _leader_agent(project_dir: Path) -> str:
    return load_settings(project_dir).roles.leader.ladder[0].agent


def _implementer_agent(project_dir: Path) -> str:
    return load_settings(project_dir).roles.implementer.ladder[0].agent


def _block_description(agent: str, reset: str) -> str:
    return f"{agent} quota blocked until {reset}"


def _motion_interval_seconds(project_dir: Path) -> float:
    return 1 / load_settings(project_dir).dashboard.motion.refresh_hz


def _seed_queued_task(
    connection: Connection,
    clock: FakeClock,
    title: str,
    wait_reason: WaitReason,
) -> str:
    task = seed_task(connection, clock, title=title)
    TaskStore(connection, clock).set_wait_reason(task.id, wait_reason)
    return task.id


def _seed_running_quota_wait(connection: Connection, clock: FakeClock, project_dir: Path) -> str:
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
        until=LEADER_BLOCK_UNTIL,
    )
    return task.id


def _set_leader_block(
    connection: Connection,
    clock: FakeClock,
    agent: str,
    state: _BlockState,
) -> None:
    store = QuotaStore(connection)
    match state:
        case _BlockState.ACTIVE:
            store.block(agent, LEADER_BLOCK_UNTIL, "quota")
        case _BlockState.EXPIRED:
            store.block(agent, EXPIRED_UNTIL, "quota")
            clock.advance(EXPIRED_WAIT_SECONDS)
        case _BlockState.CLEARED:
            store.block(agent, LEADER_BLOCK_UNTIL, "quota")
            store.clear_block(agent)


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
    task_id = _seed_queued_task(connection, clock, "Retry after quota", WaitReason.QUOTA)
    QuotaStore(connection).block(_leader_agent(project_dir), LEADER_BLOCK_UNTIL, "quota")
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
    task_id = _seed_running_quota_wait(connection, clock, project_dir)
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test():
        text = await _task_row_text(widget, task_id)

        assert QUOTA_MARKER in text


@pytest.mark.parametrize(
    ("wait_reason", "block_state", "marked"),
    [
        pytest.param(WaitReason.QUOTA, _BlockState.ACTIVE, True, id="quota-reason-active-block"),
        pytest.param(WaitReason.QUOTA, _BlockState.EXPIRED, True, id="quota-reason-expired-block"),
        pytest.param(WaitReason.QUOTA, _BlockState.CLEARED, True, id="quota-reason-cleared-block"),
        pytest.param(
            WaitReason.DEPENDENCY,
            _BlockState.ACTIVE,
            False,
            id="other-reason-active-block",
        ),
    ],
)
async def test_queued_task_carries_the_quota_marker_by_its_persisted_wait_reason(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    wait_reason: WaitReason,
    block_state: _BlockState,
    marked: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _seed_queued_task(connection, clock, "Marked by its reason", wait_reason)
    _set_leader_block(connection, clock, _leader_agent(project_dir), block_state)
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test():
        text = await _task_row_text(widget, task_id)

        assert (QUOTA_MARKER in text) == marked


@pytest.mark.parametrize(
    "block_state",
    [
        pytest.param(_BlockState.EXPIRED, id="expired-block"),
        pytest.param(_BlockState.CLEARED, id="cleared-block"),
    ],
)
async def test_usage_shows_no_banner_for_an_expired_or_cleared_block(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    block_state: _BlockState,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _seed_queued_task(connection, clock, "Waiting after the block", WaitReason.QUOTA)
    _set_leader_block(connection, clock, _leader_agent(project_dir), block_state)
    panel = UsagePanel(widget_context(reader, project_dir, clock))

    async with WidgetHostApp(panel).run_test(size=(100, 40)):
        await _usage_text(panel)

        assert "quota blocked" not in plain_text(panel.content)


async def test_usage_banner_names_the_blocked_agent_and_the_tasks_waiting_behind_it(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _seed_queued_task(connection, clock, "Waiting behind the block", WaitReason.QUOTA)
    agent = _leader_agent(project_dir)
    QuotaStore(connection).block(agent, LEADER_BLOCK_UNTIL, "quota")
    panel = UsagePanel(widget_context(reader, project_dir, clock))

    async with WidgetHostApp(panel).run_test(size=(100, 40)):
        text = await _usage_text(panel)

        assert f"{_block_description(agent, LEADER_RESET)} · 1 task waiting" in text


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


async def test_home_usage_shows_quota_banners_before_details_and_drops_them_at_expiry(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    leader = _leader_agent(project_dir)
    _seed_queued_task(connection, clock, "Waiting behind the leader", WaitReason.QUOTA)
    store = QuotaStore(connection)
    store.block(leader, LEADER_BLOCK_UNTIL, "quota")
    store.block(UNRELATED_AGENT, UNRELATED_BLOCK_UNTIL, "quota")
    app = dashboard_app(
        reader,
        project_dir,
        clock,
        widget_names=["usage"],
        motion_interval_seconds=_motion_interval_seconds(project_dir),
    )

    async with app.run_test(size=(180, 40)) as pilot:
        usage = pilot.app.screen.query_one("#usage", UsagePanel)
        await wait_until(
            lambda: "quota blocked" in plain_text(usage.content), timeout=UI_WAIT_SECONDS
        )

        text = plain_text(usage.content)
        assert f"{_block_description(leader, LEADER_RESET)} · 1 task waiting" in text
        assert _block_description(UNRELATED_AGENT, UNRELATED_RESET) in text
        assert f"{_block_description(UNRELATED_AGENT, UNRELATED_RESET)} ·" not in text

        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        clock.advance(AFTER_ALL_BLOCKS_SECONDS)

        await wait_until(
            lambda: "quota blocked" not in plain_text(usage.content),
            timeout=UI_WAIT_SECONDS,
        )


async def test_expanded_usage_banner_disappears_on_motion_once_the_block_expires(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _seed_queued_task(connection, clock, "Waiting until reset", WaitReason.QUOTA)
    QuotaStore(connection).block(_leader_agent(project_dir), LEADER_BLOCK_UNTIL, "quota")
    app = dashboard_app(
        reader,
        project_dir,
        clock,
        widget_names=["usage"],
        motion_interval_seconds=_motion_interval_seconds(project_dir),
    )

    async with app.run_test(size=(180, 40)) as pilot:
        await open_usage_details(pilot)
        usage = pilot.app.screen.query_one("#usage", UsagePanel)
        await wait_until(
            lambda: "quota blocked" in plain_text(usage.content), timeout=UI_WAIT_SECONDS
        )

        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        clock.advance(AFTER_ALL_BLOCKS_SECONDS)

        await wait_until(
            lambda: "quota blocked" not in plain_text(usage.content),
            timeout=UI_WAIT_SECONDS,
        )


async def test_banner_counts_each_task_once_for_every_blocked_agent_it_needs(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    leader = _leader_agent(project_dir)
    implementer = _implementer_agent(project_dir)
    first_task_id = _seed_queued_task(connection, clock, "Needs both agents", WaitReason.QUOTA)
    _seed_queued_task(connection, clock, "Also needs both agents", WaitReason.QUOTA)
    _seed_queued_task(connection, clock, "Behind a dependency", WaitReason.DEPENDENCY)
    _seed_running_quota_wait(connection, clock, project_dir)
    store = QuotaStore(connection)
    store.block(leader, LEADER_BLOCK_UNTIL, "quota")
    store.block(implementer, IMPLEMENTER_BLOCK_UNTIL, "quota")
    store.block(UNRELATED_AGENT, UNRELATED_BLOCK_UNTIL, "quota")
    views = state_views(reader, project_dir, clock, StubServices())

    banners = {block.agent: block.banner for block in views.quota_blocks()}
    detail = await views.task_detail(first_task_id)

    assert banners == {
        leader: f"{_block_description(leader, LEADER_RESET)} · 3 tasks waiting",
        implementer: f"{_block_description(implementer, IMPLEMENTER_RESET)} · 2 tasks waiting",
        UNRELATED_AGENT: _block_description(UNRELATED_AGENT, UNRELATED_RESET),
    }
    assert detail is not None
    assert needed_from_you(detail) == (
        f"Waiting on {_block_description(implementer, IMPLEMENTER_RESET)}."
    )


async def test_task_details_name_the_blocked_agent_and_reset_time(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _seed_queued_task(connection, clock, "Detail under quota", WaitReason.QUOTA)
    agent = _leader_agent(project_dir)
    QuotaStore(connection).block(agent, LEADER_BLOCK_UNTIL, "quota")
    views = state_views(reader, project_dir, clock, StubServices())

    detail = await views.task_detail(task_id)

    assert detail is not None
    assert needed_from_you(detail) == f"Waiting on {_block_description(agent, LEADER_RESET)}."


@pytest.mark.parametrize(
    ("reset_at", "other_agent_blocked", "moves_first"),
    [
        pytest.param(START + timedelta(hours=9), False, True, id="beyond-limit-another-agent-free"),
        pytest.param(START + timedelta(hours=9), True, False, id="beyond-limit-no-agent-free"),
        pytest.param(LEADER_BLOCK_UNTIL, False, False, id="within-limit"),
    ],
)
async def test_task_details_say_whether_a_quota_wait_moves_the_task_to_another_agent_first(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    reset_at: datetime,
    other_agent_blocked: bool,
    moves_first: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    (project_dir / ".shikiru" / "config.toml").write_text(REROUTING_CONFIG)
    clock = FakeClock(START)
    task_id = _seed_queued_task(connection, clock, "Reroute candidate", WaitReason.QUOTA)
    quotas = QuotaStore(connection)
    quotas.block("claude", reset_at, "quota")
    if other_agent_blocked:
        quotas.block("codex", reset_at, "quota")
    views = state_views(reader, project_dir, clock, StubServices())

    detail = await views.task_detail(task_id)

    assert detail is not None
    assert detail.blocker is not None
    assert any(MOVES_FIRST in line for line in detail.blocker.details) is moves_first


@pytest.mark.parametrize(
    ("reroutes", "extended_until", "guidance"),
    [
        pytest.param(
            True,
            EXTENDED_PAST_BUDGET_UNTIL,
            MOVES_FIRST,
            id="past-budget-another-agent",
        ),
        pytest.param(
            False,
            EXTENDED_PAST_BUDGET_UNTIL,
            ESCALATES_AT_LIMIT,
            id="past-budget-no-other-agent",
        ),
        pytest.param(
            True,
            EXTENDED_WITHIN_BUDGET_UNTIL,
            WITHIN_LIMIT,
            id="within-budget",
        ),
    ],
)
async def test_a_block_extended_during_a_running_wait_is_judged_against_the_waits_own_budget(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    reroutes: bool,
    extended_until: datetime,
    guidance: str,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    if reroutes:
        (project_dir / ".shikiru" / "config.toml").write_text(REROUTING_CONFIG)
    clock = FakeClock(START)
    task_id = _seed_running_quota_wait(connection, clock, project_dir)
    QuotaStore(connection).block(_leader_agent(project_dir), extended_until, "quota")
    clock.advance(RUNNING_WAIT_ELAPSED_SECONDS)
    views = state_views(reader, project_dir, clock, StubServices())

    detail = await views.task_detail(task_id)

    assert detail is not None
    assert detail.blocker is not None
    assert any(guidance in line for line in detail.blocker.details)
