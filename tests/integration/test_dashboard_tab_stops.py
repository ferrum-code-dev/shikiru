from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.color import Color
from textual.geometry import Region
from textual.widgets import ListView, Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.detail import TASK_DETAIL_IDENTITY
from shikiru.domain.escalation import options_for
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Status
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.plugins.widgets.resources import ResourcesPanel
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.tokens import SELECTED_SURFACE_COLOR
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    choose_focus_view,
    ignore_palette_action,
    state_views,
)
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.usefixtures("colour_terminal")
_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (120, 40)
_COMPACT = (48, 20)
_TINY = (20, 8)
_SIZES = (_WIDE, _COMPACT, _TINY)
_INVISIBLE_STOPS = frozenset(
    {"focus-root", "focus-readouts", "escalation-panels", "focus-footer", "header"}
)
_ATTENTION_LABEL = "Attention · decisions, stalls and faults"
_READOUTS = (UsagePanel, ResourcesPanel, EscalationsPanel)
_SELECTED_SURFACE = Color.parse(SELECTED_SURFACE_COLOR).rgb


def _seed_tasks(
    connection: Connection,
    clock: FakeClock,
    *,
    decision: bool,
    finished: bool = False,
) -> None:
    seed_task(connection, clock, title="Idle task", mode=Mode.IMPLEMENT)
    if finished:
        done = seed_task(connection, clock, title="Finished task", mode=Mode.IMPLEMENT)
        TaskStore(connection, clock).apply_transition(
            done.id,
            Transition.stay(done, step=None, status=Status.DONE),
        )
    if not decision:
        return
    task = seed_task(connection, clock, title="Blocked task", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).apply_transition(
        task.id,
        Transition.stay(task, step=task.step, status=Status.ESCALATED),
    )
    EscalationStore(connection, clock).open(
        task.id,
        step=task.step,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=f"{task.id} · Keep the current behaviour.",
        packet_path=".shikiru/escalations/missing-packet.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )


def _home(
    connection: Connection,
    reader: StateReader,
    project_dir: Path,
    clock: FakeClock,
    *,
    decision: bool,
    finished: bool = False,
) -> DashboardApp:
    _seed_tasks(connection, clock, decision=decision, finished=finished)
    settings = load_settings(project_dir)
    return DashboardApp(
        focus_task=None,
        views=state_views(reader, project_dir, clock, StubServices(), settings=settings),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        animations=False,
        open_palette=ignore_palette_action,
        widget_names=settings.dashboard.widgets,
        open_palette_context=None,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        tmux_detach_hint=None,
        detach_tmux_client=None,
    )


@pytest.fixture
def decision_home(connection: Connection, reader: StateReader, tmp_path: Path) -> DashboardApp:
    project_dir = write_minimal_project(tmp_path)
    return _home(connection, reader, project_dir, FakeClock(_START), decision=True)


@pytest.fixture
def quiet_home(connection: Connection, reader: StateReader, tmp_path: Path) -> DashboardApp:
    project_dir = write_minimal_project(tmp_path)
    return _home(connection, reader, project_dir, FakeClock(_START), decision=False)


@pytest.fixture
def recent_home(connection: Connection, reader: StateReader, tmp_path: Path) -> DashboardApp:
    project_dir = write_minimal_project(tmp_path)
    return _home(
        connection,
        reader,
        project_dir,
        FakeClock(_START),
        decision=False,
        finished=True,
    )


async def _settled(app: DashboardApp, pilot: Pilot[None], *, decision: bool) -> EscalationsPanel:
    bar = app.query_one("#escalations", EscalationsPanel)
    await wait_until(lambda: bar.allow_focus() is decision, timeout=UI_WAIT_SECONDS)
    await pilot.pause()
    return bar


def _surface_rows(widget: Widget) -> list[set[tuple[int, int, int] | None]]:
    lines = widget.render_lines(Region(0, 0, *widget.outer_size))
    first_row = 0
    return [
        {
            segment.style.bgcolor.get_truecolor()[:3]
            if segment.style and segment.style.bgcolor
            else None
            for segment in line
        }
        for line in lines[first_row:]
    ]


async def _tab_cycle(app: DashboardApp, pilot: Pilot[None], key: str = "tab") -> list[Widget]:
    start = app.focused
    assert start is not None
    stops = [start]
    for _ in range(len(app.screen.focus_chain) + 1):
        await pilot.press(key)
        assert app.focused is not None
        if app.focused is start:
            return stops
        stops.append(app.focused)
    pytest.fail("Tab never returned to the first stop")


@pytest.mark.parametrize("size", _SIZES)
async def test_tab_visits_only_visible_stops_and_highlights_the_readouts(
    decision_home: DashboardApp,
    size: tuple[int, int],
) -> None:
    async with decision_home.run_test(size=size) as pilot:
        await _settled(decision_home, pilot, decision=True)
        task_list = decision_home.query_one("#task-browser-list", ListView)
        assert decision_home.focused is task_list
        bar = decision_home.query_one("#escalations", EscalationsPanel)
        assert bar.has_class("armed")
        assert {_SELECTED_SURFACE} == set().union(*_surface_rows(bar))
        stops = await _tab_cycle(decision_home, pilot)
        for stop in stops:
            assert stop.id not in _INVISIBLE_STOPS
            assert stop.display
            assert stop.outer_size.width > 0
            assert stop.outer_size.height > 0
        reached = {type(stop) for stop in stops if isinstance(stop, _READOUTS)}
        assert reached == set(_READOUTS)
        for stop in stops:
            if isinstance(stop, _READOUTS):
                stop.focus()
                await pilot.pause()
                assert {_SELECTED_SURFACE} == set().union(*_surface_rows(stop))
        resources = decision_home.query_one("#resources", ResourcesPanel)
        rule = decision_home.query_one("#resources-rule")
        resources.focus()
        await pilot.pause()
        assert resources.gutter.top == 0
        assert rule.region.bottom == resources.region.y
        assert _SELECTED_SURFACE not in set().union(*_surface_rows(rule))


@pytest.mark.parametrize("size", [_COMPACT, _TINY])
async def test_focused_readout_scrolls_with_page_keys_and_keeps_the_task_selection(
    decision_home: DashboardApp,
    size: tuple[int, int],
) -> None:
    async with decision_home.run_test(size=size) as pilot:
        await _settled(decision_home, pilot, decision=True)
        scrollers = (
            decision_home.query_one("#focus-readouts"),
            decision_home.query_one("#focus-root"),
        )
        task_list = decision_home.query_one("#task-browser-list", ListView)
        selected = task_list.index
        decision_home.query_one("#usage", UsagePanel).focus()
        await pilot.pause()
        before = sum(scroller.scroll_y for scroller in scrollers)
        await pilot.press("pagedown")
        await wait_until(
            lambda: sum(scroller.scroll_y for scroller in scrollers) > before,
            timeout=UI_WAIT_SECONDS,
        )
        assert task_list.index == selected


@pytest.mark.parametrize("size", _SIZES)
async def test_quiet_bar_is_not_a_tab_stop_but_views_attention_still_reaches_it(
    quiet_home: DashboardApp,
    size: tuple[int, int],
) -> None:
    async with quiet_home.run_test(size=size) as pilot:
        bar = await _settled(quiet_home, pilot, decision=False)
        assert not bar.has_class("armed")
        assert bar not in await _tab_cycle(quiet_home, pilot)
        await choose_focus_view(pilot, _ATTENTION_LABEL)
        await wait_until(lambda: quiet_home.focused is bar, timeout=UI_WAIT_SECONDS)


async def _settled_lists(app: DashboardApp, pilot: Pilot[None]) -> tuple[ListView, ListView]:
    active = app.query_one("#task-browser-list", ListView)
    recent = app.query_one("#task-browser-recent-list", ListView)
    await wait_until(
        lambda: app.focused is active and bool(recent.children),
        timeout=UI_WAIT_SECONDS,
    )
    await pilot.pause()
    return active, recent


def _unhighlighted_lists(stops: list[Widget], lists: tuple[ListView, ListView]) -> list[Widget]:
    return [
        stop
        for stop in stops
        if isinstance(stop, ListView) and stop in lists and stop.highlighted_child is None
    ]


async def test_tab_sweeps_skip_recent_until_down_hands_the_stop_over(
    recent_home: DashboardApp,
) -> None:
    async with recent_home.run_test(size=_WIDE) as pilot:
        active, recent = await _settled_lists(recent_home, pilot)
        for key in ("tab", "shift+tab"):
            stops = await _tab_cycle(recent_home, pilot, key)
            assert recent not in stops
            assert not _unhighlighted_lists(stops, (active, recent))
        await pilot.press("down")
        await wait_until(lambda: recent.highlighted_child is not None, timeout=UI_WAIT_SECONDS)
        await pilot.press("tab")
        assert recent_home.focused is recent
        stops = await _tab_cycle(recent_home, pilot)
        assert recent in stops
        assert active not in stops
        assert not _unhighlighted_lists(stops, (active, recent))


async def test_clicking_a_recent_row_from_an_active_selection_focuses_and_highlights_it(
    recent_home: DashboardApp,
) -> None:
    async with recent_home.run_test(size=_WIDE) as pilot:
        active, recent = await _settled_lists(recent_home, pilot)
        row = recent.children[0]
        await pilot.click(f"#{row.id}")
        await wait_until(lambda: recent.highlighted_child is row, timeout=UI_WAIT_SECONDS)
        assert recent_home.focused is recent
        assert active.highlighted_child is None
        identity = plain_text(recent_home.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
        assert "Finished task" in identity


async def test_escape_clears_the_selection_and_the_list_that_held_focus_leaves_no_tab_stop(
    recent_home: DashboardApp,
) -> None:
    async with recent_home.run_test(size=_WIDE) as pilot:
        active, recent = await _settled_lists(recent_home, pilot)
        await pilot.press("escape")
        await wait_until(lambda: active.highlighted_child is None, timeout=UI_WAIT_SECONDS)
        assert recent_home.focused is active
        await pilot.press("tab")
        stops = await _tab_cycle(recent_home, pilot)
        assert active not in stops
        assert recent not in stops
