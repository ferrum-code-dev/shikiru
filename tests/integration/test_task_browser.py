from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.widgets import Button, Input, ListView, Static

from shikiru.agents.contracts import role_dir
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import EscalationOption
from shikiru.domain.events import EventKind
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status
from shikiru.paths import run_dir, turns_dir
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore, fetch_task_browser_pages
from shikiru.view_log import (
    VIEW_SESSION_STARTED_SIGNAL,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TURN_KEY,
    view_log_path,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    ignore_palette_action,
    state_views,
    widget_context,
)
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.store.database import Database
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
PAGE_SIZE = 24
PAGE_COUNT = 2
PAGE_SELECT_STATEMENTS = 3
STALL_SECONDS = 2
STALL_TURN_NAME = "001-implement-i1"
READING_INDEX = 10
READING_SCROLL_POSITION = 12


def _start_task(connection: Connection, clock: FakeClock, task_id: str) -> None:
    task = TaskStore(connection, clock).require(task_id)
    store = TaskStore(connection, clock)
    store.set_slot(task_id, 0)
    store.apply_transition(task_id, first_step(task))


def _finish_task(connection: Connection, clock: FakeClock, task_id: str) -> None:
    task = TaskStore(connection, clock).require(task_id)
    TaskStore(connection, clock).apply_transition(
        task_id,
        Transition.stay(task, step=task.step, status=Status.DONE),
    )


def _static_text(widget: Static) -> str:
    rendered = widget.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


def _visible_task_ids(list_view: ListView) -> tuple[str, ...]:
    return tuple(
        item.id.removeprefix("task-row-")
        for item in list_view.children
        if item.id is not None and item.region.overlaps(list_view.scrollable_content_region)
    )


async def test_task_browser_queries_apply_filter_order_search_and_page_counts(
    connection: Connection, reader: StateReader
) -> None:
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    queued = seed_task(connection, clock, title="Ädd Parser retry", priority=2)
    high_priority = seed_task(connection, clock, title="Repair other", priority=8)
    requeued = seed_task(connection, clock, title="Requeued", priority=0)
    store.set_requeued(requeued.id)
    waiting = seed_task(connection, clock, title="Paused work", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, waiting.id)
    store.set_holds(waiting.id, frozenset({Hold.PAUSED}))
    active = seed_task(connection, clock, title="Unblocked work", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, active.id)
    decision = seed_task(connection, clock, title="Needs a decision")
    task_store = TaskStore(connection, clock)
    task_store.apply_transition(
        decision.id,
        Transition.stay(decision, step=None, status=Status.ESCALATED),
    )
    EscalationStore(connection, clock).open(
        decision.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="Choose the parser behavior",
        packet_path="packet.md",
        options=(EscalationOption(label=ResolutionKind.ANSWER, recommended=True),),
    )
    first_finished = seed_task(connection, clock, title="Earlier history")
    _finish_task(connection, clock, first_finished.id)
    clock.advance(1)
    latest_finished = seed_task(connection, clock, title="Later history")
    _finish_task(connection, clock, latest_finished.id)

    waiting_page = reader.task_browser_page(
        category=TaskBrowserFilter.WAITING.value,
        search="",
        limit=20,
        offset=0,
    )
    active_page = reader.task_browser_page(
        category=TaskBrowserFilter.ACTIVE.value,
        search="",
        limit=20,
        offset=0,
    )
    history_page = reader.task_browser_page(
        category=TaskBrowserFilter.HISTORY.value,
        search="",
        limit=1,
        offset=0,
    )
    history_next_page = reader.task_browser_page(
        category=TaskBrowserFilter.HISTORY.value,
        search="",
        limit=1,
        offset=1,
    )
    searched_page = reader.task_browser_page(
        category=TaskBrowserFilter.WAITING.value,
        search="äDD pArSeR",
        limit=20,
        offset=0,
    )
    no_matches = reader.task_browser_page(
        category=TaskBrowserFilter.WAITING.value,
        search="unmatched title",
        limit=20,
        offset=0,
    )

    assert [task.id for task in waiting_page.tasks] == [
        requeued.id,
        high_priority.id,
        queued.id,
        waiting.id,
        decision.id,
    ]
    assert [task.id for task in active_page.tasks] == [decision.id, waiting.id, active.id]
    assert [task.id for task in history_page.tasks] == [latest_finished.id]
    assert history_page.matching_count == 2
    assert history_page.filtered_count == 2
    assert [task.id for task in history_next_page.tasks] == [first_finished.id]
    assert history_next_page.matching_count == history_page.matching_count
    assert history_page.project_count == 8
    assert searched_page.matching_count == 1
    assert [task.id for task in searched_page.tasks] == [queued.id]
    assert no_matches.matching_count == 0
    assert no_matches.filtered_count == 5
    assert no_matches.project_count == 8


