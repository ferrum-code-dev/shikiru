from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console
from textual.color import Color
from textual.widgets import Input, ListItem, ListView, Select

from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.detail import (
    TASK_DETAIL_AGENT_LIST,
    TASK_DETAIL_AGENT_ROLE,
    TASK_VIEW_AGENT,
)
from shikiru.domain.escalation import options_for
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Status, Step, Task
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.tokens import AMBER, SECONDARY_TEXT_COLOR
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    choose_inspector_details_entry,
    ignore_palette_action,
    state_views,
)
from tests.support.escalations import (
    operator_services,
    resolution_factory,
    resolution_title,
    wait_for_resolution,
)
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import RenderableType
    from rich.segment import Segment
    from textual.pilot import Pilot

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (120, 40)
_COMPACT = (48, 20)
_MAX_ROWS = 6
_BAR = "#escalations"
_TASK_LIST = "#task-browser-list"
_SEARCH = "#task-browser-search"
_ARROW_STOPS = ("#escalations", "#usage", "#resources", "#focus-footer-actions", _TASK_LIST)
_NO_HINT_ACTIONS = "Ctrl+P actions"
_OTHER_DECISIONS = re.compile(r"\d+ decisions? on other tasks")


@dataclass
class _Home:
    app: DashboardApp
    plain: Task
    first: Task
    second: Task
    decisions: dict[str, str]

    @property
    def bar(self) -> EscalationsPanel:
        return self.app.query_one(_BAR, EscalationsPanel)

    def segments(self) -> list[Segment]:
        content = cast("RenderableType", self.bar.query_one(GatedStatic).content)
        console = Console(width=self.bar.size.width, color_system="truecolor")
        return [
            segment for segment in console.render(content, console.options) if segment.text.strip()
        ]

    def text(self) -> str:
        return " ".join("".join(segment.text for segment in self.segments()).split())

    def amber_bold_texts(self) -> list[str]:
        amber = Color.parse(AMBER)
        return [
            segment.text
            for segment in self.segments()
            if segment.style
            and segment.style.bold
            and segment.style.color is not None
            and Color.from_rich_color(segment.style.color) == amber
        ]

    def bold_texts(self) -> list[str]:
        return [segment.text for segment in self.segments() if segment.style and segment.style.bold]

    def styled_text(self, color: str, *, bold: bool) -> str:
        expected = Color.parse(color)
        return " ".join(
            " ".join(
                segment.text
                for segment in self.segments()
                if segment.style
                and bool(segment.style.bold) is bold
                and segment.style.color is not None
                and Color.from_rich_color(segment.style.color) == expected
            ).split()
        )

    def other_decisions(self) -> str | None:
        match = _OTHER_DECISIONS.search(self.styled_text(SECONDARY_TEXT_COLOR, bold=False))
        return match.group(0) if match else None


def _escalate(
    connection: Connection, clock: FakeClock, task: Task, reason: EscalationReason
) -> str:
    TaskStore(connection, clock).apply_transition(
        task.id, Transition.stay(task, step=task.step, status=Status.ESCALATED)
    )
    return (
        EscalationStore(connection, clock)
        .open(
            task.id,
            step=Step.CONSULT,
            reason=reason,
            headline=f"{task.id} · {Step.CONSULT.value} · {reason.value}: Needs an Operator.",
            packet_path=".shikiru/escalations/missing-packet.md",
            options=options_for(reason),
        )
        .id
    )


@pytest.fixture
def home(connection: Connection, reader: StateReader, tmp_path: Path) -> _Home:
    clock = FakeClock(_START)
    project_dir = write_minimal_project(tmp_path)
    plain = seed_task(connection, clock, title="Plain task", mode=Mode.IMPLEMENT)
    first = seed_task(connection, clock, title="First blocked", mode=Mode.IMPLEMENT)
    second = seed_task(connection, clock, title="Second blocked", mode=Mode.IMPLEMENT)
    decisions = {
        first.id: _escalate(connection, clock, first, EscalationReason.DECISION_REQUIRED),
        second.id: _escalate(connection, clock, second, EscalationReason.INTEGRATION_BLOCKED),
    }
    commands, queries, _ = operator_services(connection, reader, clock, project_dir)
    settings = load_settings(project_dir)
    app = DashboardApp(
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
        resolution_screen=resolution_factory(commands, queries, clock, reader),
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        tmux_detach_hint=None,
        detach_tmux_client=None,
    )
    return _Home(app, plain, first, second, decisions)


