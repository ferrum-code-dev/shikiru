from __future__ import annotations

import asyncio
import errno
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import ListView, Select, Static

from shikiru.agents.contracts import (
    QuotaWindow,
    Rung,
    Usage,
    role_dir,
)
from shikiru.config.loader import load_settings
from shikiru.config.settings import TimeoutsConfig
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import FOCUS_COMMAND_HISTORY, FOCUS_USAGE_DETAILS, DashboardApp
from shikiru.dashboard.detail import (
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_OWNERSHIP,
    TASK_DETAIL_SIGNAL,
    TASK_DETAIL_VIEWS_OPEN_BUTTON,
    TaskDetailViewsScreen,
)
from shikiru.domain.escalation import Escalation, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ReportKind,
)
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status
from shikiru.paths import run_dir, turns_dir
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.queue import QueuePanel
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.compact import StageActivity
from shikiru.ui.glyphs import GLYPH_ENGINE, GLYPH_ESCALATED, GLYPH_STALLED
from shikiru.ui.strip import PanelState, StampKind, TaskStrip
from shikiru.ui.tokens import STYLE_REVERSE
from shikiru.view_log import (
    VIEW_SESSION_STARTED_SIGNAL,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TOOL_FINISHED_SIGNAL,
    VIEW_TOOL_STARTED_SIGNAL,
    VIEW_TURN_ENDED_SIGNAL,
    VIEW_TURN_KEY,
    view_log_path,
)
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    choose_focus_view,
    state_views,
    widget_context,
)
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until, yield_control

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from rich.text import Text
    from textual.content import Content
    from textual.pilot import Pilot

    from shikiru.config.settings import Settings
    from shikiru.dashboard.views import StateDashboardViews
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.store.database import Database
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_TURN_NAME = "001-implement-i1"
_STALE_TURN_NAME = "000-implement-i0"
_TASK_ACTIVITY_WAIT_SECONDS = 2
_OLD_ITERATION = 1
_CURRENT_ITERATION = 2
_INITIAL_USAGE_COST = 0.42
_QUIET_STALL_SECONDS = 30
_QUIET_TOOL_SECONDS = 60
_QUIET_TOOL_ELAPSED_SECONDS = 10.5
_STATUS_MOTION_TICKS = 5
_STATUS_MOTION_PHASES = 2
_NATIVE_VISUAL_REVIEW_DIR_ENV = "SHIKIRU_NATIVE_VISUAL_REVIEW_DIR"
_USAGE_REFRESH_COSTS = (0.87, 1.13, 1.41, 1.62)
_USAGE_REFRESH_INPUT_TOKENS = 200
_USAGE_REFRESH_OUTPUT_TOKENS = 40
_INITIAL_TRIAGE_TURN = "01-triage-i0"
_IN_FLIGHT_REPORT_TEXT = (
    '{"title":"Loaded report","mode":"implement","complexity":"normal",'
    '"acceptance_criteria":[],"references":[]}'
)


@dataclass(frozen=True)
class _StageTimingCase:
    entries: tuple[tuple[Step, int], ...]
    current_iteration: int
    expected_entry: int | None


def _row(at: datetime, turn: str, signal: str) -> str:
    return json.dumps({VIEW_TIME_KEY: at.isoformat(), VIEW_TURN_KEY: turn, VIEW_SIGNAL_KEY: signal})


def _motion_settings(project_dir: Path) -> Settings:
    settings = load_settings(project_dir)
    timeouts = TimeoutsConfig(
        stall=2,
        tool=5,
        max_quota_wait=60,
        integration_wait=30,
        pr_wait=60,
        helper=10,
        cache_copy=600,
        git_command=120,
    )
    return settings.model_copy(
        update={
            "orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts}),
            "dashboard": settings.dashboard.model_copy(update={"widgets": ["strips", "usage"]}),
        }
    )


def _seed_running_task(
    connection: Connection,
    clock: FakeClock,
    *,
    title: str,
    slot: int,
) -> tuple[Task, Run]:
    tasks = TaskStore(connection, clock)
    task = seed_task(connection, clock, title=title, mode=Mode.IMPLEMENT)
    tasks.set_slot(task.id, slot)
    tasks.apply_transition(task.id, first_step(task))
    task = tasks.get(task.id)
    assert task is not None
    step = task.step
    assert step is not None
    task_run = RunStore(connection, clock).start(
        task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-luna", "low")
    )
    EventLog(connection, clock).append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="implementer turn started",
        data={"step": step.value, "role": Role.IMPLEMENTER.value, "iteration": task.iteration},
    )
    return task, task_run


def _seed_dashboard_state(connection: Connection, clock: FakeClock) -> tuple[Task, Run]:
    tasks = TaskStore(connection, clock)
    task, task_run = _seed_running_task(connection, clock, title="Live stream", slot=0)
    RunStore(connection, clock).record_usage(
        task_run.id, Usage(100, 20, 0, 0, 0.42, "billed", None)
    )
    QuotaStore(connection).record(
        "codex", QuotaWindow("weekly", 50, _START + timedelta(seconds=10), _START)
    )
    EventLog(connection, clock).append(
        EventKind.VERDICT,
        task_id=task.id,
        message="accepted",
        data={"decision": "accept"},
    )
    merged_task = seed_task(connection, clock, title="Merged task", mode=Mode.IMPLEMENT)
    tasks.apply_transition(merged_task.id, first_step(merged_task))
    merged_task = tasks.get(merged_task.id)
    assert merged_task is not None
    tasks.finish(
        merged_task.id,
        Transition.stay(merged_task, step=merged_task.step, status=Status.DONE),
        EventKind.TASK_DONE,
        message="merged",
        data={"summary": "merged"},
    )
    return task, task_run


def _task_dashboard(
    reader: StateReader,
    project_dir: Path,
    settings: Settings,
    clock: FakeClock,
    task: Task,
) -> tuple[DashboardApp, list[tuple[object | None, Callable[[], Awaitable[None]]]]]:
    registered_callbacks: list[tuple[object | None, Callable[[], Awaitable[None]]]] = []

    async def watch(callback: Callable[[], Awaitable[None]]) -> None:
        registered_callbacks.append((getattr(callback, "__self__", None), callback))
        await reader.watch(callback)

    async def open_palette(action: str) -> None:
        del action

    views = _build_views(reader, project_dir, settings, clock, task)
    app = DashboardApp(
        focus_task=task.id,
        views=views,
        on_change=watch,
        clock=clock,
        motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        animations=settings.dashboard.animations,
        open_palette=open_palette,
        widget_names=settings.dashboard.widgets,
    )
    return app, registered_callbacks


