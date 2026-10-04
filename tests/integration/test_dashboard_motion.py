from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from shikiru.agents.contracts import (
    QuotaWindow,
    Rung,
    Usage,
    role_dir,
)
from shikiru.config.loader import load_settings
from shikiru.config.settings import TimeoutsConfig
from shikiru.dashboard.activity import RoleViewTail
from shikiru.dashboard.app import DashboardApp
from shikiru.domain.escalation import Escalation, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status
from shikiru.paths import run_dir, turns_dir
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.strips import StripsPanel
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
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
from tests.support.dashboard import StubServices, choose_focus_view, state_views
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from rich.text import Text

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


def _seed_dashboard_state(connection: Connection, clock: FakeClock) -> tuple[Task, Run]:
    tasks = TaskStore(connection, clock)
    task = seed_task(connection, clock, title="Live stream", mode=Mode.IMPLEMENT)
    tasks.set_slot(task.id, 0)
    tasks.apply_transition(task.id, first_step(task))
    task = tasks.get(task.id)
    assert task is not None
    step = task.step
    assert step is not None
    runs = RunStore(connection, clock)
    task_run = runs.start(task.id, Role.IMPLEMENTER, Rung("codex", "gpt-6-luna", "low"))
    runs.record_usage(task_run.id, Usage(100, 20, 0, 0, 0.42, "billed", None))
    EventLog(connection, clock).append(
        EventKind.TURN_STARTED,
        task_id=task.id,
        message="implementer turn started",
        data={"step": step.value, "role": Role.IMPLEMENTER.value, "iteration": task.iteration},
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


def _build_views(
    reader: StateReader, project_dir: Path, settings: Settings, clock: FakeClock, task: Task
) -> StateDashboardViews:
    return state_views(
        reader, project_dir, clock, StubServices(), focus_task=task.id, settings=settings
    )


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
            sessions = app.query_one("#task-detail-sessions", Static)
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
            motion_interval_seconds=(
                1 / settings.dashboard.motion.refresh_hz if animations else None
            ),
            resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
            docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
            open_palette=open_palette,
            widget_names=settings.dashboard.widgets,
            timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        )
        async with app.run_test(size=(90, 40)):
            progress = app.query_one("#task-detail-progress", Static)
            signal = app.query_one("#task-detail-signal", Static)
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
                    _TURN_NAME in plain_text(signal.content)
                    and "1s ago" in plain_text(signal.content)
                    and "1s" in plain_text(progress.content)
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
            assert "Engine stopped" in plain_text(status.content)
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
            panel = app.query_one(EscalationsPanel)
            scroll = panel.query_one("#escalation-panels", VerticalScroll)
            await wait_until(lambda: len(list(scroll.query(Static))) == 1, timeout=1)
            row = scroll.query_one(Static)
            assert first.id in plain_text(row.content)
            assert "Enter inspect" in plain_text(row.content)
            panel.focus()
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
            assert "Enter inspect" in plain_text(scroll.query_one(Static).content)
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