async def test_active_browser_places_a_diagnosed_stall_before_older_active_rows(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    ordinary = seed_task(connection, clock, title="Ordinary task", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, ordinary.id)
    clock.advance(1)
    stalled = seed_task(connection, clock, title="Stalled task", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, stalled.id)
    task = TaskStore(connection, clock).require(stalled.id)
    step = task.step
    assert step is not None
    EventLog(connection, clock).append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="implementer turn started",
        data={"step": step.value, "role": Role.IMPLEMENTER.value, "iteration": task.iteration},
    )
    role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    role_root.mkdir(parents=True)
    turn_directory = turns_dir(project_dir, task.id) / STALL_TURN_NAME
    turn_directory.mkdir(parents=True)
    (role_root / CURRENT_TURN_LINK_NAME).symlink_to(turn_directory, target_is_directory=True)
    view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value).write_text(
        json.dumps(
            {
                VIEW_TIME_KEY: clock.now().isoformat(),
                VIEW_TURN_KEY: STALL_TURN_NAME,
                VIEW_SIGNAL_KEY: VIEW_SESSION_STARTED_SIGNAL,
            }
        )
        + "\n"
    )
    clock.advance(STALL_SECONDS + 1)
    settings = load_settings(project_dir)
    timeouts = settings.orchestration.timeouts.model_copy(update={"stall": STALL_SECONDS})
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts})}
    )
    views = state_views(
        reader,
        project_dir,
        clock,
        StubServices(),
        settings=settings,
    )

    page = views.task_browser(
        task_filter=TaskBrowserFilter.ACTIVE,
        search="",
        offset=0,
        limit=1,
        selected_task_id=None,
    )

    assert [row.task_id for row in page.rows] == [stalled.id]
    assert page.matching_count == 2
    assert page.rows[0].diagnosed_stall


