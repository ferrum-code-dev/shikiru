from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult

from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import RepoStatus, WidgetContext
from shikiru.dashboard.views import StateDashboardViews
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import Hold, Mode, Status
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.strips import StripsPanel, StripsWidget
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection


START = datetime(2026, 1, 1, tzinfo=UTC)


def _plain(content: object) -> str:
    return content.plain if isinstance(content, Text) else str(content)


class _StubServices:
    def __init__(self, *, engine_running: bool = False) -> None:
        self._engine_running = engine_running

    async def repo_status(self) -> RepoStatus:
        return RepoStatus(branch="main", ahead=1, behind=0, dirty=2)

    def engine_up(self) -> bool:
        return self._engine_running


class _WidgetHostApp(App[None]):
    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _submit_task(
    connection: Connection, clock: FakeClock, *, mode: Mode | None = Mode.IMPLEMENT
) -> str:
    task = seed_task(connection, clock, title="Add retry", mode=mode)
    store = TaskStore(connection, clock)
    store.set_slot(task.id, 0)
    store.apply_transition(task.id, first_step(task))
    return task.id


def _context(
    reader: StateReader, project_dir: Path, *, services: _StubServices | None = None
) -> WidgetContext:
    return WidgetContext(
        views=StateDashboardViews(
            reader,
            focus_task=None,
            settings=load_settings(project_dir),
            sources=services or _StubServices(),
        ),
        focus_task=None,
        on_change=reader.watch,
    )


async def test_header_shows_project_name_engine_state_and_slots(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = _context(reader, project_dir, services=_StubServices(engine_running=True))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = cast("str", widget.content)
        assert "demo" in text
        assert "engine up" in text
        assert "slots 1/2" in text
        assert "+1/-0" in text
        assert "2 dirty" in text


async def test_header_does_not_count_an_escalated_slot_as_occupied(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="escalated")
    store = TaskStore(connection, clock)
    store.set_slot(task.id, 0)
    store.apply_transition(task.id, Transition.stay(task, step=task.step, status=Status.ESCALATED))

    context = _context(reader, project_dir)
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()

        assert "slots 0/2" in cast("str", widget.content)


async def test_header_refreshes_when_the_writer_commits(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)

    context = _context(reader, project_dir)
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "slots 0/2" in cast("str", widget.content)

        _submit_task(connection, clock)

        await wait_until(lambda: "slots 1/2" in cast("str", widget.content), timeout=5.0)


async def test_strips_shows_the_active_task_at_its_current_step(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)

    context = _context(reader, project_dir)
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert task_id in text
        assert "Implement" in text


async def test_strips_shows_only_a_triage_panel_before_the_mode_is_decided(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock, mode=None)

    context = _context(reader, project_dir)
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert task_id in text
        assert "Triage" in text
        assert "Implement" not in text
        assert "Merge" not in text


async def test_strips_carries_style_information_into_the_rendered_content(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = _context(reader, project_dir)
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        content = widget.content
        assert isinstance(content, Text)
        assert content.spans


async def test_strips_renders_the_narrow_form_in_a_narrow_pane(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = _context(reader, project_dir)
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with _WidgetHostApp(widget).run_test(size=(30, 24)) as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert "|" not in text
        for line in text.splitlines():
            assert cell_len(line) <= 30


async def test_strips_shows_a_paused_stamp_for_a_held_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)
    TaskStore(connection, clock).set_holds(task_id, frozenset({Hold.PAUSED}))

    context = _context(reader, project_dir)
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "PAUSED" in _plain(widget.content)


async def test_queue_lists_a_queued_task_with_its_priority_and_wait_reason(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Add retry", priority=3)
    assert task.status is Status.QUEUED
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.SLOT)

    context = _context(reader, project_dir)
    widget = cast("QueuePanel", QueueWidget().create(context))
    async with _WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = cast("str", widget.content)
        assert task.id in text
        assert "priority 3" in text
        assert "slot" in text