async def _settled(home: _Home, pilot: Pilot[None]) -> None:
    await wait_until(home.bar.allow_focus, timeout=UI_WAIT_SECONDS)
    await pilot.pause()


async def _select(home: _Home, pilot: Pilot[None], task: Task) -> None:
    for _ in range(_MAX_ROWS):
        highlighted = home.app.query_one(_TASK_LIST, ListView).highlighted_child
        if highlighted is not None and highlighted.id == f"task-row-{task.id}":
            break
        await pilot.press("down")
    await pilot.pause()


@pytest.mark.parametrize("size", [_WIDE, _COMPACT])
async def test_bar_follows_the_selected_task_without_moving_focus_or_writing_commands(
    home: _Home,
    connection: Connection,
    size: tuple[int, int],
) -> None:
    queue = CommandQueue(connection, FakeClock(_START))
    pending = len(queue.pending())
    async with home.app.run_test(size=size) as pilot:
        await _settled(home, pilot)
        task_list = home.app.query_one(_TASK_LIST)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=UI_WAIT_SECONDS
        )
        assert "Integration blocked" in home.text()
        assert "integration_blocked" not in home.text()
        assert home.bar.has_class("armed")
        assert home.decisions[home.second.id] in home.amber_bold_texts()
        assert home.second.id in home.bold_texts()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=UI_WAIT_SECONDS)
        assert f"{home.plain.id} · No decision needed" in home.text()
        assert _NO_HINT_ACTIONS in home.text()
        assert not home.bar.has_class("armed")
        assert home.plain.id in home.bold_texts()
        await _select(home, pilot, home.first)
        await wait_until(
            lambda: home.decisions[home.first.id] in home.text(), timeout=UI_WAIT_SECONDS
        )
        assert "Decision required" in home.text()
        assert "decision_required" not in home.text()
        assert home.app.focused is task_list
        assert len(queue.pending()) == pending


@pytest.mark.parametrize(
    ("resolved", "shown"),
    [(0, "2 decisions on other tasks"), (1, "1 decision on other tasks"), (2, None)],
)
async def test_other_decisions_are_counted_only_while_any_remain(
    home: _Home,
    connection: Connection,
    resolved: int,
    shown: str | None,
) -> None:
    escalations = EscalationStore(connection, FakeClock(_START))
    for escalation_id in list(home.decisions.values())[:resolved]:
        escalations.resolve(escalation_id, "closed before the bar is shown")
    async with home.app.run_test(size=_WIDE) as pilot:
        await pilot.pause()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=UI_WAIT_SECONDS)
        assert home.other_decisions() == shown


async def test_enter_on_the_list_opens_the_resolution_of_the_followed_escalation_only(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=UI_WAIT_SECONDS
        )
        home_screen = home.app.screen
        await pilot.press("enter")
        screen = await wait_for_resolution(home.app)
        assert home.decisions[home.second.id] in resolution_title(screen)
        await pilot.press("escape")
        await wait_until(lambda: home.app.screen is home_screen, timeout=UI_WAIT_SECONDS)
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=UI_WAIT_SECONDS)
        await pilot.press("enter")
        await pilot.pause()
        assert home.app.screen is home_screen


async def test_clicking_a_task_row_and_clearing_the_selection_move_the_bar(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await pilot.click(f"#task-row-{home.plain.id}")
        await wait_until(lambda: "No decision needed" in home.text(), timeout=UI_WAIT_SECONDS)
        await pilot.press("escape")
        await wait_until(lambda: "No decision needed" not in home.text(), timeout=UI_WAIT_SECONDS)
        assert home.bar.has_class("armed")
        assert home.app.focused is home.app.query_one(_TASK_LIST)


async def test_a_choice_made_with_left_and_right_survives_refreshes(
    connection: Connection,
    home: _Home,
) -> None:
    clock = FakeClock(_START)
    extra = _escalate(connection, clock, home.first, EscalationReason.AGENT_ERROR)
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.first)
        await wait_until(
            lambda: home.decisions[home.first.id] in home.text(), timeout=UI_WAIT_SECONDS
        )
        home.bar.focus()
        await pilot.press("right")
        await wait_until(lambda: extra in home.text(), timeout=UI_WAIT_SECONDS)
        await pilot.pause()
        assert extra in home.text()


