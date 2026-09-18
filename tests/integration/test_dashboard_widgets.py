from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from textual.app import App, ComposeResult

from shikiru.clock import SystemClock
from shikiru.dashboard.contract import RepoStatus, WidgetContext
from shikiru.domain.commands import Submit
from shikiru.domain.reports import NewTask
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import Hold, Mode, Status, TaskOrigin
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.strips import StripsPanel, StripsWidget
from shikiru.store.commands import CommandQueue
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.store.database import Database
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)


class _StubServices:
    def __init__(self, *, engine_running: bool = False) -> None:
        self._engine_running = engine_running

    async def repo_status(self) -> Sequence[RepoStatus]:
        return [RepoStatus(path=".", branch="main", ahead=1, behind=0, dirty=2)]

    def engine_up(self) -> bool:
        return self._engine_running


class _WidgetHostApp(App[None]):
    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _submit_task(connection: Connection, clock: FakeClock, *, mode: Mode = Mode.IMPLEMENT) -> str:
    queue = CommandQueue(connection, clock)
    queued = queue.append(Submit(task_id="", title="Add retry", request="Add retry", mode=mode))
    assert isinstance(queued.command, Submit)
    store = TaskStore(connection, clock)
    task = store.insert_from_submit(queued.command)
    store.apply_transition(task.id, first_step(task))
    return task.id


def _context(
    reader: StateReader, project_dir: Path, *, services: _StubServices | None = None
) -> WidgetContext:
    return WidgetContext(
        state=reader,
        project_dir=project_dir,
        focus_task=None,
        services=services or _StubServices(),
    )


async def test_header_shows_project_name_engine_state_and_slots(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    reader = StateReader(database, SystemClock())
    try:
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
    finally:
        reader.close()


async def test_header_refreshes_when_the_writer_commits(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)

    reader = StateReader(database, SystemClock())
    try:
        context = _context(reader, project_dir)
        widget = cast("HeaderPanel", HeaderWidget().create(context))
        async with _WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            assert "slots 0/2" in cast("str", widget.content)

            _submit_task(connection, clock)

            await wait_until(lambda: "slots 1/2" in cast("str", widget.content), timeout=5.0)
    finally:
        reader.close()


async def test_strips_shows_the_active_task_at_its_current_step(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)

    reader = StateReader(database, SystemClock())
    try:
        context = _context(reader, project_dir)
        widget = cast("StripsPanel", StripsWidget().create(context))
        async with _WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            text = cast("str", widget.content)
            assert task_id in text
            assert "Implement" in text
    finally:
        reader.close()


async def test_strips_shows_a_split_parent_as_a_finished_strip_with_its_children(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    parent_id = _submit_task(connection, clock)
    store = TaskStore(connection, clock)
    parent = store.get(parent_id)
    assert parent is not None
    split_transition = dataclasses.replace(
        Transition.stay(parent, step=None, status=Status.DONE), status_reason="split"
    )
    store.apply_transition(parent_id, split_transition)
    child = store.insert_child(
        NewTask(title="Half of it", request="Half of it"),
        depends_on=(),
        parent_id=parent_id,
        origin=TaskOrigin.SPLIT,
    )
    store.apply_transition(child.id, first_step(child))

    reader = StateReader(database, SystemClock())
    try:
        context = _context(reader, project_dir)
        widget = cast("StripsPanel", StripsWidget().create(context))
        async with _WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            text = cast("str", widget.content)
            assert parent_id in text
            assert child.id in text
    finally:
        reader.close()


async def test_strips_shows_a_paused_stamp_for_a_held_task(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)
    TaskStore(connection, clock).set_holds(task_id, frozenset({Hold.PAUSED}))

    reader = StateReader(database, SystemClock())
    try:
        context = _context(reader, project_dir)
        widget = cast("StripsPanel", StripsWidget().create(context))
        async with _WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            assert "PAUSED" in cast("str", widget.content)
    finally:
        reader.close()


async def test_queue_lists_a_queued_task_with_its_priority_and_wait_reason(
    database: Database, connection: Connection, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    queue_store = CommandQueue(connection, clock)
    queued = queue_store.append(
        Submit(task_id="", title="Add retry", request="Add retry", priority=3)
    )
    assert isinstance(queued.command, Submit)
    store = TaskStore(connection, clock)
    task = store.insert_from_submit(queued.command)
    assert task.status is Status.QUEUED
    store.set_wait_reason(task.id, WaitReason.SLOT)

    reader = StateReader(database, SystemClock())
    try:
        context = _context(reader, project_dir)
        widget = cast("QueuePanel", QueueWidget().create(context))
        async with _WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            text = cast("str", widget.content)
            assert task.id in text
            assert "priority 3" in text
            assert "slot" in text
    finally:
        reader.close()
