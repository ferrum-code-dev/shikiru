from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.detail import (
    AGENT_TIMELINE_IDS,
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_TIMELINE_LIST,
    TASK_VIEW_AGENT,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_REQUEST,
    TASK_VIEW_RESULTS,
    TASK_VIEW_USAGE,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.timeline_filter import TimelineFilterBar
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Role
from shikiru.paths import run_dir
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.ui.gated_static import GatedStatic
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    ManualFocusHost,
    dashboard_app,
    is_hit_at_center,
    list_rows,
    widget_context,
)
from tests.support.factories import LEADER_RUNG
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.dashboard.contract import DashboardViews
    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
AGENT_LIST = "task-detail-agent-list"
EVENTS_FOLD = TASK_VIEW_EVIDENCE
FIRST_FOLD = TASK_VIEW_REQUEST


@dataclass(frozen=True)
class _KeyCase:
    keys: tuple[str, ...]
    focused: str
    open_views: frozenset[str]


KEY_CASES = [
    _KeyCase(("down",), TASK_VIEW_RESULTS, frozenset()),
    _KeyCase(("down", "up"), FIRST_FOLD, frozenset()),
    _KeyCase(("down", "enter"), TASK_VIEW_RESULTS, frozenset({TASK_VIEW_RESULTS})),
    _KeyCase(("down", "enter", "enter"), TASK_VIEW_RESULTS, frozenset()),
    _KeyCase(("down", "space", "space"), TASK_VIEW_RESULTS, frozenset()),
    _KeyCase(
        ("space", "down", "space"),
        TASK_VIEW_RESULTS,
        frozenset({TASK_VIEW_REQUEST, TASK_VIEW_RESULTS}),
    ),
    _KeyCase(
        ("down", "down", "down", "enter"), TASK_DETAIL_TIMELINE_LIST, frozenset({EVENTS_FOLD})
    ),
    _KeyCase(("down", "down", "down", "space"), EVENTS_FOLD, frozenset({EVENTS_FOLD})),
]


class _CountingViews:
    def __init__(self, delegate: DashboardViews) -> None:
        self._delegate = delegate
        self.timeline_categories: set[str] = set()
        self.usage_reads = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def task_timeline(
        self, task_id: str, *, cursor: str | None, role: str | None, turn: str | None, category: str
    ) -> object:
        self.timeline_categories.add(category)
        return await self._delegate.task_timeline(
            task_id, cursor=cursor, role=role, turn=turn, category=category
        )

    def usage(self) -> object:
        self.usage_reads += 1
        return self._delegate.usage()


def _seed(
    connection: Connection, project_root: Path, clock: FakeClock, title: str
) -> tuple[str, Path]:
    task = seed_task(connection, clock, title=title)
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signals = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    signals.parent.mkdir(parents=True)
    append_event(connection, clock, task.id, "first-event")
    append_signal(signals, clock, "first-signal")
    return task.id, signals


def append_event(connection: Connection, clock: FakeClock, task_id: str, message: str) -> None:
    clock.advance(1)
    EventLog(connection, clock).append(
        EventKind.TURN_FAILED,
        task_id=task_id,
        message=message,
        data={"role": Role.LEADER.value, "turn": message},
    )


def append_signal(path: Path, clock: FakeClock, turn: str) -> None:
    clock.advance(1)
    row = {"ts": clock.now().isoformat(), "turn": turn, "signal": "Message", "text": turn}
    with path.open("a") as output:
        output.write(json.dumps(row) + "\n")