def _task_rows(home: _Home) -> list[ListItem]:
    children = home.app.query_one(_TASK_LIST, ListView).children
    return [child for child in children if isinstance(child, ListItem)]


def _selected_index(home: _Home) -> int:
    highlighted = home.app.query_one(_TASK_LIST, ListView).highlighted_child
    return next((i for i, row in enumerate(_task_rows(home)) if row is highlighted), -1)


async def _wait_for_selection(home: _Home, index: int) -> None:
    await wait_until(lambda: _selected_index(home) == index, timeout=UI_WAIT_SECONDS)


async def _tab_to(home: _Home, pilot: Pilot[None], selector: str) -> None:
    target = home.app.query_one(selector)
    for _ in range(len(home.app.screen.focus_chain)):
        if home.app.focused is target:
            return
        await pilot.press("tab")
    pytest.fail(f"Tab never reached {selector}")


@pytest.mark.parametrize("stop", _ARROW_STOPS)
async def test_up_and_down_move_the_task_selection_one_step_from_every_home_stop(
    home: _Home,
    stop: str,
) -> None:
    async with home.app.run_test(size=_COMPACT) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.first)
        await _tab_to(home, pilot, stop)
        count = len(_task_rows(home))
        current = _selected_index(home)
        for key, step in (("down", 1), ("up", -1)):
            expected = (current + step) % count
            await pilot.press(key)
            await _wait_for_selection(home, expected)
            assert home.app.focused is home.app.query_one(stop)
            current = expected


async def test_up_and_down_leave_the_task_selection_alone_in_the_search_input(home: _Home) -> None:
    async with home.app.run_test(size=_COMPACT) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.first)
        selected = _selected_index(home)
        task_list = home.app.query_one(_TASK_LIST, ListView)
        await pilot.press("slash")
        search = home.app.query_one(_SEARCH, Input)
        await wait_until(lambda: home.app.focused is search, timeout=UI_WAIT_SECONDS)
        await pilot.press("down", "up", "escape")
        await wait_until(lambda: home.app.focused is task_list, timeout=UI_WAIT_SECONDS)
        assert _selected_index(home) == selected


async def test_arrows_move_an_expanded_select_in_task_detail_without_moving_the_task(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.plain)
        task_list = home.app.query_one(_TASK_LIST, ListView)
        selected = task_list.highlighted_child
        await choose_inspector_details_entry(pilot, TASK_VIEW_AGENT)
        agent_list = home.app.query_one(f"#{TASK_DETAIL_AGENT_LIST}")
        await wait_until(lambda: agent_list.region.height > 0, timeout=UI_WAIT_SECONDS)
        agent_list.focus()
        await pilot.press("f")
        category = cast("Select[str]", home.app.query_one(f"#{TASK_DETAIL_AGENT_ROLE}", Select))
        await wait_until(lambda: home.app.focused is category, timeout=UI_WAIT_SECONDS)
        await pilot.press("enter")
        await wait_until(lambda: category.expanded, timeout=UI_WAIT_SECONDS)
        initial = category.value
        await pilot.press("down", "enter")
        await wait_until(lambda: category.value != initial, timeout=UI_WAIT_SECONDS)
        assert task_list.highlighted_child is selected


@pytest.mark.parametrize("key", ["left", "right"])
async def test_left_and_right_do_nothing_when_the_followed_task_has_one_decision(
    home: _Home,
    key: str,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=UI_WAIT_SECONDS
        )
        shown = home.text()
        home.bar.focus()
        await pilot.press(key)
        await pilot.pause()
        assert home.app.focused is home.bar
        assert home.text() == shown
