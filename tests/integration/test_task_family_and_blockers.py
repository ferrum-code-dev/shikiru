from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Input, ListView, Static

from shikiru.agents.contracts import QuotaWindow, Rung
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp, TaskDetailScreen, TaskPaletteRoute
from shikiru.dashboard.contract import ResourceView, TaskBrowserFilter
from shikiru.dashboard.detail import (
    AGENT_TIMELINE_IDS,
    TASK_DETAIL_FEEDBACK,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RELATED_LIST,
    TASK_DETAIL_RELATIONSHIPS,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_WORKSPACE,
    TaskDetailPanel,
)
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.timeline_filter import TimelineFilterBar
from shikiru.domain.events import EventKind
from shikiru.domain.reports import NewTask, Verdict
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step, next_step
from shikiru.domain.tasks import Hold, Mode, Role, Status, StatusReason, TaskOrigin
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore, fetch_task_relationships
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    choose_details_entry,
    fixed_settings,
    focus_inspector_details,
    ignore_palette_action,
    list_rows,
    state_views,
    widget_context,
)
from tests.support.quota_waits import record_quota_wait
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.store.database import Database
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

TASK_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
TASK_BROWSER_PAGE_SIZE = 24
RESOURCE_SAMPLE_AGE_SECONDS = 120
QUOTA_BLOCK_SECONDS = 300


def _task_detail_app(
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    services: StubServices,
) -> DashboardApp:
    settings = load_settings(project_dir)
    views = state_views(reader, project_dir, clock, services, settings=settings)
    return DashboardApp(
        focus_task=None,
        views=views,
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        settings=fixed_settings(reader, settings),
        open_palette=ignore_palette_action,
        widget_names=["queue"],
    )


def _start_task(connection: Connection, clock: FakeClock, task_id: str) -> None:
    task = TaskStore(connection, clock).require(task_id)
    store = TaskStore(connection, clock)
    store.set_slot(task_id, 0)
    store.apply_transition(task_id, first_step(task))


def _nested_plan_family(
    connection: Connection,
    clock: FakeClock,
) -> tuple[Task, Task, tuple[Task, ...]]:
    root = seed_task(connection, clock, title="Parser root plan", mode=Mode.PLAN)
    store = TaskStore(connection, clock)
    [nested] = store.apply_scope_change(
        root.id,
        Transition.stay(root, step=Step.INTEGRATE, status=Status.DONE),
        [NewTask(title="Parser nested plan", request="plan parser work")],
        origin=TaskOrigin.LEADER,
    )
    nested_transition = replace(
        Transition.stay(nested, step=Step.INTEGRATE, status=Status.DONE),
        mode=Mode.PLAN,
    )
    children = store.apply_scope_change(
        nested.id,
        nested_transition,
        [
            NewTask(title="Parser implementation", request="implement parser"),
            NewTask(title="Parser fixture update", request="update fixtures"),
        ],
        origin=TaskOrigin.LEADER,
    )
    return root, nested, tuple(children)


@dataclass(frozen=True)
class _PaginatedRelationshipTasks:
    dependencies: tuple[Task, ...]
    superseded: Task
    replacements: tuple[Task, ...]
    dependents: tuple[Task, ...]
    blocked: Task