async def test_browser_keeps_selection_while_search_filter_state_and_history_change(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    selected = seed_task(connection, clock, title="Parser retry selected", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, selected.id)
    queued = seed_task(connection, clock, title="Parser retry queued")
    services = StubServices()
    context = widget_context(reader, project_dir, clock, services=services)
    widget = cast("QueuePanel", QueueWidget().create(context))

    async with WidgetHostApp(widget).run_test(size=(90, 100)) as pilot:
        await pilot.pause()
        search = widget.query_one("#task-browser-search", Input)
        search.value = "pArSeR"
        await pilot.pause()
        await pilot.click("#task-filter-waiting")
        await pilot.pause()

        assert widget.query_one(f"#task-row-{queued.id}")
        assert "Waiting" in _static_text(widget.query_one("#task-browser-count", Static))
        selected_preview = widget.query_one("#task-browser-preview", Static)
        assert selected.id in _static_text(selected_preview)
        assert "Parser retry queued" in _static_text(
            widget.query_one(f"#task-row-{queued.id} > Static", Static)
        )

        await pilot.click("#task-browser-clear")
        await pilot.click("#task-filter-active")
        await pilot.pause()
        await pilot.press("enter")

        decision = seed_task(connection, clock, title="Urgent decision")
        task_store = TaskStore(connection, clock)
        task_store.apply_transition(
            decision.id,
            Transition.stay(decision, step=None, status=Status.ESCALATED),
        )
        EscalationStore(connection, clock).open(
            decision.id,
            step=None,
            reason=EscalationReason.DECISION_REQUIRED,
            headline="Choose a behavior",
            packet_path="packet.md",
            options=(EscalationOption(label=ResolutionKind.ANSWER, recommended=True),),
        )
        await wait_until(
            lambda: (
                decision.id
                in [
                    (item.id or "").removeprefix("task-row-")
                    for item in widget.query_one("#task-browser-list", ListView).children
                ]
            ),
            timeout=5.0,
        )
        assert selected.id in _static_text(selected_preview)
        await pilot.press("up")
        assert decision.id in _static_text(selected_preview)
        await pilot.press("down")
        assert selected.id in _static_text(selected_preview)

        _finish_task(connection, clock, selected.id)
        await wait_until(lambda: "done" in _static_text(selected_preview), timeout=5.0)
        assert selected.id in _static_text(selected_preview)

        assert not widget.query_one("#task-browser-reveal").disabled
        assert await pilot.click("#task-browser-reveal", offset=(5, 1))
        await pilot.pause()
        assert widget.query_one(f"#task-row-{selected.id}")


async def test_history_load_more_keeps_the_selected_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks: list[Task] = []
    for index in range(PAGE_SIZE + 1):
        old_task = seed_task(connection, clock, title=f"Old result {index}")
        tasks.append(old_task)
        _finish_task(connection, clock, old_task.id)
        clock.advance(1)
    selected = tasks[0]
    context = replace(
        widget_context(reader, project_dir, clock, services=StubServices()),
        focus_task=selected.id,
    )
    widget = cast("QueuePanel", QueueWidget().create(context))

    async with WidgetHostApp(widget).run_test(size=(90, 100)) as pilot:
        await pilot.pause()
        await pilot.click("#task-filter-history")
        await pilot.pause()
        task_list = widget.query_one("#task-browser-list", ListView)
        assert len(task_list.children) == PAGE_SIZE
        assert selected.id in _static_text(widget.query_one("#task-browser-preview", Static))
        first_page = tuple(item.id for item in task_list.children)
        assert selected.id not in first_page
        load_more = widget.query_one("#task-browser-more", Button)
        assert not load_more.disabled
        assert await pilot.click("#task-browser-more")
        await wait_until(
            lambda: (
                len(task_list.children) == PAGE_SIZE + 1
                and str(PAGE_SIZE + 1)
                in _static_text(widget.query_one("#task-browser-count", Static))
            ),
            timeout=5.0,
        )
        assert len(task_list.children) == PAGE_SIZE + 1
        assert tuple(item.id for item in task_list.children[:PAGE_SIZE]) == first_page
        assert widget.query_one("#task-browser-more", Button).disabled
        assert task_list.children[-1].id == f"task-row-{selected.id}"
        assert selected.id in _static_text(widget.query_one("#task-browser-preview", Static))


async def test_history_load_more_reconciles_a_new_task_before_watch_poll(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks: list[Task] = []
    for index in range(PAGE_SIZE + 1):
        task = seed_task(connection, clock, title=f"History task {index}")
        tasks.append(task)
        _finish_task(connection, clock, task.id)
        if index < PAGE_SIZE:
            clock.advance(1)
    selected = tasks[-1 - READING_INDEX]
    reader = StateReader(database, clock)
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    try:
        async with WidgetHostApp(widget).run_test(size=(90, 100)) as pilot:
            await pilot.pause()
            await pilot.click("#task-filter-history")
            await pilot.pause()
            list_view = widget.query_one("#task-browser-list", ListView)
            list_view.focus()
            list_view.index = READING_INDEX
            await pilot.pause()
            list_view.scroll_to(y=READING_SCROLL_POSITION, animate=False)
            await pilot.pause()
            visible_before = _visible_task_ids(list_view)
            assert selected.id in visible_before
            assert selected.id in _static_text(widget.query_one("#task-browser-preview", Static))
            await wait_until(clock.has_pending_sleep, timeout=5.0)

            new_task = seed_task(connection, clock, title="Inserted before the next page")
            _finish_task(connection, clock, new_task.id)
            assert len(list_view.children) == PAGE_SIZE
            assert f"of {PAGE_SIZE + 1}" in _static_text(
                widget.query_one("#task-browser-count", Static)
            )

            assert await pilot.click("#task-browser-more")
            await wait_until(
                lambda: (
                    len(list_view.children) == PAGE_SIZE + 2
                    and f"of {PAGE_SIZE + 2}"
                    in _static_text(widget.query_one("#task-browser-count", Static))
                ),
                timeout=5.0,
            )
            await wait_until(
                lambda: _visible_task_ids(list_view) == visible_before,
                timeout=5.0,
            )

            loaded_ids = tuple(
                item.id.removeprefix("task-row-") for item in list_view.children if item.id
            )
            assert loaded_ids == (new_task.id, *(task.id for task in reversed(tasks)))
            assert len(set(loaded_ids)) == len(loaded_ids)
            assert selected.id in _visible_task_ids(list_view)
            assert selected.id in _static_text(widget.query_one("#task-browser-preview", Static))
            assert list_view.has_focus
    finally:
        reader.close()


def test_browser_page_batch_keeps_one_snapshot_during_a_writer_commit(
    connection: Connection, database: Database
) -> None:
    clock = FakeClock(START)
    tasks: list[Task] = []
    for index in range(PAGE_SIZE + 1):
        task = seed_task(connection, clock, title=f"Snapshot history {index}")
        tasks.append(task)
        _finish_task(connection, clock, task.id)
        clock.advance(1)

    reader_connection = database.connect_readonly()
    select_count = 0
    inserted_tasks: list[Task] = []

    def commit_between_page_reads(statement: str) -> None:
        nonlocal select_count
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        select_count += 1
        if select_count == PAGE_SELECT_STATEMENTS + 1:
            inserted = seed_task(connection, clock, title="Concurrent history task")
            _finish_task(connection, clock, inserted.id)
            inserted_tasks.append(inserted)

    reader_connection.set_trace_callback(commit_between_page_reads)
    try:
        pages = fetch_task_browser_pages(
            reader_connection,
            category=TaskBrowserFilter.HISTORY.value,
            search="",
            limit=PAGE_SIZE,
            page_count=PAGE_COUNT,
        )
    finally:
        reader_connection.set_trace_callback(None)
        reader_connection.close()

    rows = tuple(task for page in pages for task in page.tasks)
    assert len(inserted_tasks) == 1
    assert tuple(task.id for task in rows) == tuple(task.id for task in reversed(tasks))
    assert len({task.id for task in rows}) == PAGE_SIZE + 1
    assert all(page.matching_count == PAGE_SIZE + 1 for page in pages)
    assert rows[0].id != inserted_tasks[0].id


async def test_state_refresh_preserves_visible_list_position_and_selected_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks = [
        seed_task(connection, clock, title=f"Queued task {index}") for index in range(PAGE_SIZE)
    ]
    widget = cast("QueuePanel", QueueWidget().create(widget_context(reader, project_dir, clock)))

    async with WidgetHostApp(widget).run_test(size=(90, 100)) as pilot:
        await pilot.pause()
        list_view = widget.query_one("#task-browser-list", ListView)
        list_view.focus()
        list_view.index = READING_INDEX
        await pilot.pause()
        list_view.scroll_to(y=READING_SCROLL_POSITION, animate=False)
        await pilot.pause()
        visible_before = _visible_task_ids(list_view)
        selected = tasks[READING_INDEX]
        assert visible_before
        assert selected.id in visible_before

        TaskStore(connection, clock).set_priority(tasks[-1].id, -1)
        await wait_until(
            lambda: (
                "priority -1"
                in _static_text(widget.query_one(f"#task-row-{tasks[-1].id} > Static", Static))
            ),
            timeout=5.0,
        )
        await wait_until(lambda: _visible_task_ids(list_view) == visible_before, timeout=5.0)

        assert list_view.has_focus
        assert selected.id in _static_text(widget.query_one("#task-browser-preview", Static))


async def test_dashboard_slash_focuses_search_and_a_slash_inside_search_is_text(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    settings = load_settings(project_dir)
    services = StubServices()
    empty_project = reader.task_browser_page(
        category=TaskBrowserFilter.ALL.value,
        search="",
        limit=PAGE_SIZE,
        offset=0,
    )
    assert empty_project.matching_count == 0
    assert empty_project.project_count == 0
    app = DashboardApp(
        focus_task=None,
        views=state_views(reader, project_dir, clock, services),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=ignore_palette_action,
        widget_names=["queue", "usage"],
    )

    async with app.run_test(size=(90, 60)) as pilot:
        await pilot.pause()
        search = app.query_one("#task-browser-search", Input)
        await pilot.click("#section-tab-usage")
        await pilot.click("#workspace-expand")
        await pilot.pause()
        assert not app.query_one("#section-workspace").display
        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#section-workspace").display
        assert search.has_focus
        assert search.is_on_screen
        assert search.value == ""
        await pilot.press("slash")

        assert search.value == "/"
        await pilot.click("#workspace-help")
        await pilot.pause()
        await pilot.press("slash")
        assert not search.has_focus
        assert app.screen.query_one("#workspace-help-frame")