async def _wake(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
    clock.advance(seconds)


def _focused_stop(pilot: Pilot[None]) -> str | None:
    focused = pilot.app.focused
    if isinstance(focused, RowList | TimelineFilterBar):
        return focused.id
    parent = focused.parent if focused is not None else None
    return parent.view_id if isinstance(parent, DetailFold) else None


async def test_header_keys_move_between_folds_and_open_or_close_them(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed(connection, project_root, clock, "Fold keys")
    reader = StateReader(database, FakeClock(START))
    try:
        for case in KEY_CASES:
            panel = TaskDetailPanel(
                widget_context(reader, project_root, clock), task_id, fixed=True
            )
            async with ManualFocusHost(panel).run_test(size=(100, 40)) as pilot:
                await wait_until(
                    lambda panel=panel: (
                        task_id
                        in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
                        and bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content)
                    ),
                    timeout=UI_WAIT_SECONDS,
                )
                panel.focus_folds()
                await wait_until(
                    lambda pilot=pilot: _focused_stop(pilot) == FIRST_FOLD,
                    timeout=UI_WAIT_SECONDS,
                )
                await pilot.press(*case.keys)
                await wait_until(
                    lambda pilot=pilot, case=case: _focused_stop(pilot) == case.focused,
                    timeout=UI_WAIT_SECONDS,
                )
                await wait_until(
                    lambda panel=panel, case=case: panel.open_views == case.open_views,
                    timeout=UI_WAIT_SECONDS,
                )
    finally:
        reader.close()


async def test_open_folds_stay_live_and_folded_ones_cost_nothing(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, signals = _seed(connection, project_root, clock, "Live folds")
    reader_clock = FakeClock(START)
    reader = StateReader(database, reader_clock)
    context = widget_context(reader, project_root, clock)
    views = _CountingViews(context.views)
    seconds = load_settings(project_root).dashboard.timeline_refresh_seconds
    panel = TaskDetailPanel(
        replace(context, views=cast("DashboardViews", views)), task_id, fixed=True
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 60)):
            await wait_until(
                lambda: panel.task_id == task_id and panel.is_mounted, timeout=UI_WAIT_SECONDS
            )
            await _wake(clock, seconds)
            await _wake(clock, seconds)
            assert (views.timeline_categories, views.usage_reads) == (set(), 0)

            panel.open_view(EVENTS_FOLD)
            panel.open_view(TASK_VIEW_AGENT)
            await wait_until(
                lambda: (
                    bool(list_rows(panel, TASK_DETAIL_TIMELINE_LIST))
                    and bool(list_rows(panel, AGENT_LIST))
                ),
                timeout=UI_WAIT_SECONDS,
            )
            append_event(connection, clock, task_id, "second-event")
            append_signal(signals, clock, "second-signal")
            await wait_until(reader_clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
            reader_clock.advance(seconds)
            await _wake(clock, seconds)
            await wait_until(
                lambda: (
                    "second-event" in list_rows(panel, TASK_DETAIL_TIMELINE_LIST)[0]
                    and len(list_rows(panel, AGENT_LIST)) == 2
                ),
                timeout=UI_WAIT_SECONDS,
            )
            assert views.usage_reads == 0
    finally:
        reader.close()


async def test_open_folds_survive_a_refresh_and_a_task_change_but_task_state_resets(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    first_id, _ = _seed(connection, project_root, clock, "First task")
    second_id, _ = _seed(connection, project_root, clock, "Second task")
    reader = StateReader(database, FakeClock(START))
    panel = TaskDetailPanel(widget_context(reader, project_root, clock), first_id, fixed=True)
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 60)):
            await wait_until(lambda: panel.is_mounted, timeout=UI_WAIT_SECONDS)
            panel.open_view(EVENTS_FOLD)
            panel.open_view(TASK_VIEW_USAGE)
            bar = panel.query_one(f"#{AGENT_TIMELINE_IDS.filters}", TimelineFilterBar)
            bar.action_cycle_role()
            opened = frozenset({EVENTS_FOLD, TASK_VIEW_USAGE})
            assert panel.open_views == opened

            panel.refresh_after_resume()
            panel.select_task(second_id)
            await wait_until(
                lambda: (
                    second_id
                    in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            await wait_until(
                lambda: bool(list_rows(panel, TASK_DETAIL_TIMELINE_LIST)), timeout=UI_WAIT_SECONDS
            )
            assert panel.open_views == opened
            assert not bar.active
    finally:
        reader.close()


async def test_escape_returns_from_an_open_list_to_its_header_before_leaving(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed(connection, project_root, clock, "Escape steps")
    reader = StateReader(database, FakeClock(START))
    left_for_home: list[str] = []

    async def navigate_home() -> str:
        left_for_home.append(task_id)
        return "home"

    try:
        app = dashboard_app(
            reader,
            project_root,
            clock,
            widget_names=[],
            focus_task=task_id,
            navigate_home=navigate_home,
        )
        async with app.run_test(size=(100, 40)) as pilot:
            panel = app.query_one(TaskDetailPanel)
            await wait_until(lambda: panel.is_mounted, timeout=UI_WAIT_SECONDS)
            panel.open_view(EVENTS_FOLD)
            await wait_until(
                lambda: _focused_stop(pilot) == TASK_DETAIL_TIMELINE_LIST, timeout=UI_WAIT_SECONDS
            )

            await pilot.press("escape")
            await wait_until(lambda: _focused_stop(pilot) == EVENTS_FOLD, timeout=UI_WAIT_SECONDS)
            assert panel.open_views == frozenset({EVENTS_FOLD})
            assert left_for_home == []

            panel.open_view(TASK_VIEW_AGENT)
            await wait_until(lambda: _focused_stop(pilot) == AGENT_LIST, timeout=UI_WAIT_SECONDS)
            await pilot.press("f")
            await wait_until(
                lambda: _focused_stop(pilot) == AGENT_TIMELINE_IDS.filters,
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("escape")
            await wait_until(lambda: _focused_stop(pilot) == AGENT_LIST, timeout=UI_WAIT_SECONDS)
            assert left_for_home == []

            await pilot.press("escape", "escape")
            await wait_until(lambda: left_for_home == [task_id], timeout=UI_WAIT_SECONDS)
    finally:
        reader.close()


def _fold_header(panel: TaskDetailPanel, view_id: str) -> Widget:
    return next(fold.header for fold in panel.query(DetailFold) if fold.view_id == view_id)


async def test_clicking_fold_header_enters_its_list_and_closes_back_to_the_header(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed(connection, project_root, clock, "Selection marks")
    reader = StateReader(database, FakeClock(START))
    panel = TaskDetailPanel(widget_context(reader, project_root, clock), task_id, fixed=True)
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 40)) as pilot:
            await wait_until(lambda: panel.is_mounted, timeout=UI_WAIT_SECONDS)
            await wait_until(
                lambda: bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content),
                timeout=UI_WAIT_SECONDS,
            )
            header = _fold_header(panel, EVENTS_FOLD)
            await wait_until(partial(is_hit_at_center, pilot.app, header), timeout=UI_WAIT_SECONDS)
            await pilot.pause()
            await wait_until(partial(is_hit_at_center, pilot.app, header), timeout=UI_WAIT_SECONDS)
            click_offset = (header.size.width // 2, header.size.height // 2)
            assert await pilot.click(header, offset=click_offset)
            await wait_until(lambda: EVENTS_FOLD in panel.open_views, timeout=UI_WAIT_SECONDS)
            await wait_until(
                lambda: _focused_stop(pilot) == TASK_DETAIL_TIMELINE_LIST,
                timeout=UI_WAIT_SECONDS,
            )
            await wait_until(partial(is_hit_at_center, pilot.app, header), timeout=UI_WAIT_SECONDS)
            await pilot.pause()
            await wait_until(partial(is_hit_at_center, pilot.app, header), timeout=UI_WAIT_SECONDS)
            assert await pilot.click(header, offset=click_offset)
            await wait_until(lambda: EVENTS_FOLD not in panel.open_views, timeout=UI_WAIT_SECONDS)
            await wait_until(lambda: _focused_stop(pilot) == EVENTS_FOLD, timeout=UI_WAIT_SECONDS)
    finally:
        reader.close()