async def _wait_for_fifo_writer(path: Path) -> int:
    descriptor: int | None = None

    async def open_writer() -> bool:
        nonlocal descriptor
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
            return False
        return True

    await wait_until(open_writer, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
    assert descriptor is not None
    return descriptor


async def _assert_closed_history_callback_is_safe(
    app: DashboardApp,
    pilot: Pilot[None],
    registered_callbacks: list[tuple[object | None, Callable[[], Awaitable[None]]]],
) -> None:
    await pilot.press("escape")
    await wait_until(
        lambda: not isinstance(app.screen, TaskDetailViewsScreen),
        timeout=_TASK_ACTIVITY_WAIT_SECONDS,
    )
    await pilot.press("v")
    await wait_until(
        lambda: isinstance(app.screen, TaskDetailViewsScreen),
        timeout=_TASK_ACTIVITY_WAIT_SECONDS,
    )
    choice = cast("Select[str]", app.screen.query_one("#task-detail-view-choice", Select))
    choice.value = FOCUS_COMMAND_HISTORY
    await pilot.click(f"#{TASK_DETAIL_VIEWS_OPEN_BUTTON}")
    await wait_until(
        lambda: bool(app.screen.query("#command-history-page")),
        timeout=_TASK_ACTIVITY_WAIT_SECONDS,
    )
    history_screen = app.screen
    await wait_until(
        lambda: any(owner is history_screen for owner, _ in registered_callbacks),
        timeout=_TASK_ACTIVITY_WAIT_SECONDS,
    )
    callback = next(callback for owner, callback in registered_callbacks if owner is history_screen)
    await pilot.press("escape")
    await wait_until(
        lambda: app.screen is not history_screen,
        timeout=_TASK_ACTIVITY_WAIT_SECONDS,
    )
    await callback()
    assert app.is_running


def _build_views(
    reader: StateReader, project_dir: Path, settings: Settings, clock: FakeClock, task: Task
) -> StateDashboardViews:
    return state_views(
        reader,
        project_dir,
        clock,
        StubServices(engine_running=True),
        focus_task=task.id,
        settings=settings,
    )


def _capture_home_status(app: DashboardApp, *, animations: bool) -> None:
    configured_directory = os.environ.get(_NATIVE_VISUAL_REVIEW_DIR_ENV)
    if configured_directory is None:
        return
    capture_directory = Path(configured_directory)
    capture_directory.mkdir(parents=True, exist_ok=True)
    output_kind = (
        "color"
        if os.environ.get("TERM") == "xterm-256color"
        and "NO_COLOR" not in os.environ
        and "FORCE_COLOR" not in os.environ
        else "mono"
    )
    motion_kind = "motion" if animations else "static"
    output_path = capture_directory / f"task113-home-status-{motion_kind}-{output_kind}.svg"
    output_path.write_text(app.export_screenshot(), encoding="utf-8")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _StageTimingCase(
                ((Step.IMPLEMENT, _OLD_ITERATION),),
                _CURRENT_ITERATION,
                None,
            ),
            id="old-iteration",
        ),
        pytest.param(
            _StageTimingCase(
                ((Step.IMPLEMENT, _OLD_ITERATION), (Step.CONSULT, _OLD_ITERATION)),
                _OLD_ITERATION,
                None,
            ),
            id="same-iteration-reentry-without-current-entry",
        ),
        pytest.param(
            _StageTimingCase(
                (
                    (Step.IMPLEMENT, _OLD_ITERATION),
                    (Step.CONSULT, _OLD_ITERATION),
                    (Step.IMPLEMENT, _OLD_ITERATION),
                ),
                _OLD_ITERATION,
                3,
            ),
            id="same-iteration-current-entry",
        ),
    ],
)
async def test_actual_stage_start_uses_latest_matching_workflow_entry(
    case: _StageTimingCase,
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    tasks = TaskStore(connection, clock)
    if case.current_iteration != task.iteration:
        tasks.apply_transition(
            task.id,
            replace(Transition.stay(task, step=task.step), iteration=case.current_iteration),
        )
    events = EventLog(connection, clock)
    expected_at = None
    for index, (step, iteration) in enumerate(case.entries, start=1):
        clock.advance(1)
        entry_at = clock.now()
        events.append(
            EventKind.STEP_STARTED,
            task_id=task.id,
            message=f"{step.value} stage started",
            data={"step": step.value, "iteration": iteration},
        )
        if case.expected_entry == index:
            expected_at = entry_at
    reader = StateReader(database, clock)
    try:
        detail = await _build_views(reader, project_dir, settings, clock, task).task_detail(
            task.id,
            include_timeline=False,
        )

        assert detail is not None
        assert detail.actual_stage_started_at == expected_at
    finally:
        reader.close()


@pytest.fixture
def task_activity_environment(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> Iterator[tuple[Connection, Path, FakeClock, Settings, Task, StateReader, StateDashboardViews]]:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        yield (
            connection,
            project_dir,
            clock,
            settings,
            task,
            reader,
            _build_views(reader, project_dir, settings, clock, task),
        )
    finally:
        reader.close()


def _write_current_turn_log(project_dir: Path, task: Task, views: StateDashboardViews) -> Path:
    role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    role_root.mkdir(parents=True)
    turn_dir = turns_dir(project_dir, task.id) / _TURN_NAME
    turn_dir.mkdir(parents=True)
    stale_turn = turns_dir(project_dir, task.id) / _STALE_TURN_NAME
    stale_turn.mkdir()
    current_link = role_root / CURRENT_TURN_LINK_NAME
    current_link.symlink_to(stale_turn, target_is_directory=True)
    log_path = view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    log_path.write_text(
        "\n".join(
            (
                _row(_START, _STALE_TURN_NAME, VIEW_SESSION_STARTED_SIGNAL),
                _row(_START, _STALE_TURN_NAME, VIEW_TOOL_STARTED_SIGNAL),
                _row(_START, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL),
                _row(_START, _TURN_NAME, VIEW_TOOL_STARTED_SIGNAL),
                "",
            )
        )
    )
    stale_data = next(strip for strip in views.strips() if strip.task_id == task.id)
    active_panel_index = 1
    assert not stale_data.panels[active_panel_index].spinner_frame
    current_link.unlink()
    current_link.symlink_to(turn_dir, target_is_directory=True)
    live_data = next(strip for strip in views.strips() if strip.task_id == task.id)
    assert live_data.panels[active_panel_index].spinner_frame
    return log_path


def _write_session_start_only_log(project_dir: Path, task: Task) -> Path:
    role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    role_root.mkdir(parents=True)
    turn_dir = turns_dir(project_dir, task.id) / _TURN_NAME
    turn_dir.mkdir(parents=True)
    (role_root / CURRENT_TURN_LINK_NAME).symlink_to(turn_dir, target_is_directory=True)
    log_path = view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    log_path.write_text(f"{_row(_START, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL)}\n")
    return log_path


def _write_empty_current_turn_log(project_dir: Path, task: Task) -> Path:
    role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    role_root.mkdir(parents=True)
    turn_dir = turns_dir(project_dir, task.id) / _TURN_NAME
    turn_dir.mkdir(parents=True)
    (role_root / CURRENT_TURN_LINK_NAME).symlink_to(turn_dir, target_is_directory=True)
    log_path = view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
    log_path.write_text("")
    return log_path


def _open_escalation(
    connection: Connection, clock: FakeClock, project_dir: Path, task_id: str, title: str
) -> Escalation:
    store = EscalationStore(connection, clock)
    escalation_id = store.reserve_id()
    packet_path = project_dir / ".shikiru" / "escalations" / f"{escalation_id}.md"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        f"# {escalation_id} · {title}\n\n"
        "## Question\n\nChoose an option.\n\n"
        "## Options\n\n- `retry` ★: Try again\n"
    )
    return store.open(
        task_id,
        escalation_id=escalation_id,
        step=None,
        reason=EscalationReason.AGENT_ERROR,
        headline=title,
        packet_path=str(packet_path),
        options=options_for(EscalationReason.AGENT_ERROR),
    )


def test_role_view_tail_recovers_a_new_turn_consumed_under_the_old_turn_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "view.jsonl"
    path.write_text(f"{_row(_START, '001-implement-i1', VIEW_SESSION_STARTED_SIGNAL)}\n")
    tail = RoleViewTail()
    assert tail.read(path, "001-implement-i1") is not None

    next_turn_at = _START + timedelta(seconds=1)
    with path.open("a") as handle:
        handle.write(f"{_row(next_turn_at, '002-implement-i1', VIEW_SESSION_STARTED_SIGNAL)}\n")
        handle.write(f"{_row(next_turn_at, '002-implement-i1', VIEW_TOOL_STARTED_SIGNAL)}\n")

    tail.read(path, "001-implement-i1")
    activity = tail.read(path, "002-implement-i1")

    assert activity is not None
    assert activity.last_signal_at == next_turn_at
    latest_row = _row(next_turn_at, "002-implement-i1", VIEW_TOOL_STARTED_SIGNAL)
    assert activity.identity_offset == path.read_bytes().rfind(latest_row.encode())
    assert activity.tool_running
    assert not activity.ended


def test_role_view_tail_keeps_terminal_state_until_a_new_session_signal(tmp_path: Path) -> None:
    path = tmp_path / "view.jsonl"
    path.write_text(
        "\n".join(
            (
                _row(_START, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL),
                _row(_START, _TURN_NAME, VIEW_TOOL_STARTED_SIGNAL),
                _row(_START, _TURN_NAME, VIEW_TURN_ENDED_SIGNAL),
                "",
            )
        )
    )
    tail = RoleViewTail()
    activity = tail.read(path, _TURN_NAME)
    assert activity is not None
    assert activity.ended

    with path.open("a") as handle:
        handle.write(
            f"{_row(_START + timedelta(seconds=1), _TURN_NAME, VIEW_TOOL_FINISHED_SIGNAL)}\n"
        )
    activity = tail.read(path, _TURN_NAME)
    assert activity is not None
    assert activity.ended

    with path.open("a") as handle:
        handle.write(
            f"{_row(_START + timedelta(seconds=2), _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL)}\n"
        )
    activity = tail.read(path, _TURN_NAME)

    assert activity is not None
    assert not activity.ended
    assert not activity.tool_running


async def test_dashboard_motion_ticks_live_views_from_current_turn_log_without_db_changes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)

    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        log_path = _write_current_turn_log(project_dir, task, views)
        initial_strips = views.strips()
        initial_rendered = {item.task_id: TaskStrip(item).render(100) for item in initial_strips}
        verdict_strip = next(item for item in initial_strips if item.stamp is not None)
        merge_strip = next(item for item in initial_strips if item.title == "Merged task")
        assert "VERDICT" in initial_rendered[verdict_strip.task_id].plain
        assert any(
            STYLE_REVERSE in str(span.style)
            for span in initial_rendered[verdict_strip.task_id].spans
        )
        assert "MERGED" in initial_rendered[merge_strip.task_id].plain
        merge_rendered = initial_rendered[merge_strip.task_id]
        assert any(STYLE_REVERSE in str(span.style) for span in merge_rendered.spans)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(180, 40)) as pilot:
            await choose_focus_view(pilot, "Workflow · Strips")
            strips = pilot.app.screen.query_one(StripsPanel)
            await wait_until(clock.has_pending_sleep, timeout=1)

            previous_frame = cast("Text", strips.content).plain
            clock.advance(1)
            await wait_until(lambda: all(item.stamp is None for item in views.strips()), timeout=1)
            await wait_until(
                lambda: cast("Text", strips.content).plain != previous_frame,
                timeout=1,
            )
            clock.advance(2)
            await wait_until(
                lambda: (
                    "3s"
                    in next(item for item in views.strips() if item.task_id == task.id)
                    .panels[1]
                    .detail
                ),
                timeout=1,
            )
            current_strip = next(item for item in views.strips() if item.task_id == task.id)
            assert "$0.42" in current_strip.panels[1].detail
            assert GLYPH_STALLED not in cast("Text", strips.content).plain

            with log_path.open("a") as handle:
                handle.write(f"{_row(clock.now(), _TURN_NAME, VIEW_TOOL_FINISHED_SIGNAL)}\n")
            clock.advance(2.5)
            await wait_until(lambda: GLYPH_STALLED in cast("Text", strips.content).plain, timeout=1)
    finally:
        reader.close()


