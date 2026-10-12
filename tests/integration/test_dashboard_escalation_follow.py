from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console
from textual.color import Color
from textual.containers import VerticalScroll
from textual.widgets import ListView

from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.domain.escalation import ESCALATION_REASON_LABELS, options_for
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Status, Step, Task
from shikiru.plugins.widgets.escalations import EscalationsPanel
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.tokens import AMBER, PRIMARY_TEXT_COLOR, SECONDARY_TEXT_COLOR
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, state_views
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import RenderableType
    from rich.segment import Segment
    from textual.pilot import Pilot

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WAIT_SECONDS = 2
_WIDE = (120, 40)
_COMPACT = (48, 20)
_MAX_ROWS = 6
_BAR = "#escalations"
_SCROLLERS = ("#focus-root", "#focus-readouts", "#escalation-panels")
_TASK_LIST = "#task-browser-list"
_NO_HINT_ACTIONS = "Ctrl+P actions"
_OTHER_DECISIONS = re.compile(r"\d+ decisions? on other tasks")


@dataclass
class _Home:
    app: DashboardApp
    plain: Task
    first: Task
    second: Task
    decisions: dict[str, str]
    palette_actions: list[str]

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
    palette_actions: list[str] = []

    async def record(action: str) -> None:
        palette_actions.append(action)

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
        open_palette=record,
        widget_names=settings.dashboard.widgets,
        open_palette_context=None,
        timeline_refresh_seconds=settings.dashboard.timeline_refresh_seconds,
        tmux_detach_hint=None,
        detach_tmux_client=None,
    )
    return _Home(app, plain, first, second, decisions, palette_actions)


async def _settled(home: _Home, pilot: Pilot[None]) -> None:
    await wait_until(home.bar.allow_focus, timeout=_WAIT_SECONDS)
    await pilot.pause()


async def _select(home: _Home, pilot: Pilot[None], task: Task) -> None:
    for _ in range(_MAX_ROWS):
        highlighted = home.app.query_one(_TASK_LIST, ListView).highlighted_child
        if highlighted is not None and highlighted.id == f"task-row-{task.id}":
            break
        await pilot.press("down")
    await pilot.pause()


def test_every_escalation_reason_has_a_readable_label() -> None:
    assert set(ESCALATION_REASON_LABELS) == set(EscalationReason)
    for reason, label in ESCALATION_REASON_LABELS.items():
        assert label
        assert "_" not in label
        assert reason.value not in label


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
            lambda: home.decisions[home.second.id] in home.text(), timeout=_WAIT_SECONDS
        )
        assert "Integration blocked" in home.text()
        assert "integration_blocked" not in home.text()
        assert home.bar.has_class("armed")
        assert home.decisions[home.second.id] in home.amber_bold_texts()
        assert home.second.id in home.bold_texts()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        assert f"{home.plain.id} · No decision needed" in home.text()
        assert _NO_HINT_ACTIONS in home.text()
        assert not home.bar.has_class("armed")
        assert home.plain.id in home.bold_texts()
        await _select(home, pilot, home.first)
        await wait_until(
            lambda: home.decisions[home.first.id] in home.text(), timeout=_WAIT_SECONDS
        )
        assert "Decision required" in home.text()
        assert "decision_required" not in home.text()
        assert home.app.focused is task_list
        assert len(queue.pending()) == pending


@pytest.mark.parametrize("size", [_WIDE, _COMPACT])
async def test_unescalated_selection_keeps_the_task_bold_and_the_status_white(
    home: _Home,
    size: tuple[int, int],
) -> None:
    async with home.app.run_test(size=size) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        assert home.styled_text(PRIMARY_TEXT_COLOR, bold=True) == home.plain.id
        assert home.styled_text(PRIMARY_TEXT_COLOR, bold=False) == "· No decision needed"


@pytest.mark.parametrize("size", [_WIDE, _COMPACT])
@pytest.mark.parametrize(
    ("resolved", "shown"),
    [(0, "2 decisions on other tasks"), (1, "1 decision on other tasks"), (2, None)],
)
async def test_other_decisions_are_a_grey_count_only_while_any_remain(
    home: _Home,
    connection: Connection,
    size: tuple[int, int],
    resolved: int,
    shown: str | None,
) -> None:
    escalations = EscalationStore(connection, FakeClock(_START))
    for escalation_id in list(home.decisions.values())[:resolved]:
        escalations.resolve(escalation_id, "closed before the bar is shown")
    async with home.app.run_test(size=size) as pilot:
        await pilot.pause()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        assert home.other_decisions() == shown


async def test_enter_on_the_list_opens_the_form_of_the_followed_escalation_only(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=_WAIT_SECONDS
        )
        await pilot.press("enter")
        await wait_until(lambda: bool(home.palette_actions), timeout=_WAIT_SECONDS)
        assert home.palette_actions == [f"resolve {home.decisions[home.second.id]}"]
        home.palette_actions.clear()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        await pilot.press("enter")
        await pilot.pause()
        assert home.palette_actions == []


async def test_clicking_a_task_row_and_clearing_the_selection_move_the_bar(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await pilot.click(f"#task-row-{home.plain.id}")
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        await pilot.press("escape")
        await wait_until(lambda: "No decision needed" not in home.text(), timeout=_WAIT_SECONDS)
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
            lambda: home.decisions[home.first.id] in home.text(), timeout=_WAIT_SECONDS
        )
        home.bar.focus()
        await pilot.press("right")
        await wait_until(lambda: extra in home.text(), timeout=_WAIT_SECONDS)
        await pilot.pause()
        assert extra in home.text()


def _scroll_offsets(app: DashboardApp) -> tuple[float, ...]:
    return tuple(app.query_one(selector, VerticalScroll).scroll_y for selector in _SCROLLERS)


async def test_up_and_down_do_nothing_while_the_bar_has_focus_and_the_list_still_moves(
    home: _Home,
) -> None:
    async with home.app.run_test(size=_COMPACT) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=_WAIT_SECONDS
        )
        task_list = home.app.query_one(_TASK_LIST, ListView)
        highlighted = task_list.highlighted_child
        shown = home.text()
        offsets = _scroll_offsets(home.app)
        home.bar.focus()
        for key in ("up", "down", "up"):
            await pilot.press(key)
            await pilot.pause()
            assert home.app.focused is home.bar
            assert home.text() == shown
            assert task_list.highlighted_child is highlighted
            assert _scroll_offsets(home.app) == offsets
        task_list.focus()
        await _select(home, pilot, home.plain)
        await wait_until(lambda: "No decision needed" in home.text(), timeout=_WAIT_SECONDS)
        assert task_list.highlighted_child is not highlighted


@pytest.mark.parametrize("key", ["left", "right"])
async def test_left_and_right_do_nothing_when_the_followed_task_has_one_decision(
    home: _Home,
    key: str,
) -> None:
    async with home.app.run_test(size=_WIDE) as pilot:
        await _settled(home, pilot)
        await _select(home, pilot, home.second)
        await wait_until(
            lambda: home.decisions[home.second.id] in home.text(), timeout=_WAIT_SECONDS
        )
        shown = home.text()
        home.bar.focus()
        await pilot.press(key)
        await pilot.pause()
        assert home.app.focused is home.bar
        assert home.text() == shown
