"""Home Recent shows a bottom-aligned three-task window pageable by Up/Down and Later/Earlier."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, ListItem, ListView, Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.dashboard.detail import TASK_DETAIL_IDENTITY
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Status
from shikiru.plugins.widgets.queue import QueuePanel
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, dashboard_app, state_views
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.config.settings import Settings
    from shikiru.dashboard.app import DashboardApp
    from shikiru.domain.tasks import Task
    from shikiru.store.database import Database
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WAIT_SECONDS = 5
_WIDE = (120, 40)
_SHORT_WIDE = (80, 24)
_NARROW = (36, 20)
_TINY = (20, 8)
_WINDOW = 3
_LATER = "#task-browser-recent-later"
_EARLIER = "#task-browser-recent-earlier"
_INACTIVE = "-inactive"


def _finish(store: TaskStore, task: Task, status: Status) -> None:
    store.apply_transition(task.id, Transition.stay(task, step=None, status=status))


def _seed(
    connection: Connection,
    clock: FakeClock,
    *,
    active: int,
    terminal: int,
) -> tuple[list[str], list[str]]:
    store = TaskStore(connection, clock)
    active_ids = [
        seed_task(connection, clock, title=f"Active {index}").id for index in range(active)
    ]
    terminal_ids: list[str] = []
    for index in range(terminal):
        task = seed_task(connection, clock, title=f"Finished {index}")
        _finish(store, task, Status.DONE if index % 2 else Status.CANCELLED)
        terminal_ids.append(task.id)
        clock.advance(1)
    return active_ids, terminal_ids


def _recent_ids(app: DashboardApp) -> tuple[str, ...]:
    recent = app.query_one("#task-browser-recent-list", ListView)
    return tuple(
        item.id.removeprefix("task-row-")
        for item in recent.children
        if isinstance(item, ListItem) and item.id is not None
    )


def _shown_task(app: DashboardApp) -> str:
    identity = plain_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
    return identity.split(" · ")[0].split()[0] if identity else ""


async def _wait_for_recent(app: DashboardApp, expected: tuple[str, ...]) -> None:
    await wait_until(lambda: _recent_ids(app) == expected, timeout=_WAIT_SECONDS)


async def _wait_for_selection(app: DashboardApp, task_id: str) -> None:
    await wait_until(lambda: _shown_task(app) == task_id, timeout=_WAIT_SECONDS)


async def _press_to(pilot: Pilot[None], app: DashboardApp, key: str, task_id: str) -> None:
    await pilot.press(key)
    await _wait_for_selection(app, task_id)


def _highlighted_ids(app: DashboardApp) -> tuple[str, ...]:
    highlighted = (
        app.query_one(selector, ListView).highlighted_child
        for selector in ("#task-browser-list", "#task-browser-recent-list")
    )
    return tuple(
        item.id.removeprefix("task-row-")
        for item in highlighted
        if item is not None and item.id is not None
    )


async def _poll_changes(clock: FakeClock) -> None:
    await wait_until(clock.has_pending_sleep, timeout=_WAIT_SECONDS)
    clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)


async def _click_pager(pilot: Pilot[None], app: DashboardApp, selector: str) -> None:
    await wait_until(lambda: app.query_one(selector).region.width > 1, timeout=_WAIT_SECONDS)
    await pilot.click(selector)


def _with_recent_limit(project_dir: Path, limit: int) -> Settings:
    settings = load_settings(project_dir)
    return settings.model_copy(
        update={"dashboard": settings.dashboard.model_copy(update={"recent_task_limit": limit})}
    )


def _command_count(reader: StateReader) -> int:
    return reader.command_history(limit=1).total_count


async def test_up_and_down_walk_seven_terminal_tasks_across_window_edges_in_both_directions(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    (first_active, second_active), terminal = _seed(connection, clock, active=2, terminal=7)
    newest_first = tuple(reversed(terminal))
    pages = (newest_first[:3], newest_first[3:6], newest_first[6:])
    reader = StateReader(database, clock)
    try:
        commands = _command_count(reader)
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE) as pilot:
            await _wait_for_recent(app, pages[0])
            await _wait_for_selection(app, first_active)

            await _press_to(pilot, app, "down", second_active)
            await _press_to(pilot, app, "down", pages[0][0])
            await _press_to(pilot, app, "down", pages[0][1])
            await _press_to(pilot, app, "down", pages[0][2])
            await _press_to(pilot, app, "down", pages[1][0])
            await _wait_for_recent(app, pages[1])
            assert _highlighted_ids(app) == (pages[1][0],)
            await _press_to(pilot, app, "down", pages[1][1])
            await _press_to(pilot, app, "down", pages[1][2])
            await _press_to(pilot, app, "down", pages[2][0])
            await _wait_for_recent(app, pages[2])
            await _press_to(pilot, app, "down", first_active)
            assert _recent_ids(app) == pages[2]

            await _press_to(pilot, app, "up", pages[2][0])
            await _press_to(pilot, app, "up", pages[1][2])
            await _wait_for_recent(app, pages[1])
            await _press_to(pilot, app, "up", pages[1][1])
            await _press_to(pilot, app, "up", pages[1][0])
            await _press_to(pilot, app, "up", pages[0][2])
            await _wait_for_recent(app, pages[0])
            await _press_to(pilot, app, "up", pages[0][1])
            await _press_to(pilot, app, "up", pages[0][0])
            await _press_to(pilot, app, "up", second_active)
            await _press_to(pilot, app, "up", first_active)
            await _press_to(pilot, app, "up", pages[0][2])
            assert _command_count(reader) == commands
    finally:
        reader.close()


async def test_recent_keeps_only_the_newest_thirty_terminal_tasks_while_history_lists_all(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _, terminal = _seed(connection, clock, active=0, terminal=33)
    newest_first = tuple(reversed(terminal))
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE) as pilot:
            await _wait_for_recent(app, newest_first[:3])
            for position in range(1, 30):
                await _press_to(pilot, app, "down", newest_first[position])
            await _wait_for_recent(app, newest_first[27:30])
            await _press_to(pilot, app, "down", newest_first[0])
            await _wait_for_recent(app, newest_first[:3])
        history = state_views(reader, project_dir, clock, StubServices()).task_browser_pages(
            task_filter=TaskBrowserFilter.HISTORY,
            search="",
            limit=_WINDOW,
            page_count=1,
            selected_task_id=None,
        )
        assert history[-1].matching_count == len(terminal)
    finally:
        reader.close()


async def test_a_limit_that_is_not_a_window_multiple_gives_a_short_last_page_at_a_stable_heading(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _, terminal = _seed(connection, clock, active=1, terminal=10)
    newest_first = tuple(reversed(terminal))
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(
            reader,
            project_dir,
            clock,
            widget_names=["queue"],
            settings=_with_recent_limit(project_dir, 7),
        )
        async with app.run_test(size=_WIDE) as pilot:
            heading = app.query_one("#task-browser-recent-heading", Horizontal)
            await _wait_for_recent(app, newest_first[:3])
            heading_y = heading.region.y
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[6:7])
            assert heading.region.y == heading_y
            assert app.query_one(_EARLIER).has_class(_INACTIVE)
            assert not app.query_one(_LATER).has_class(_INACTIVE)
    finally:
        reader.close()


@pytest.mark.parametrize("terminal", [0, 1, 3])
async def test_the_pager_is_hidden_with_three_or_fewer_recent_tasks(
    database: Database, connection: Connection, tmp_path: Path, terminal: int
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed(connection, clock, active=1, terminal=terminal)
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE):
            await wait_until(lambda: len(_recent_ids(app)) == terminal, timeout=_WAIT_SECONDS)
            assert not app.query_one(_LATER).display
            assert not app.query_one(_EARLIER).display
    finally:
        reader.close()


async def test_pager_controls_are_disabled_at_their_boundary_and_page_by_click(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _, terminal = _seed(connection, clock, active=1, terminal=7)
    newest_first = tuple(reversed(terminal))
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE) as pilot:
            later, earlier = app.query_one(_LATER), app.query_one(_EARLIER)
            await _wait_for_recent(app, newest_first[:3])
            assert later.has_class(_INACTIVE)
            assert not earlier.has_class(_INACTIVE)
            await _click_pager(pilot, app, _LATER)
            await pilot.pause()
            assert _recent_ids(app) == newest_first[:3]
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            assert not later.has_class(_INACTIVE)
            assert not earlier.has_class(_INACTIVE)
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[6:])
            await _click_pager(pilot, app, _EARLIER)
            await pilot.pause()
            assert _recent_ids(app) == newest_first[6:]
            await _click_pager(pilot, app, _LATER)
            await _wait_for_recent(app, newest_first[3:6])
    finally:
        reader.close()


async def test_clicking_a_pager_control_keeps_the_selected_task_and_matches_the_keyboard_page(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    (active,), terminal = _seed(connection, clock, active=1, terminal=7)
    newest_first = tuple(reversed(terminal))
    reader = StateReader(database, clock)
    try:
        commands = _command_count(reader)
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE) as pilot:
            await _wait_for_recent(app, newest_first[:3])
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            assert _shown_task(app) == active
            await _click_pager(pilot, app, _LATER)
            await _wait_for_recent(app, newest_first[:3])

            await pilot.click(f"#task-row-{newest_first[1]}")
            await _wait_for_selection(app, newest_first[1])
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            assert _shown_task(app) == newest_first[1]
            assert _highlighted_ids(app) == ()
            await _click_pager(pilot, app, _LATER)
            await _wait_for_recent(app, newest_first[:3])
            await wait_until(
                lambda: _highlighted_ids(app) == (newest_first[1],), timeout=_WAIT_SECONDS
            )

            await pilot.press("down")
            await pilot.press("down")
            await _wait_for_recent(app, newest_first[3:6])
            await _wait_for_selection(app, newest_first[3])
            assert _command_count(reader) == commands
    finally:
        reader.close()


async def test_a_new_completion_a_search_or_a_filter_change_resets_to_the_newest_window(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    (_, running), terminal = _seed(connection, clock, active=2, terminal=7)
    newest_first = tuple(reversed(terminal))
    store = TaskStore(connection, clock)
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE) as pilot:
            queue = app.query_one(QueuePanel)
            await _wait_for_recent(app, newest_first[:3])
            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])

            await queue.activate_filter(TaskBrowserFilter.HISTORY)
            await queue.activate_filter(TaskBrowserFilter.ALL)
            await _wait_for_recent(app, newest_first[:3])

            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            search = app.query_one("#task-browser-search", Input)
            search.value = "Active"
            await wait_until(lambda: not _recent_ids(app), timeout=_WAIT_SECONDS)
            search.value = ""
            await _wait_for_recent(app, newest_first[:3])

            await _click_pager(pilot, app, _EARLIER)
            await _wait_for_recent(app, newest_first[3:6])
            _finish(store, store.require(running), Status.DONE)
            await _poll_changes(clock)
            await _wait_for_recent(app, (running, *newest_first[:2]))
    finally:
        reader.close()


@pytest.mark.parametrize("terminal", [1, 2])
async def test_fewer_than_three_recent_tasks_leave_blank_space_above_a_bottom_aligned_block(
    database: Database, connection: Connection, tmp_path: Path, terminal: int
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed(connection, clock, active=2, terminal=terminal)
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_WIDE):
            await wait_until(lambda: len(_recent_ids(app)) == terminal, timeout=_WAIT_SECONDS)
            heading = app.query_one("#task-browser-recent-heading", Horizontal)
            recent = app.query_one("#task-browser-recent-list", ListView)
            last_active = app.query_one("#task-browser-list", ListView).children[-1]
            queue = app.query_one("#queue")
            assert recent.region.bottom == queue.region.bottom
            assert heading.region.y - last_active.region.bottom > _WINDOW
    finally:
        reader.close()


@pytest.mark.parametrize("size", [_WIDE, _SHORT_WIDE])
async def test_the_recent_block_stays_at_the_bottom_whatever_the_length_of_the_active_list(
    database: Database, connection: Connection, tmp_path: Path, size: tuple[int, int]
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _seed(connection, clock, active=2, terminal=7)
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=size):
            await wait_until(lambda: len(_recent_ids(app)) == _WINDOW, timeout=_WAIT_SECONDS)
            heading = app.query_one("#task-browser-recent-heading", Horizontal)
            recent = app.query_one("#task-browser-recent-list", ListView)
            queue = app.query_one("#queue")
            few_active = (heading.region.y, recent.region.bottom, queue.region.bottom)
            for index in range(12):
                seed_task(connection, clock, title=f"More active {index}")
            await _poll_changes(clock)
            await wait_until(
                lambda: len(app.query_one("#task-browser-list", ListView).children) > 2,
                timeout=_WAIT_SECONDS,
            )
            assert (heading.region.y, recent.region.bottom, queue.region.bottom) == few_active
            assert recent.region.bottom == queue.region.bottom
    finally:
        reader.close()


async def test_compact_layouts_keep_the_recent_block_in_natural_flow_and_the_selection_visible(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _, terminal = _seed(connection, clock, active=2, terminal=7)
    newest = terminal[-1]
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_NARROW) as pilot:
            await wait_until(lambda: len(_recent_ids(app)) == _WINDOW, timeout=_WAIT_SECONDS)
            block = app.query_one("#task-browser-recent", Vertical)
            active = app.query_one("#task-browser-list", ListView)
            assert block.region.y == active.region.bottom
            await pilot.press("down", "down")
            await wait_until(
                lambda: app.screen.can_view_partial(app.query_one(f"#task-row-{newest}")),
                timeout=_WAIT_SECONDS,
            )
    finally:
        reader.close()


async def test_the_selected_active_row_stays_visible_at_the_smallest_size(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    active, _ = _seed(connection, clock, active=14, terminal=7)
    reader = StateReader(database, clock)
    try:
        app = dashboard_app(reader, project_dir, clock, widget_names=["queue"])
        async with app.run_test(size=_TINY) as pilot:
            await wait_until(
                lambda: len(app.query_one("#task-browser-list", ListView).children) == len(active),
                timeout=_WAIT_SECONDS,
            )
            for task_id in active[1:]:
                await pilot.press("down")
                await wait_until(
                    lambda task_id=task_id: (
                        bool(app.query(f"#task-row-{task_id}"))
                        and app.screen.can_view_partial(app.query_one(f"#task-row-{task_id}"))
                    ),
                    timeout=_WAIT_SECONDS,
                )
    finally:
        reader.close()
