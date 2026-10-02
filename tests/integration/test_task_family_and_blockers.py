from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.widgets import Button, Input, ListView, Select, Static

from shikiru.agents.contracts import QuotaWindow
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.contract import ResourceView, TaskBrowserFilter
from shikiru.dashboard.detail import (
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_OPEN_RELATED,
    TASK_DETAIL_RELATED_TASK,
    TASK_DETAIL_RELATIONSHIPS,
    TASK_DETAIL_RELATIONSHIPS_BUTTON,
    TaskDetailPanel,
)
from shikiru.domain.events import EventKind
from shikiru.domain.reports import NewTask, Verdict
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step, next_step
from shikiru.domain.tasks import Hold, Mode, Role, Status, StatusReason, TaskOrigin
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
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
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        open_palette=ignore_palette_action,
        widget_names=["queue"],
    )


def _start_task(connection: Connection, clock: FakeClock, task_id: str) -> None:
    task = TaskStore(connection, clock).require(task_id)
    store = TaskStore(connection, clock)
    store.set_slot(task_id, 0)
    store.apply_transition(task_id, first_step(task))


def _static_text(widget: Static) -> str:
    rendered = widget.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


async def test_child_search_shows_parent_context_and_expands_the_family_group(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    parent = seed_task(connection, clock, title="Parser plan", mode=Mode.PLAN)
    store = TaskStore(connection, clock)
    children = store.apply_scope_change(
        parent.id,
        Transition.stay(parent, step=Step.INTEGRATE, status=Status.DONE),
        [
            NewTask(title="Parser implementation", request="implement parser"),
            NewTask(title="Parser fixture update", request="update fixtures"),
        ],
        origin=TaskOrigin.LEADER,
    )
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
    assert [row.task_id for row in page.ancestor_context] == [parent.id]
    assert detail is not None
    assert detail.relationships is not None
    assert detail.relationships.family_root.task_id == parent.id
    outcomes = {item.outcome: item.count for item in detail.relationships.family_outcomes}
    assert outcomes[Status.DONE.value] == 1
    assert outcomes[Status.RUNNING.value] == 1
    assert outcomes[Status.QUEUED.value] == 1

    browser = widget_context(reader, project_dir, clock, services=services)
    queue = cast("QueuePanel", QueueWidget().create(browser))
    async with WidgetHostApp(queue).run_test(size=(90, 60)) as pilot:
        await pilot.pause()
        search = queue.query_one("#task-browser-search", Input)
        search.value = "Parser implementation"
        await pilot.pause()
        context = queue.query_one("#task-browser-family-context", Static)
        assert parent.id in _static_text(context)
        assert "of 1" in _static_text(queue.query_one("#task-browser-count", Static))

    detail_context = widget_context(reader, project_dir, clock)
    detail_panel = TaskDetailPanel(detail_context, children[0].id, fixed=True)
    async with WidgetHostApp(detail_panel).run_test(size=(90, 80)) as pilot:
        await wait_until(
            lambda: (
                children[0].id
                in _static_text(detail_panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        toggle = detail_panel.query_one(f"#{TASK_DETAIL_RELATIONSHIPS_BUTTON}", Button)
        await pilot.pause()
        assert await pilot.click(toggle)
        relationships = detail_panel.query_one(f"#{TASK_DETAIL_RELATIONSHIPS}", Static)
        await wait_until(lambda: relationships.display, timeout=5.0)
        await pilot.pause()
        assert relationships.display
        assert parent.id in _static_text(relationships)
        assert children[0].id in _static_text(relationships)
        assert children[1].id in _static_text(relationships)
        toggle.press()
        await pilot.pause()
        await wait_until(lambda: not relationships.display, timeout=5.0)
        assert not relationships.display


def test_family_context_keeps_interleaved_priority_matches_in_dispatch_order(
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
    assert {row.task_id for row in page.ancestor_context} == {
        first_parent.id,
        second_parent.id,
    }


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
        task_list = app.query_one("#task-browser-list", ListView)
        task_list.focus()
        task_list.index = 0
        await pilot.press("enter")
        await wait_until(
            lambda: (
                blocked.id in _static_text(app.screen.query_one(f"#{TASK_DETAIL_IDENTITY}", Static))
            ),
            timeout=5.0,
        )
        dependent_detail = app.screen
        await pilot.click(f"#{TASK_DETAIL_RELATIONSHIPS_BUTTON}")
        related = cast(
            "Select[str]",
            dependent_detail.query_one(f"#{TASK_DETAIL_RELATED_TASK}", Select),
        )
        related.value = dependency.id
        await pilot.click(f"#{TASK_DETAIL_OPEN_RELATED}")
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

        assert blocked.id in _static_text(app.query_one("#task-browser-preview", Static))
        assert app.query_one("#task-browser-search", Input).value == blocked.title
        detail = await state_views(reader, project_dir, clock, services).task_detail(blocked.id)
        assert detail is not None
        assert detail.blocker is not None
        assert dependency.id in "\n".join(detail.blocker.lines)


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
    assert "Engine quota retry/probe deadline" in quota_evidence
    assert (TASK_CREATED_AT + timedelta(seconds=QUOTA_BLOCK_SECONDS)).isoformat() in quota_evidence
    assert "observed 2m ago" in quota_evidence
    assert resource_detail is not None
    assert resource_detail.blocker is not None
    resource_evidence = "\n".join(resource_detail.blocker.lines)
    assert "Recorded scheduler reason · resources" in resource_evidence
    assert "Dashboard resource sample" in resource_evidence
    assert "sample age 2m" in resource_evidence


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