def _seed_paginated_relationship_tasks(
    connection: Connection,
    clock: FakeClock,
    settings: Settings,
) -> _PaginatedRelationshipTasks:
    store = TaskStore(connection, clock)
    dependencies = tuple(
        seed_task(connection, clock, title=f"External dependency {index}")
        for index in range(TASK_BROWSER_PAGE_SIZE + 1)
    )
    for dependency in dependencies[:-1]:
        store.apply_transition(
            dependency.id,
            Transition.stay(dependency, step=None, status=Status.DONE),
        )
    superseded = seed_task(
        connection,
        clock,
        title="Paginated replacement family",
        mode=Mode.IMPLEMENT,
        depends_on=tuple(item.id for item in dependencies),
    )
    store.apply_transition(superseded.id, Transition.stay(superseded, step=Step.VERIFY))
    superseded = store.require(superseded.id)
    verdict = Verdict(
        decision="supersede",
        summary="Split the replacement work",
        new_tasks=[
            NewTask(title=f"Replacement {index}", request=f"replacement {index}")
            for index in range(TASK_BROWSER_PAGE_SIZE + 1)
        ],
    )
    replacements = store.apply_scope_change(
        superseded.id,
        next_step(superseded, verdict, settings),
        verdict.new_tasks,
        origin=TaskOrigin.LEADER,
    )
    EventLog(connection, clock).append(
        EventKind.SCOPE_CHANGE,
        task_id=superseded.id,
        message="Recorded replacement task IDs",
        data={"new_task_ids": [item.id for item in replacements]},
    )
    dependents = tuple(
        seed_task(
            connection,
            clock,
            title=f"Dependent task {index}",
            depends_on=(superseded.id,),
        )
        for index in range(TASK_BROWSER_PAGE_SIZE + 1)
    )
    blocked = seed_task(
        connection,
        clock,
        title="Blocked by the last dependency",
        depends_on=tuple(item.id for item in dependencies),
    )
    store.set_wait_reason(blocked.id, WaitReason.DEPENDENCY)
    return _PaginatedRelationshipTasks(
        dependencies=dependencies,
        superseded=superseded,
        replacements=tuple(replacements),
        dependents=dependents,
        blocked=blocked,
    )


def _assert_relationship_pages(
    reader: StateReader,
    tasks: _PaginatedRelationshipTasks,
) -> None:
    first_page = reader.task_relationships(tasks.superseded.id)
    last_page = reader.task_relationships(
        tasks.superseded.id,
        offset=TASK_BROWSER_PAGE_SIZE,
    )
    assert first_page is not None
    assert first_page.has_more
    assert first_page.family_child_count == TASK_BROWSER_PAGE_SIZE + 1
    assert len(first_page.family_children) == TASK_BROWSER_PAGE_SIZE
    assert len(first_page.dependencies) == TASK_BROWSER_PAGE_SIZE
    assert len(first_page.dependents) == TASK_BROWSER_PAGE_SIZE
    assert len(first_page.replacements) == TASK_BROWSER_PAGE_SIZE
    assert last_page is not None
    assert last_page.page_offset == TASK_BROWSER_PAGE_SIZE
    assert [item.id for item in last_page.family_children] == [tasks.replacements[-1].id]
    assert [item.id for item in last_page.dependencies] == [tasks.dependencies[-1].id]
    assert [item.id for item in last_page.dependents] == [tasks.dependents[-1].id]
    assert [item.id for item in last_page.replacements] == [tasks.replacements[-1].id]


async def _open_browser_task(
    app: DashboardApp,
    pilot: Pilot[None],
    title: str,
    task_id: str,
) -> None:
    home = app.screen
    search = app.query_one("#task-browser-search", Input)
    search.value = title
    await pilot.pause()
    await pilot.click(f"#task-row-{task_id}")
    await wait_until(
        lambda: task_id in _static_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
        timeout=5.0,
    )
    assert app.screen is home
    app.route_task_palette(task_id, TaskPaletteRoute.INSPECT)
    await wait_until(lambda: isinstance(app.screen, TaskDetailScreen), timeout=5.0)
    await wait_until(
        lambda: task_id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
        timeout=5.0,
    )


async def _open_detail_view(
    panel: TaskDetailPanel,
    pilot: Pilot[None],
    view_id: str,
) -> TaskDetailPanel:
    await focus_inspector_details(pilot)
    await choose_details_entry(pilot, view_id)
    await wait_until(
        lambda: (
            (
                isinstance(pilot.app.screen, TaskDetailScreen)
                and view_id
                in pilot.app.screen.query_one("#task-detail-panel", TaskDetailPanel).open_views
            )
            or view_id in panel.open_views
        ),
        timeout=UI_WAIT_SECONDS,
    )
    if isinstance(pilot.app.screen, TaskDetailScreen):
        return pilot.app.screen.query_one("#task-detail-panel", TaskDetailPanel)
    return panel


async def _open_relationships(panel: TaskDetailPanel, pilot: Pilot[None]) -> TaskDetailPanel:
    return await _open_detail_view(panel, pilot, TASK_VIEW_RELATIONSHIPS)