async def test_quiet_current_tool_keeps_cycling_without_changing_recorded_signal_age(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = load_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "orchestration": base_settings.orchestration.model_copy(
                update={
                    "timeouts": base_settings.orchestration.timeouts.model_copy(
                        update={"stall": _QUIET_STALL_SECONDS, "tool": _QUIET_TOOL_SECONDS}
                    )
                }
            ),
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        _write_current_turn_log(project_dir, task, views)
        initial = next(strip for strip in views.strips() if strip.task_id == task.id)
        assert initial.last_signal_at == _START
        initial_frame = initial.panels[1].spinner_frame

        clock.advance(_QUIET_TOOL_ELAPSED_SECONDS)
        quiet = next(strip for strip in views.strips() if strip.task_id == task.id)

        assert quiet.last_signal_at == _START
        assert quiet.panels[1].spinner_frame != initial_frame
        assert quiet.panels[1].state is PanelState.ACTIVE
    finally:
        reader.close()


async def test_slow_motion_callback_does_not_block_or_overlap_other_callbacks(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        blocked = asyncio.Event()
        cancelled = asyncio.Event()
        blocked_calls = 0
        fast_frames: list[datetime] = []

        async def blocked_refresh() -> None:
            nonlocal blocked_calls
            blocked_calls += 1
            try:
                await blocked.wait()
            finally:
                cancelled.set()

        async def fast_refresh() -> None:
            fast_frames.append(clock.now())

        scheduled_calls = 0

        async def scheduled_refresh() -> None:
            nonlocal scheduled_calls
            scheduled_calls += 1

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=_build_views(reader, project_dir, settings, clock, task),
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        unsubscribe_blocked = app.register_motion_callback(blocked_refresh)
        unsubscribe_fast = app.register_motion_callback(fast_refresh)
        interval = 1 / settings.dashboard.motion.refresh_hz
        async with app.run_test(size=(100, 35)):
            await wait_until(clock.has_pending_sleep, timeout=1)
            for expected_frames in (1, 2, 3):
                clock.advance(interval)
                await wait_until(
                    lambda frame_count=expected_frames: len(fast_frames) >= frame_count,
                    timeout=1,
                )
            assert blocked_calls == 1
            unsubscribe_blocked()
            await wait_until(cancelled.is_set, timeout=1)
            for expected_frames in (4, 5):
                clock.advance(interval)
                await wait_until(
                    lambda frame_count=expected_frames: len(fast_frames) >= frame_count,
                    timeout=1,
                )
            assert len(fast_frames) == 5
            unsubscribe_fast()
            unsubscribe_scheduled = app.register_motion_callback(scheduled_refresh)
            await wait_until(clock.has_pending_sleep, timeout=1)
            clock.advance(interval)
            unsubscribe_scheduled()
            await yield_control()
            assert scheduled_calls == 0
    finally:
        reader.close()


@pytest.mark.parametrize("animations", [True, False], ids=("motion", "static"))
async def test_home_status_blinks_only_for_a_recorded_active_turn(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    animations: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "orchestration": base_settings.orchestration.model_copy(
                update={
                    "timeouts": base_settings.orchestration.timeouts.model_copy(
                        update={"stall": _QUIET_STALL_SECONDS, "tool": _QUIET_TOOL_SECONDS}
                    )
                }
            ),
            "dashboard": base_settings.dashboard.model_copy(
                update={
                    "animations": animations,
                    "widgets": ["queue"],
                }
            ),
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    other_task, _ = _seed_running_task(
        connection,
        clock,
        title="Second current turn",
        slot=1,
    )
    reader = StateReader(database, clock)
    try:
        views = state_views(
            reader,
            project_dir,
            clock,
            StubServices(engine_running=True),
            settings=settings,
        )
        _write_current_turn_log(project_dir, task, views)
        _write_current_turn_log(project_dir, other_task, views)

        async def open_palette(action: str) -> None:
            del action

        interval = 1 / settings.dashboard.motion.refresh_hz
        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=interval,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            animations=animations,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(120, 40)):
            await wait_until(
                lambda: bool(app.query(f"#task-row-{task.id} > Static")),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            row = app.query_one(f"#task-row-{task.id} > Static", Static)
            other_row = app.query_one(f"#task-row-{other_task.id} > Static", Static)

            def status_content(widget: Static) -> Content:
                return cast("Content", widget.render())

            def status_is_bold(widget: Static, expected_bold: bool) -> bool:
                rendered = status_content(widget)
                offset = rendered.plain.index("Implementing")
                return bool(rendered.get_style_at_offset(offset).bold) is expected_bold

            active_text = status_content(row)
            initial_offset = active_text.plain.index("Implementing")
            initial_bold = active_text.get_style_at_offset(initial_offset).bold is True
            other_initial = status_content(other_row)
            other_offset = other_initial.plain.index("Implementing")
            other_initial_bold = other_initial.get_style_at_offset(other_offset).bold is True

            for tick in range(1, _STATUS_MOTION_TICKS + 1):
                clock.advance(interval)
                expected_bold = bool(animations and tick % _STATUS_MOTION_PHASES)
                await wait_until(
                    lambda expected=expected_bold: (
                        status_is_bold(row, expected) and status_is_bold(other_row, expected)
                    ),
                    timeout=_TASK_ACTIVITY_WAIT_SECONDS,
                )

            rendered = status_content(row)
            assert "Implementing" in rendered.plain
            assert initial_bold is False
            assert other_initial_bold is False
            app.query_one("#queue", QueuePanel).select_task(task.id)
            identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            signal = app.query_one(f"#{TASK_DETAIL_SIGNAL}", Static)
            await wait_until(
                lambda: (
                    task.id in plain_text(identity.content)
                    and "loading selected task" not in plain_text(identity.content).casefold()
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(
                lambda: "Last recorded signal ·" in plain_text(signal.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            _capture_home_status(app, animations=animations)
            if animations:
                detail = await views.task_detail(task.id, include_timeline=False)
                assert detail is not None
                assert detail.latest_signal == VIEW_TOOL_STARTED_SIGNAL
                assert detail.latest_signal_at == _START
    finally:
        reader.close()


async def test_static_home_refreshes_stall_and_signal_age(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "orchestration": base_settings.orchestration.model_copy(
                update={
                    "timeouts": base_settings.orchestration.timeouts.model_copy(
                        update={"stall": _QUIET_STALL_SECONDS, "tool": _QUIET_TOOL_SECONDS}
                    )
                }
            ),
            "dashboard": base_settings.dashboard.model_copy(
                update={"animations": False, "widgets": ["queue"]}
            ),
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = state_views(
            reader,
            project_dir,
            clock,
            StubServices(engine_running=True),
            settings=settings,
        )
        _write_current_turn_log(project_dir, task, views)

        async def open_palette(action: str) -> None:
            del action

        interval = 1 / settings.dashboard.motion.refresh_hz
        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=interval,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            animations=False,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(120, 40)):
            await wait_until(
                lambda: bool(app.query(f"#task-row-{task.id} > Static")),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            row = app.query_one(f"#task-row-{task.id} > Static", Static)
            app.query_one("#queue", QueuePanel).select_task(task.id)
            signal = app.query_one(f"#{TASK_DETAIL_SIGNAL}", Static)
            await wait_until(
                lambda: "Last recorded signal · 0s ago" in plain_text(signal.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )

            clock.advance(_QUIET_TOOL_SECONDS + 1)
            await wait_until(
                lambda: "stalled" in cast("Content", row.render()).plain,
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(
                lambda: "Last recorded signal · 1m ago" in plain_text(signal.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            row_content = cast("Content", row.render())
            state_offset = row_content.plain.index("Implementing")
            assert row_content.get_style_at_offset(state_offset).bold is not True
            assert app.is_running
    finally:
        reader.close()


async def test_queue_refresh_survives_child_teardown_and_updates_after_recompose(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(
                update={"animations": False, "widgets": ["queue"]}
            )
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)

        async def open_palette(action: str) -> None:
            del action

        interval = 1 / settings.dashboard.motion.refresh_hz
        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=interval,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            animations=False,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(120, 40)):
            await wait_until(
                lambda: bool(app.query("#task-browser-count")),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            queue = app.query_one("#queue", QueuePanel)
            queued_during_recompose = seed_task(
                connection,
                clock,
                title="Added during queue teardown",
                mode=Mode.IMPLEMENT,
            )
            count_removal = queue.query_one("#task-browser-count", Static).remove()
            list_removal = queue.query_one("#task-browser-list", ListView).remove()
            clock.advance(interval)
            await asyncio.gather(count_removal, list_removal)
            await queue.recompose()

            await wait_until(
                lambda: (
                    bool(queue.query("#task-browser-count"))
                    and bool(queue.query("#task-browser-list"))
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            clock.advance(interval)
            await wait_until(
                lambda: bool(app.query(f"#task-row-{queued_during_recompose.id} > Static")),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )

            added_after_recompose = seed_task(
                connection,
                clock,
                title="Added after queue teardown",
                mode=Mode.IMPLEMENT,
            )
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            clock.advance(interval)
            await wait_until(
                lambda: bool(app.query(f"#task-row-{added_after_recompose.id} > Static")),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            assert app.is_running
    finally:
        reader.close()


async def test_focus_usage_details_refreshes_reported_cost_and_quota_on_motion(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, task_run = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(180, 40)) as pilot:
            await choose_focus_view(pilot, "Usage details")
            usage = pilot.app.screen.query_one("#usage", UsagePanel)
            await wait_until(clock.has_pending_sleep, timeout=1)
            assert "$0.42" in plain_text(usage.content)
            assert "10s" in plain_text(usage.content)

            RunStore(connection, clock).record_usage(
                task_run.id,
                Usage(200, 40, 0, 0, 0.87, "billed", None),
            )
            clock.advance(0.5)
            await wait_until(lambda: "$0.87" in plain_text(usage.content), timeout=1)
    finally:
        reader.close()


async def test_usage_motion_callbacks_survive_repeated_screen_teardown(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    _, task_run = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    motion_callbacks: list[Callable[[], Awaitable[None]]] = []

    def register_motion(callback: Callable[[], Awaitable[None]]) -> Callable[[], None]:
        motion_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in motion_callbacks:
                motion_callbacks.remove(callback)

        return unsubscribe

    try:
        context = replace(
            widget_context(reader, project_dir, clock),
            on_motion=register_motion,
        )
        usage = UsagePanel(context)
        app = WidgetHostApp(usage)
        async with app.run_test(size=(180, 40)):
            await wait_until(
                lambda: f"${_INITIAL_USAGE_COST:.2f}" in plain_text(usage.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            for cost in _USAGE_REFRESH_COSTS[:-1]:
                motion_callback = motion_callbacks[-1]
                RunStore(connection, clock).record_usage(
                    task_run.id,
                    Usage(
                        _USAGE_REFRESH_INPUT_TOKENS,
                        _USAGE_REFRESH_OUTPUT_TOKENS,
                        0,
                        0,
                        cost,
                        "billed",
                        None,
                    ),
                )
                await motion_callback()
                previous_content = plain_text(usage.content)
                assert f"${cost:.2f}" in previous_content

                await usage.remove()
                assert motion_callback not in motion_callbacks
                await motion_callback()
                assert plain_text(usage.content) == previous_content
                assert app.is_running

                usage = UsagePanel(context)
                await app.mount(usage)
                await wait_until(
                    lambda current_usage=usage, expected_cost=cost: (
                        f"${expected_cost:.2f}" in plain_text(current_usage.content)
                    ),
                    timeout=_TASK_ACTIVITY_WAIT_SECONDS,
                )

            active_callback = motion_callbacks[-1]
            RunStore(connection, clock).record_usage(
                task_run.id,
                Usage(
                    _USAGE_REFRESH_INPUT_TOKENS,
                    _USAGE_REFRESH_OUTPUT_TOKENS,
                    0,
                    0,
                    _USAGE_REFRESH_COSTS[-1],
                    "billed",
                    None,
                ),
            )
            await active_callback()
            assert f"${_USAGE_REFRESH_COSTS[-1]:.2f}" in plain_text(usage.content)
    finally:
        reader.close()


async def test_task_detail_refreshes_facts_after_a_covered_screen_returns(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=task.id,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(180, 40)) as pilot:
            ownership = app.query_one(f"#{TASK_DETAIL_OWNERSHIP}", Static)
            await wait_until(
                lambda: (
                    task.title in plain_text(app.query_one("#task-detail-identity", Static).content)
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            assert Hold.PAUSED.value not in plain_text(ownership.content)
            await pilot.press("v")
            await wait_until(
                lambda: isinstance(app.screen, TaskDetailViewsScreen),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
            clock.advance(1)
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            assert Hold.PAUSED.value not in plain_text(ownership.content)

            await pilot.press("escape")
            await wait_until(
                lambda: not isinstance(app.screen, TaskDetailViewsScreen),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(
                lambda: Hold.PAUSED.value in plain_text(ownership.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
    finally:
        reader.close()


async def test_usage_result_finishing_after_context_screen_teardown_is_discarded(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    app, registered_callbacks = _task_dashboard(reader, project_dir, settings, clock, task)
    writer_descriptors: list[int] = []

    try:
        async with app.run_test(size=(180, 40)) as pilot:
            await wait_until(
                lambda: (
                    task.title
                    in plain_text(app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await pilot.press("v")
            await wait_until(
                lambda: isinstance(app.screen, TaskDetailViewsScreen),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            choice = cast("Select[str]", app.screen.query_one("#task-detail-view-choice", Select))
            choice.value = FOCUS_USAGE_DETAILS
            await pilot.click(f"#{TASK_DETAIL_VIEWS_OPEN_BUTTON}")
            await wait_until(
                lambda: bool(app.screen.query(UsagePanel)),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            usage = app.screen.query_one(UsagePanel)
            await wait_until(
                lambda: any(owner is usage for owner, _ in registered_callbacks),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(
                lambda: f"${_INITIAL_USAGE_COST:.2f}" in plain_text(usage.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )

            report_path = turns_dir(project_dir, task.id) / _INITIAL_TRIAGE_TURN / REPORT_FILENAME
            report_path.parent.mkdir(parents=True)
            (report_path.parent / REPORT_KIND_FILENAME).write_text(ReportKind.TRIAGE.value)
            os.mkfifo(report_path)
            TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))
            refresh = next(callback for owner, callback in registered_callbacks if owner is usage)
            pending_refresh = asyncio.ensure_future(refresh())
            writer_descriptors.append(await _wait_for_fifo_writer(report_path))
            previous_content = plain_text(usage.content)
            report_path.unlink()
            report_path.write_text(_IN_FLIGHT_REPORT_TEXT)
            await pilot.press("escape")
            await wait_until(
                lambda: not app.screen.query(UsagePanel),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            writer_descriptor = writer_descriptors[0]
            os.write(writer_descriptor, _IN_FLIGHT_REPORT_TEXT.encode())
            os.close(writer_descriptor)
            writer_descriptors.remove(writer_descriptor)
            await wait_until(
                pending_refresh.done,
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await pending_refresh
            assert plain_text(usage.content) == previous_content
            assert app.is_running
            await _assert_closed_history_callback_is_safe(app, pilot, registered_callbacks)
    finally:
        for writer_descriptor in writer_descriptors:
            os.close(writer_descriptor)
        reader.close()


@pytest.mark.parametrize("motion_enabled", [False, True], ids=("static", "normal"))
async def test_task_usage_details_tick_quota_age_and_reset_without_motion_evidence_reads(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    motion_enabled: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    QuotaStore(connection).record(
        "codex",
        QuotaWindow(
            "weekly",
            50,
            _START + timedelta(seconds=10),
            _START - timedelta(seconds=59),
        ),
    )
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=task.id,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=(
                1 / settings.dashboard.motion.refresh_hz if motion_enabled else None
            ),
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
            timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        )
        async with app.run_test(size=(90, 40)) as pilot:
            await wait_until(
                lambda: (
                    task.title in plain_text(app.query_one("#task-detail-identity", Static).content)
                ),
                timeout=2,
            )
            await pilot.press("u")
            sessions = app.screen.query_one("#task-detail-sessions", Static)
            await wait_until(
                lambda: (
                    "reset 10s" in plain_text(sessions.content)
                    and "observed now" in plain_text(sessions.content)
                ),
                timeout=2,
            )
            await wait_until(clock.has_pending_sleep, timeout=2)
            clock.advance(2)
            await wait_until(
                lambda: (
                    "reset 8s" in plain_text(sessions.content)
                    and "observed 1m ago" in plain_text(sessions.content)
                ),
                timeout=2,
            )
    finally:
        reader.close()


async def test_task_activity_distinguishes_setup_from_a_missing_signal(
    task_activity_environment: tuple[
        Connection, Path, FakeClock, Settings, Task, StateReader, StateDashboardViews
    ],
) -> None:
    connection, project_dir, clock, settings, task, reader, views = task_activity_environment
    no_signal = await views.task_detail(task.id, include_timeline=False)
    assert no_signal is not None
    assert not no_signal.signal_observed
    assert no_signal.latest_signal is None
    assert no_signal.actual_stage == Step.IMPLEMENT.value
    assert no_signal.actual_stage_started_at is None

    setup = seed_task(connection, clock, title="Setup stage", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).apply_transition(
        setup.id,
        Transition.stay(setup, step=None, status=Status.RUNNING),
    )
    setup = TaskStore(connection, clock).get(setup.id)
    assert setup is not None
    setup_views = _build_views(reader, project_dir, settings, clock, setup)
    setup_detail = await setup_views.task_detail(setup.id, include_timeline=False)
    assert setup_detail is not None
    assert setup_detail.actual_stage is None
    assert setup_detail.actual_stage_started_at is None
    setup_activity = plain_text(StageActivity(setup_detail).render(clock.now()))
    assert "Stage · setup" in setup_activity
    assert "not started" not in setup_activity

    completed_task = TaskStore(connection, clock).get(setup.id)
    assert completed_task is not None
    TaskStore(connection, clock).finish(
        completed_task.id,
        Transition.stay(completed_task, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="setup task completed",
        data={"summary": "completed"},
    )
    completed_detail = await setup_views.task_detail(setup.id, include_timeline=False)
    assert completed_detail is not None
    completed_activity = plain_text(StageActivity(completed_detail).render(clock.now()))
    assert "Stage · complete" in completed_activity
    assert "not started" not in completed_activity

    future_detail = replace(
        setup_detail,
        actual_stage=Step.VERIFY.value,
        actual_stage_started_at=clock.now() + timedelta(seconds=1),
    )
    future_activity = plain_text(StageActivity(future_detail).render(clock.now()))
    assert "future timestamp" in future_activity


async def test_task_activity_requires_current_turn_signal_and_stays_still_without_motion(
    task_activity_environment: tuple[
        Connection, Path, FakeClock, Settings, Task, StateReader, StateDashboardViews
    ],
) -> None:
    _, project_dir, clock, settings, task, reader, views = task_activity_environment
    _write_current_turn_log(project_dir, task, views)
    observed = await views.task_detail(task.id, include_timeline=False)
    assert observed is not None
    assert observed.signal_observed
    assert observed.latest_signal_at == _START
    assert observed.latest_signal_role == Role.IMPLEMENTER.value
    assert observed.latest_signal_turn == _TURN_NAME

    static_settings = settings.model_copy(
        update={"dashboard": settings.dashboard.model_copy(update={"animations": False})}
    )
    static_views = _build_views(reader, project_dir, static_settings, clock, task)
    static_observed = await static_views.task_detail(task.id, include_timeline=False)
    assert static_observed is not None
    assert static_observed.signal_observed
    static_spinner = static_observed.progress.panels[1].spinner_frame
    clock.advance(1)
    static_after_tick = await static_views.task_detail(task.id, include_timeline=False)
    assert static_after_tick is not None
    assert static_after_tick.progress.panels[1].spinner_frame == static_spinner


@pytest.mark.parametrize("animations", [True, False], ids=("motion", "static"))
async def test_task_detail_refreshes_recorded_ages_without_database_changes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    animations: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={"dashboard": base_settings.dashboard.model_copy(update={"animations": animations})}
    )
    task, _ = _seed_dashboard_state(connection, clock)
    EventLog(connection, clock).append(
        EventKind.STEP_STARTED,
        task_id=task.id,
        message="implementation stage started",
        data={
            "step": Step.IMPLEMENT.value,
            "role": Role.IMPLEMENTER.value,
            "iteration": task.iteration,
        },
    )
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        log_path = _write_empty_current_turn_log(project_dir, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=task.id,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
            timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        )
        async with app.run_test(size=(90, 40)):
            progress = app.query_one("#task-detail-progress", Static)
            signal = app.query_one("#task-detail-signal", Static)
            identity = app.query_one("#task-detail-identity", Static)
            await wait_until(
                lambda: "Implement" in plain_text(progress.content),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            tick = settings.dashboard.timeline_refresh_seconds
            signal_at = clock.now()
            log_path.write_text(f"{_row(signal_at, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL)}\n")
            await wait_until(
                clock.has_pending_sleep,
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            clock.advance(tick)
            await wait_until(
                lambda: (
                    VIEW_SESSION_STARTED_SIGNAL in plain_text(signal.content)
                    and "1s ago" in plain_text(signal.content)
                    and _TURN_NAME not in plain_text(signal.content)
                    and "1s" in plain_text(progress.content)
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
            await wait_until(clock.has_pending_sleep, timeout=_TASK_ACTIVITY_WAIT_SECONDS)
            clock.advance(settings.orchestration.timeouts.stall + 1)
            await wait_until(
                lambda: (
                    GLYPH_STALLED in plain_text(identity.content)
                    and "stalled" in plain_text(progress.content)
                ),
                timeout=_TASK_ACTIVITY_WAIT_SECONDS,
            )
    finally:
        reader.close()


async def test_task_activity_does_not_claim_a_signal_during_a_hold(
    task_activity_environment: tuple[
        Connection, Path, FakeClock, Settings, Task, StateReader, StateDashboardViews
    ],
) -> None:
    connection, project_dir, clock, _, task, _, views = task_activity_environment
    _write_current_turn_log(project_dir, task, views)
    TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED}))

    held = await views.task_detail(task.id, include_timeline=False)
    assert held is not None
    assert Hold.PAUSED.value in held.holds
    assert not held.signal_observed
    assert held.latest_signal_at == _START
    assert held.actual_stage_started_at is None


async def test_task_activity_does_not_claim_a_signal_after_stop_or_completion(
    task_activity_environment: tuple[
        Connection, Path, FakeClock, Settings, Task, StateReader, StateDashboardViews
    ],
) -> None:
    connection, project_dir, clock, _, task, _, views = task_activity_environment
    _write_current_turn_log(project_dir, task, views)
    tasks = TaskStore(connection, clock)
    current = tasks.get(task.id)
    assert current is not None
    step = current.step
    assert step is not None
    EventLog(connection, clock).append(
        EventKind.TURN_STOPPED,
        task_id=task.id,
        message="turn stopped",
        data={
            "step": step.value,
            "role": Role.IMPLEMENTER.value,
            "iteration": current.iteration,
        },
    )
    stopped = await views.task_detail(task.id, include_timeline=False)
    assert stopped is not None
    assert not stopped.signal_observed
    assert stopped.latest_signal_at == _START

    current = tasks.get(task.id)
    assert current is not None
    tasks.finish(
        task.id,
        Transition.stay(current, step=current.step, status=Status.DONE),
        EventKind.TASK_DONE,
        message="task completed",
        data={"summary": "completed"},
    )
    completed = await views.task_detail(task.id, include_timeline=False)
    assert completed is not None
    assert completed.status == Status.DONE.value
    assert not completed.signal_observed
    assert completed.latest_signal_at == _START


async def test_home_attention_count_updates_from_motion_without_database_changes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(
                update={"widgets": ["header", "strips"]}
            )
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        _write_session_start_only_log(project_dir, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(100, 30)):
            status = app.query_one("#escalations", Static)
            await wait_until(clock.has_pending_sleep, timeout=1)
            assert "No decisions needed" in plain_text(status.content)
            clock.advance(3)
            await wait_until(
                lambda: task.id in plain_text(status.content),
                timeout=1,
            )
            assert views.attention().stalls == 1
    finally:
        reader.close()


async def test_focus_header_motion_waits_for_home_when_modal_opens_and_closes(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(
                update={"widgets": ["header", "strips"]}
            )
        }
    )
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        _write_session_start_only_log(project_dir, task)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            status = app.query_one("#escalations", Static)
            home_screen = pilot.app.screen
            await wait_until(clock.has_pending_sleep, timeout=1)
            assert "No decisions needed" in plain_text(status.content)

            await pilot.press("?")
            await wait_until(lambda: pilot.app.screen is not home_screen, timeout=1)
            clock.advance(3)
            await wait_until(clock.has_pending_sleep, timeout=1)
            assert "No decisions needed" in plain_text(status.content)

            await pilot.press("escape")
            await wait_until(lambda: pilot.app.screen is home_screen, timeout=1)
            clock.advance(1)
            await wait_until(
                lambda: task.id in plain_text(status.content),
                timeout=1,
            )
            assert views.attention().stalls == 1
    finally:
        reader.close()


@pytest.mark.parametrize(
    "case",
    [
        (Status.RUNNING, frozenset[Hold](), PanelState.ACTIVE, GLYPH_ENGINE, None),
        (
            Status.RUNNING,
            frozenset({Hold.PAUSED}),
            PanelState.ACTIVE,
            GLYPH_ENGINE,
            StampKind.PAUSED,
        ),
        (
            Status.RUNNING,
            frozenset({Hold.INTERVENTION}),
            PanelState.ACTIVE,
            GLYPH_ENGINE,
            StampKind.OPERATOR,
        ),
        (
            Status.ESCALATED,
            frozenset[Hold](),
            PanelState.ESCALATED,
            GLYPH_ESCALATED,
            None,
        ),
    ],
)
async def test_dashboard_strips_render_step_less_lifecycle_states(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    case: tuple[Status, frozenset[Hold], PanelState, str, StampKind | None],
) -> None:
    status, holds, expected_state, expected_glyph, expected_stamp = case
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    tasks = TaskStore(connection, clock)
    task = seed_task(connection, clock, title="Pre-step task")
    transition = (
        Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
        if status is Status.ESCALATED
        else Transition.stay(task, step=None, status=status)
    )
    tasks.apply_transition(task.id, transition)
    tasks.set_holds(task.id, holds)
    task = tasks.get(task.id)
    assert task is not None
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        strips = {strip.task_id: strip for strip in views.strips()}
        task_strip = strips[task.id]

        assert task_strip.panels[0].state is expected_state
        assert task_strip.panels[0].stage_glyph == expected_glyph
        assert (task_strip.stamp.kind if task_strip.stamp is not None else None) is expected_stamp
        assert task.id in TaskStrip(task_strip).render(100).plain
    finally:
        reader.close()


@pytest.mark.parametrize("ending", [EventKind.TURN_FAILED, EventKind.TURN_STOPPED])
def test_a_turn_ended_in_the_database_overrides_a_nonterminal_current_turn_log(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    ending: EventKind,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        step = task.step
        assert step is not None
        role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
        role_root.mkdir(parents=True)
        turn_dir = turns_dir(project_dir, task.id) / _TURN_NAME
        turn_dir.mkdir(parents=True)
        (role_root / CURRENT_TURN_LINK_NAME).symlink_to(turn_dir, target_is_directory=True)
        view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value).write_text(
            "\n".join(
                (
                    _row(_START, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL),
                    _row(_START, _TURN_NAME, VIEW_TOOL_STARTED_SIGNAL),
                    "",
                )
            )
        )
        clock.advance(3)
        EventLog(connection, clock).append(
            ending,
            task_id=task.id,
            message="turn ended",
            data={
                "step": step.value,
                "role": Role.IMPLEMENTER.value,
                "iteration": task.iteration,
                "result": "ProtocolError",
            },
        )

        strip = next(item for item in views.strips() if item.task_id == task.id)

        assert strip.panels[1].state is PanelState.ACTIVE
        assert strip.panels[1].spinner_frame == ""
        assert GLYPH_STALLED not in TaskStrip(strip).render(100).plain
    finally:
        reader.close()


def test_retry_turn_ignores_signal_evidence_from_the_previous_same_step_turn(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    settings = _motion_settings(project_dir)
    task, _ = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        step = task.step
        assert step is not None
        role_root = role_dir(run_dir(project_dir, task.id), Role.IMPLEMENTER.value)
        role_root.mkdir(parents=True)
        old_turn = turns_dir(project_dir, task.id) / _TURN_NAME
        old_turn.mkdir(parents=True)
        (role_root / CURRENT_TURN_LINK_NAME).symlink_to(old_turn, target_is_directory=True)
        view_log_path(run_dir(project_dir, task.id), Role.IMPLEMENTER.value).write_text(
            "\n".join(
                (
                    _row(_START, _TURN_NAME, VIEW_SESSION_STARTED_SIGNAL),
                    _row(_START, _TURN_NAME, VIEW_TOOL_STARTED_SIGNAL),
                    "",
                )
            )
        )
        clock.advance(1)
        EventLog(connection, clock).append(
            EventKind.TURN_COMPLETED,
            task_id=task.id,
            message="retry requested",
            data={
                "step": step.value,
                "role": Role.IMPLEMENTER.value,
                "iteration": task.iteration,
            },
        )
        clock.advance(1)
        EventLog(connection, clock).append(
            EventKind.TURN_STARTED,
            task_id=task.id,
            message="retry started",
            data={
                "step": step.value,
                "role": Role.IMPLEMENTER.value,
                "iteration": task.iteration,
            },
        )

        strip = next(item for item in views.strips() if item.task_id == task.id)

        assert strip.panels[1].spinner_frame == ""
        assert strip.panels[1].state is PanelState.ACTIVE
    finally:
        reader.close()


async def test_focus_attention_selects_groups_and_reappears_after_resolution(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(update={"widgets": ["escalations"]})
        }
    )
    task = seed_task(connection, clock, title="Escalated task")
    first = _open_escalation(connection, clock, project_dir, task.id, "First packet")
    second = _open_escalation(connection, clock, project_dir, task.id, "Second packet")
    third = _open_escalation(connection, clock, project_dir, task.id, "Third packet")
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)

        opened_actions: list[str] = []

        async def open_palette(action: str) -> None:
            opened_actions.append(action)

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            panel = app.query_one(EscalationsPanel)
            scroll = panel.query_one("#escalation-panels", VerticalScroll)
            await wait_until(lambda: len(list(scroll.query(Static))) == 1, timeout=1)
            row = scroll.query_one(Static)
            assert first.id in plain_text(row.content)
            panel.focus()
            await pilot.press("enter")
            await wait_until(lambda: opened_actions == [f"resolve {first.id}"], timeout=1)
            await pilot.press("right")
            await wait_until(
                lambda: (
                    "E-2 ·" in plain_text(scroll.query(Static)[0].content)
                    and "Second packet" in plain_text(scroll.query(Static)[0].content)
                ),
                timeout=1,
            )
            assert pilot.app.focused is panel
            assert len(list(scroll.query(Static))) == 1

            store = EscalationStore(connection, clock)
            store.resolve(first.id, "retry")
            store.resolve(second.id, "retry")
            store.resolve(third.id, "retry")
            clock.advance(0.5)
            await wait_until(
                lambda: (
                    panel.display
                    and "No decisions needed" in plain_text(scroll.query(Static)[0].content)
                ),
                timeout=1,
            )
            assert pilot.app.focused is panel
            app.query_one("#focus-footer").focus()
            await wait_until(lambda: panel.display, timeout=1)

            reopened = _open_escalation(connection, clock, project_dir, task.id, "Reopened packet")
            clock.advance(0.5)
            await wait_until(
                lambda: (
                    panel.display
                    and "Reopened packet" in plain_text(scroll.query(Static)[0].content)
                ),
                timeout=1,
            )
            assert views.escalations()[0].escalation_id == reopened.id
    finally:
        reader.close()


async def test_focus_attention_keyboard_selects_late_groups_in_the_compact_row(
    database: Database,
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(update={"widgets": ["escalations"]})
        }
    )
    tasks: list[Task] = []
    escalations = EscalationStore(connection, clock)
    for index in range(10):
        task = seed_task(connection, clock, title=f"Attention group {index}")
        tasks.append(task)
        escalations.open(
            task.id,
            step=task.step,
            reason=EscalationReason.DECISION_REQUIRED,
            headline=f"Missing packet decision {index}",
            packet_path=f".shikiru/escalations/missing-{index}.md",
            options=options_for(EscalationReason.DECISION_REQUIRED),
        )
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, tasks[0])

        opened_actions: list[str] = []

        async def open_palette(action: str) -> None:
            opened_actions.append(action)

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        selected_group = views.attention().groups[8]
        selected_task_id = selected_group.task_id
        assert selected_task_id is not None
        async with app.run_test(size=(100, 32)) as pilot:
            panel = app.query_one(EscalationsPanel)
            scroll = panel.query_one("#escalation-panels", VerticalScroll)
            panel.focus()
            await pilot.press(*("down" for _ in range(8)))
            await wait_until(
                lambda: (
                    selected_task_id in plain_text(scroll.query_one(Static).content)
                    and selected_group.escalations[0].escalation_id
                    in plain_text(scroll.query_one(Static).content)
                ),
                timeout=1,
            )
            panel.focus()
            await pilot.press("enter")
            await wait_until(
                lambda: (
                    opened_actions == [f"resolve {selected_group.escalations[0].escalation_id}"]
                ),
                timeout=1,
            )
    finally:
        reader.close()


async def test_disabled_animations_keep_countdowns_live_without_animating_frames(
    database: Database,
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(_START)
    base_settings = _motion_settings(project_dir)
    settings = base_settings.model_copy(
        update={
            "dashboard": base_settings.dashboard.model_copy(
                update={"animations": False, "widgets": ["strips", "usage"]}
            )
        }
    )
    task, task_run = _seed_dashboard_state(connection, clock)
    reader = StateReader(database, clock)
    try:
        views = _build_views(reader, project_dir, settings, clock, task)
        _write_current_turn_log(project_dir, task, views)

        async def open_palette(action: str) -> None:
            del action

        app = DashboardApp(
            focus_task=None,
            views=views,
            on_change=reader.watch,
            clock=clock,
            motion_interval_seconds=1 / settings.dashboard.motion.refresh_hz,
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
        )
        async with app.run_test(size=(180, 40)) as pilot:
            await choose_focus_view(pilot, "Usage details")
            usage = pilot.app.screen.query_one(UsagePanel)
            await wait_until(clock.has_pending_sleep, timeout=1)
            initial_usage = plain_text(usage.content)
            assert "$0.42" in initial_usage
            assert "10s" in initial_usage
            clock.advance(2)
            await wait_until(clock.has_pending_sleep, timeout=1)
            assert "8s" in plain_text(usage.content)
            assert plain_text(usage.content) != initial_usage

            RunStore(connection, clock).record_usage(
                task_run.id,
                Usage(200, 40, 0, 0, 0.87, "billed", None),
            )
            clock.advance(0.5)
            await wait_until(lambda: "$0.87" in plain_text(usage.content), timeout=1)

            usage_screen = pilot.app.screen
            await pilot.press("escape")
            await wait_until(lambda: pilot.app.screen is not usage_screen, timeout=1)
            await choose_focus_view(pilot, "Workflow · Strips")
            assert pilot.app.screen.query_one(StripsPanel).is_mounted
            initial_frames = tuple(
                panel_view.spinner_frame for item in views.strips() for panel_view in item.panels
            )
            clock.advance(2)
            await wait_until(clock.has_pending_sleep, timeout=1)
            current_frames = tuple(
                panel_view.spinner_frame for item in views.strips() for panel_view in item.panels
            )
            assert current_frames == initial_frames
    finally:
        reader.close()