def _related_rows(panel: TaskDetailPanel) -> list[str]:
    return list_rows(panel, TASK_DETAIL_RELATED_LIST)


async def _select_related_row(panel: TaskDetailPanel, pilot: Pilot[None], task_id: str) -> None:
    related = panel.query_one(f"#{TASK_DETAIL_RELATED_LIST}", RowList)
    related.focus()
    await wait_until(
        lambda: any(task_id in row for row in _related_rows(panel)), timeout=UI_WAIT_SECONDS
    )
    for _ in range(len(_related_rows(panel))):
        if task_id in _related_rows(panel)[related.selected]:
            return
        await pilot.press("down")
    assert task_id in _related_rows(panel)[related.selected]


async def _open_related_task(
    app: DashboardApp,
    pilot: Pilot[None],
    task_id: str,
) -> None:
    panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
    if TASK_VIEW_RELATIONSHIPS not in panel.open_views:
        panel = await _open_relationships(panel, pilot)
    await _select_related_row(panel, pilot, task_id)
    await pilot.press("enter")
    await wait_until(
        lambda: task_id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)),
        timeout=5.0,
    )


async def test_home_detail_discards_related_and_filter_state_when_selection_changes(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    dependency = seed_task(connection, clock, title="Dependency selected in detail")
    first = seed_task(
        connection,
        clock,
        title="Task with dependency",
        depends_on=(dependency.id,),
    )
    second = seed_task(connection, clock, title="Unrelated selected task")
    app = _task_detail_app(reader, project_dir, clock, StubServices())

    async with app.run_test(size=(120, 40)) as pilot:
        panel = app.query_one("#task-detail-panel", TaskDetailPanel)
        home_panel = panel
        identity = panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        search = app.query_one("#task-browser-search", Input)
        task_list = app.query_one("#task-browser-list", ListView)
        search.value = first.title
        await wait_until(
            lambda: f"task-row-{first.id}" in [child.id for child in task_list.children],
            timeout=5.0,
        )
        search.focus()
        await pilot.press("enter", "down")
        await wait_until(lambda: first.id in _static_text(identity), timeout=5.0)
        panel = await _open_relationships(panel, pilot)
        await wait_until(
            lambda: any(dependency.id in row for row in _related_rows(panel)),
            timeout=UI_WAIT_SECONDS,
        )
        panel.query_one(f"#{AGENT_TIMELINE_IDS.filters}", TimelineFilterBar).action_cycle_role()
        panel = await _open_detail_view(panel, pilot, TASK_VIEW_EVIDENCE)
        panel = await _open_detail_view(panel, pilot, TASK_VIEW_WORKSPACE)
        feedback = panel.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static)
        assert _static_text(feedback) == ""

        await pilot.press("escape")
        await wait_until(
            lambda: not isinstance(app.screen, TaskDetailScreen), timeout=UI_WAIT_SECONDS
        )
        search.value = second.title
        await wait_until(
            lambda: f"task-row-{second.id}" in [child.id for child in task_list.children],
            timeout=5.0,
        )
        search.focus()
        await pilot.press("enter", "down")
        await wait_until(
            lambda: (
                second.id in _static_text(home_panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )

        panel = home_panel
        assert not any(dependency.title in row for row in _related_rows(panel))
        assert not panel.query_one(f"#{AGENT_TIMELINE_IDS.filters}", TimelineFilterBar).active
        assert _static_text(feedback) == ""


async def _exercise_paged_relationship_navigation(
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    tasks: _PaginatedRelationshipTasks,
) -> None:
    app = _task_detail_app(reader, project_dir, clock, StubServices())
    async with app.run_test(size=(110, 70)) as pilot:
        await pilot.pause()
        await _open_browser_task(app, pilot, tasks.superseded.title, tasks.superseded.id)
        panel = await _open_relationships(
            app.screen.query_one("#task-detail-panel", TaskDetailPanel), pilot
        )
        await _select_related_row(panel, pilot, tasks.dependencies[0].id)
        await pilot.press("pagedown")
        await wait_until(
            lambda: any(tasks.replacements[-1].id in row for row in _related_rows(panel)),
            timeout=UI_WAIT_SECONDS,
        )
        assert tasks.superseded.id in _static_text(
            panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        )
        assert "page 2" in plain_text(
            panel.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static).content
        )
        await _open_related_task(app, pilot, tasks.replacements[-1].id)
        await pilot.press("escape")
        await wait_until(
            lambda: (
                tasks.superseded.id
                in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
        related = panel.query_one(f"#{TASK_DETAIL_RELATED_LIST}", RowList)
        assert tasks.replacements[-1].id in _related_rows(panel)[related.selected]
        await pilot.press("pageup")
        await wait_until(
            lambda: any(tasks.dependencies[0].id in row for row in _related_rows(panel)),
            timeout=5.0,
        )
        assert not any(tasks.replacements[-1].id in row for row in _related_rows(panel))
        detail_screen = app.screen
        await pilot.press("escape", "escape")
        await wait_until(lambda: app.screen is not detail_screen, timeout=5.0)
        await _open_browser_task(app, pilot, tasks.blocked.title, tasks.blocked.id)
        await _open_related_task(app, pilot, tasks.dependencies[-1].id)


def _static_text(widget: Static) -> str:
    rendered = widget.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


async def test_child_search_matches_only_the_child_and_detail_expands_the_family_group(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    parent, nested, children = _nested_plan_family(connection, clock)
    _start_task(connection, clock, children[0].id)
    services = StubServices()
    views = state_views(reader, project_dir, clock, services)

    page = views.task_browser(
        task_filter=TaskBrowserFilter.ACTIVE,
        search="Parser implementation",
        offset=0,
        limit=TASK_BROWSER_PAGE_SIZE,
        selected_task_id=children[0].id,
    )
    detail = await views.task_detail(children[0].id)

    assert page.matching_count == 1
    assert [row.task_id for row in page.rows] == [children[0].id]
    assert detail is not None
    assert detail.relationships is not None
    assert detail.relationships.family_root.task_id == parent.id
    assert detail.relationships.family_parent.task_id == nested.id
    outcomes = {item.outcome: item.count for item in detail.relationships.family_outcomes}
    assert outcomes[Status.DONE.value] == 2
    assert outcomes[Status.RUNNING.value] == 1
    assert outcomes[Status.QUEUED.value] == 1

    detail_context = widget_context(reader, project_dir, clock)
    detail_panel = TaskDetailPanel(detail_context, nested.id, fixed=True)
    async with WidgetHostApp(detail_panel).run_test(size=(90, 80)) as pilot:
        await wait_until(
            lambda: (
                nested.id
                in _static_text(detail_panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        related_list = detail_panel.query_one(f"#{TASK_DETAIL_RELATED_LIST}", RowList)
        assert TASK_VIEW_RELATIONSHIPS not in detail_panel.open_views
        await _open_relationships(detail_panel, pilot)
        await wait_until(lambda: related_list.region.height > 0, timeout=5.0)
        await wait_until(
            lambda: all(
                any(task.id in row for row in _related_rows(detail_panel))
                for task in (parent, children[0], children[1])
            ),
            timeout=UI_WAIT_SECONDS,
        )


async def test_nested_family_navigation_returns_through_each_detail_to_the_selected_root(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    root, nested, children = _nested_plan_family(connection, clock)
    _start_task(connection, clock, children[0].id)
    app = _task_detail_app(reader, project_dir, clock, StubServices())

    async with app.run_test(size=(110, 70)) as pilot:
        await pilot.pause()
        search = app.query_one("#task-browser-search", Input)
        search.value = root.title
        await pilot.pause()
        app.route_task_palette(root.id, TaskPaletteRoute.INSPECT)
        await wait_until(
            lambda: (
                root.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )

        root_panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
        root_panel = await _open_relationships(root_panel, pilot)
        await _select_related_row(root_panel, pilot, nested.id)
        await pilot.press("enter")
        await wait_until(
            lambda: (
                nested.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )

        nested_panel = app.screen.query_one("#task-detail-panel", TaskDetailPanel)
        nested_panel = await _open_relationships(nested_panel, pilot)
        await _select_related_row(nested_panel, pilot, children[0].id)
        await pilot.press("enter")
        await wait_until(
            lambda: (
                children[0].id
                in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )

        await pilot.press("escape")
        await wait_until(
            lambda: (
                nested.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        await pilot.press("escape", "escape")
        await wait_until(
            lambda: (
                root.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        root_screen = app.screen
        await pilot.press("escape", "escape")
        await wait_until(
            lambda: (
                app.screen is not root_screen
                and root.id in _static_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )

        assert search.value == root.title
        assert root.id in _static_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))


def test_interleaved_priority_matches_keep_dispatch_order(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    store = TaskStore(connection, clock)
    first_parent = seed_task(connection, clock, title="First family", mode=Mode.PLAN)
    second_parent = seed_task(connection, clock, title="Second family", mode=Mode.PLAN)
    first_children = store.apply_scope_change(
        first_parent.id,
        Transition.stay(first_parent, step=Step.INTEGRATE, status=Status.DONE),
        [
            NewTask(title="First low", request="low", priority=1),
            NewTask(title="First high", request="high", priority=9),
        ],
        origin=TaskOrigin.LEADER,
    )
    second_children = store.apply_scope_change(
        second_parent.id,
        Transition.stay(second_parent, step=Step.INTEGRATE, status=Status.DONE),
        [
            NewTask(title="Second middle", request="middle", priority=5),
            NewTask(title="Second low", request="low", priority=2),
        ],
        origin=TaskOrigin.LEADER,
    )

    page = state_views(reader, project_dir, clock, StubServices()).task_browser(
        task_filter=TaskBrowserFilter.WAITING,
        search="",
        offset=0,
        limit=TASK_BROWSER_PAGE_SIZE,
        selected_task_id=None,
    )

    assert [row.task_id for row in page.rows] == [
        first_children[1].id,
        second_children[0].id,
        second_children[1].id,
        first_children[0].id,
    ]
    assert page.matching_count == 4


def test_browser_row_ages_come_from_submission_and_recorded_requeue_times(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    task = seed_task(connection, clock, title="Recorded retry")
    clock.advance(100)
    TaskStore(connection, clock).set_requeued(task.id)
    clock.advance(60)

    page = state_views(reader, project_dir, clock, StubServices()).task_browser(
        task_filter=TaskBrowserFilter.WAITING,
        search=task.title,
        offset=0,
        limit=TASK_BROWSER_PAGE_SIZE,
        selected_task_id=task.id,
    )

    assert page.rows[0].age == "2m"
    assert page.rows[0].requeued_age == "1m"


async def test_blocked_dependency_navigation_returns_to_the_original_browser_selection(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    dependency = seed_task(connection, clock, title="Required parser contract")
    blocked = seed_task(
        connection,
        clock,
        title="Dependent parser work",
        depends_on=(dependency.id,),
    )
    TaskStore(connection, clock).set_wait_reason(blocked.id, WaitReason.DEPENDENCY)
    services = StubServices()
    app = _task_detail_app(reader, project_dir, clock, services)

    async with app.run_test(size=(110, 70)) as pilot:
        await pilot.pause()
        search = app.query_one("#task-browser-search", Input)
        search.value = blocked.title
        await pilot.pause()
        app.route_task_palette(blocked.id, TaskPaletteRoute.INSPECT)
        await wait_until(
            lambda: (
                blocked.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        dependent_detail = app.screen
        dependent_panel = await _open_relationships(
            dependent_detail.query_one("#task-detail-panel", TaskDetailPanel),
            pilot,
        )
        await _select_related_row(dependent_panel, pilot, dependency.id)
        await pilot.press("enter")
        await wait_until(
            lambda: (
                dependency.id
                in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        await pilot.press("escape")
        await wait_until(
            lambda: (
                blocked.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        await pilot.press("escape")
        await pilot.pause()

        assert blocked.id in _static_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
        assert app.query_one("#task-browser-search", Input).value == blocked.title
        detail = await state_views(reader, project_dir, clock, services).task_detail(blocked.id)
        assert detail is not None
        assert detail.blocker is not None
        assert dependency.id in "\n".join(detail.blocker.lines)


async def test_relationship_pages_keep_tail_tasks_navigable_and_blocking_dependencies_visible(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = load_settings(project_dir)
    tasks = _seed_paginated_relationship_tasks(connection, clock, settings)

    _assert_relationship_pages(reader, tasks)
    blocker_view = await state_views(
        reader,
        project_dir,
        clock,
        StubServices(),
        settings=settings,
    ).task_detail(tasks.blocked.id)
    assert blocker_view is not None
    assert blocker_view.blocker is not None
    assert tasks.dependencies[-1].id in "\n".join(blocker_view.blocker.lines)
    assert [item.task_id for item in blocker_view.blocker.related_tasks] == [
        tasks.dependencies[-1].id
    ]
    await _exercise_paged_relationship_navigation(
        reader,
        project_dir,
        clock,
        tasks,
    )


def test_relationship_counts_and_family_outcomes_share_one_snapshot(
    connection: Connection,
    database: Database,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    parent = seed_task(connection, clock, title="Snapshot plan", mode=Mode.PLAN)
    writer = TaskStore(connection, clock)
    read_connection = database.connect_readonly()
    writer_changed_state = False

    def change_before_relationship_count(statement: str) -> None:
        nonlocal writer_changed_state
        if not statement.startswith("SELECT COUNT(*) FROM tasks WHERE parent_id ="):
            return
        writer_changed_state = True
        writer.apply_scope_change(
            parent.id,
            Transition.stay(parent, step=Step.INTEGRATE, status=Status.DONE),
            [NewTask(title="Snapshot child", request="child")],
            origin=TaskOrigin.LEADER,
        )

    read_connection.set_trace_callback(change_before_relationship_count)
    try:
        relationships = fetch_task_relationships(read_connection, parent.id)
    finally:
        read_connection.close()

    assert writer_changed_state
    assert relationships is not None
    assert relationships.family_parent.status is Status.QUEUED
    assert relationships.family_children == ()
    assert relationships.family_child_count == 0
    outcomes = dict(relationships.family_outcomes)
    assert outcomes[Status.QUEUED.value] == 1
    assert outcomes[Status.DONE.value] == 0


async def test_quota_and_resource_evidence_keeps_engine_deadline_reset_and_sample_age_separate(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = load_settings(project_dir)
    services = StubServices()
    quota_task = seed_task(connection, clock, title="Quota waiting task")
    TaskStore(connection, clock).set_wait_reason(quota_task.id, WaitReason.QUOTA)
    quota_agent = settings.roles.leader.ladder[0].agent
    resetless = QuotaWindow(
        name="5h",
        used_pct=100.0,
        resets_at=None,
        observed_at=clock.now(),
    )
    QuotaStore(connection).record(quota_agent, resetless)
    QuotaStore(connection).block(
        quota_agent,
        clock.now() + timedelta(seconds=QUOTA_BLOCK_SECONDS),
        "quota",
    )
    resource_task = seed_task(connection, clock, title="Resource waiting task")
    TaskStore(connection, clock).set_wait_reason(resource_task.id, WaitReason.RESOURCES)
    clock.advance(RESOURCE_SAMPLE_AGE_SECONDS)
    services.resource_observation = ResourceView(
        host_cpu_pct=92.0,
        host_ram_used_pct=88.0,
        free_ram_gb=0.4,
        instance_cpu_pct=None,
        instance_rss_gb=None,
        sampled_at=clock.now() - timedelta(seconds=RESOURCE_SAMPLE_AGE_SECONDS),
    )
    views = state_views(reader, project_dir, clock, services, settings=settings)

    quota_detail = await views.task_detail(quota_task.id)
    resource_detail = await views.task_detail(resource_task.id)

    assert quota_detail is not None
    assert quota_detail.blocker is not None
    quota_evidence = "\n".join(quota_detail.blocker.lines)
    assert "reset not reported" in quota_evidence
    assert "retry/probe deadline" in quota_evidence
    assert (TASK_CREATED_AT + timedelta(seconds=QUOTA_BLOCK_SECONDS)).isoformat() in quota_evidence
    assert "observed 2m ago" in quota_evidence
    assert resource_detail is not None
    assert resource_detail.blocker is not None
    resource_evidence = "\n".join(resource_detail.blocker.lines)
    assert "Recorded scheduler reason · resources" in resource_evidence
    assert "Dashboard resource sample" in resource_evidence
    assert "sample age 2m" in resource_evidence


async def test_running_quota_wait_requires_a_live_block_for_the_current_task_role(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = load_settings(project_dir)
    implementer_rung = settings.roles.implementer.ladder[0]
    other_agent = next(agent for agent in settings.agents if agent != implementer_rung.agent)
    other_rung = Rung(agent=other_agent, model=implementer_rung.model, effort=None)
    roles = settings.roles.model_copy(
        update={
            "leader": settings.roles.leader.model_copy(update={"ladder": [other_rung]}),
            "implementer": settings.roles.implementer.model_copy(
                update={"ladder": [implementer_rung, other_rung]}
            ),
        }
    )
    settings = settings.model_copy(update={"roles": roles})
    store = TaskStore(connection, clock)

    owner = seed_task(connection, clock, title="Leader quota wait", mode=Mode.PLAN)
    store.apply_transition(owner.id, Transition.stay(owner, step=Step.PLAN))
    owner = store.require(owner.id)
    failure, quota_block = record_quota_wait(
        connection,
        clock,
        owner,
        role=Role.LEADER,
        rung=other_rung,
        until=clock.now() + timedelta(minutes=5),
    )

    unrelated = seed_task(
        connection, clock, title="Unrelated implementer work", mode=Mode.IMPLEMENT
    )
    store.apply_transition(unrelated.id, Transition.stay(unrelated, step=Step.IMPLEMENT))
    other_role = seed_task(
        connection, clock, title="Previous role quota block", mode=Mode.IMPLEMENT
    )
    store.apply_transition(other_role.id, Transition.stay(other_role, step=Step.TRIAGE))
    other_role = store.require(other_role.id)
    record_quota_wait(
        connection,
        clock,
        other_role,
        role=Role.LEADER,
        rung=other_rung,
        until=clock.now() + timedelta(minutes=5),
    )
    store.apply_transition(other_role.id, Transition.stay(other_role, step=Step.IMPLEMENT))
    expired = seed_task(connection, clock, title="Expired quota wait", mode=Mode.IMPLEMENT)
    store.apply_transition(expired.id, Transition.stay(expired, step=Step.IMPLEMENT))
    expired = store.require(expired.id)
    record_quota_wait(
        connection,
        clock,
        expired,
        role=Role.IMPLEMENTER,
        rung=implementer_rung,
        until=clock.now() - timedelta(seconds=1),
    )
    views = state_views(reader, project_dir, clock, StubServices(), settings=settings)
    expired_detail = await views.task_detail(expired.id)
    changed_rung = seed_task(
        connection, clock, title="Changed rung quota wait", mode=Mode.IMPLEMENT
    )
    store.apply_transition(changed_rung.id, Transition.stay(changed_rung, step=Step.IMPLEMENT))
    changed_rung = store.require(changed_rung.id)
    record_quota_wait(
        connection,
        clock,
        changed_rung,
        role=Role.IMPLEMENTER,
        rung=implementer_rung,
        until=clock.now() + timedelta(minutes=5),
    )
    changed = store.require(changed_rung.id)
    store.apply_transition(
        changed_rung.id,
        replace(
            Transition.stay(changed, step=Step.IMPLEMENT),
            rung_index={Role.IMPLEMENTER: 1},
        ),
    )
    owner_detail, unrelated_detail, other_role_detail, changed_detail = [
        await views.task_detail(task_id)
        for task_id in (owner.id, unrelated.id, other_role.id, changed_rung.id)
    ]

    assert owner_detail is not None
    assert owner_detail.blocker is not None
    owner_evidence = "\n".join(owner_detail.blocker.lines)
    assert "Current turn quota wait · leader" in owner_evidence
    assert failure.ts != quota_block.ts
    assert f"Task quota-block event · {quota_block.ts.isoformat()}" in owner_evidence
    assert f"Task quota-block event · {failure.ts.isoformat()}" not in owner_evidence
    assert unrelated_detail is not None
    assert unrelated_detail.blocker is None
    assert other_role_detail is not None
    assert other_role_detail.blocker is None
    assert expired_detail is not None
    assert expired_detail.blocker is None
    assert changed_detail is not None
    assert changed_detail.blocker is None


async def test_slot_pause_ownership_and_pr_blockers_use_only_recorded_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = load_settings(project_dir)
    services = StubServices()
    slot_settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"max_parallel": 1})}
    )
    occupying = seed_task(connection, clock, title="Slot occupant", mode=Mode.IMPLEMENT)
    _start_task(connection, clock, occupying.id)
    slot_waiter = seed_task(connection, clock, title="Slot waiter")
    TaskStore(connection, clock).set_wait_reason(slot_waiter.id, WaitReason.SLOT)
    held = seed_task(connection, clock, title="Owned paused task")
    run = RunStore(connection, clock).start(held.id, Role.LEADER, settings.roles.leader.ladder[0])
    RunStore(connection, clock).mark_taken_over(run.id)
    TaskStore(connection, clock).set_holds(
        held.id,
        frozenset({Hold.PAUSED, Hold.INTERVENTION}),
    )
    pull_request = seed_task(connection, clock, title="PR waiting", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).apply_transition(
        pull_request.id,
        Transition.stay(pull_request, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    TaskStore(connection, clock).set_wait_reason(pull_request.id, WaitReason.PR_OPEN)
    EventLog(connection, clock).append(
        EventKind.PR_OPENED,
        task_id=pull_request.id,
        message="PR opened",
        data={"url": "https://example.test/pr/82", "branch": "shikiru/task", "commits": []},
    )
    views = state_views(reader, project_dir, clock, services, settings=slot_settings)

    slot_detail = await views.task_detail(slot_waiter.id)
    held_detail = await views.task_detail(held.id)
    pr_detail = await views.task_detail(pull_request.id)

    assert slot_detail is not None
    assert slot_detail.blocker is not None
    assert f"1/{slot_settings.orchestration.max_parallel} slots" in "\n".join(
        slot_detail.blocker.lines
    )
    assert occupying.id in "\n".join(slot_detail.blocker.lines)
    assert held_detail is not None
    assert held_detail.blocker is not None
    hold_evidence = "\n".join(held_detail.blocker.lines)
    assert "Pause hold" in hold_evidence
    assert "Ownership hold" in hold_evidence
    assert Role.LEADER.value in hold_evidence
    assert pr_detail is not None
    assert pr_detail.blocker is not None
    pr_evidence = "\n".join(pr_detail.blocker.lines)
    assert "https://example.test/pr/82" in pr_evidence
    assert "CI state" in pr_evidence


async def test_supersede_replacements_require_event_evidence_and_keep_cancelled_work_visible(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = load_settings(project_dir)
    original = seed_task(connection, clock, title="Rejected task", mode=Mode.IMPLEMENT)
    store = TaskStore(connection, clock)
    store.apply_transition(
        original.id,
        Transition.stay(original, step=Step.VERIFY, status=Status.RUNNING),
    )
    original = store.require(original.id)
    verdict = Verdict(
        decision="supersede",
        summary="Split the work",
        new_tasks=[
            NewTask(title="Unrecorded sibling", request="sibling"),
            NewTask(title="Recorded replacement", request="replacement"),
        ],
    )
    transition = next_step(original, verdict, settings)
    children = store.apply_scope_change(
        original.id,
        transition,
        verdict.new_tasks,
        origin=TaskOrigin.LEADER,
    )
    EventLog(connection, clock).append(
        EventKind.SCOPE_CHANGE,
        task_id=original.id,
        message="Recorded one replacement task",
        data={"new_task_ids": [children[1].id], "scope_changes": []},
    )
    store.apply_transition(
        children[1].id,
        Transition.stay(children[1], step=None, status=Status.CANCELLED),
    )

    relationships = reader.task_relationships(original.id)

    assert relationships is not None
    assert relationships.family_parent.id == original.id
    assert relationships.dependency_count == 0
    assert [task.id for task in relationships.replacements] == [children[1].id]
    assert relationships.replacements[0].status is Status.CANCELLED
    outcomes = dict(relationships.family_outcomes)
    assert outcomes[StatusReason.SUPERSEDED.value] == 1
    assert outcomes[Status.QUEUED.value] == 1
    assert outcomes[Status.CANCELLED.value] == 1
