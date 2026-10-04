from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Status
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.strips import StripsPanel, StripsWidget
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, state_views, widget_context
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection


START = datetime(2026, 1, 1, tzinfo=UTC)


def _plain(content: object) -> str:
    return content.plain if isinstance(content, Text) else str(content)


def _submit_task(
    connection: Connection, clock: FakeClock, *, mode: Mode | None = Mode.IMPLEMENT
) -> str:
    task = seed_task(connection, clock, title="Add retry", mode=mode)
    store = TaskStore(connection, clock)
    store.set_slot(task.id, 0)
    store.apply_transition(task.id, first_step(task))
    return task.id


async def test_header_shows_project_name_engine_state_and_slots(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = widget_context(
        reader, project_dir, FakeClock(START), services=StubServices(engine_running=True)
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = cast("str", widget.content)
        assert "demo" in text
        assert "Engine running" in text
        assert "uptime unknown" in text
        assert "Running 1 · Waiting 0 · Attention 0" in text
        assert "slots 1/2" in text
        assert "integration merge" in text
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()

        assert "slots 0/2" in cast("str", widget.content)


async def test_home_summary_separates_paused_and_external_waits_from_slot_occupancy(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks = TaskStore(connection, clock)
    paused_id = _submit_task(connection, clock)
    tasks.set_holds(paused_id, frozenset({Hold.PAUSED}))
    running_id = _submit_task(connection, clock)
    tasks.set_slot(running_id, 1)
    external_wait = seed_task(connection, clock, title="Pull request wait")
    tasks.apply_transition(
        external_wait.id,
        Transition.stay(external_wait, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    tasks.set_wait_reason(external_wait.id, WaitReason.PR_OPEN)

    views = state_views(reader, project_dir, clock, StubServices())
    summary = views.workspace_summary()
    header = await views.header()

    assert (summary.running, summary.waiting) == (1, 2)
    assert (header.occupied_slots, header.maximum_slots) == (2, 2)


async def test_header_shows_an_open_fault_streak_and_the_age_of_the_oldest_escalation(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="stuck")
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="question",
        packet_path="packet.md",
        options=(),
    )
    events = EventLog(connection, clock)
    fault = {"scope": "engine tick", "error": "CommandFailed", "recoverable": True, "repeat": 3}
    events.append(EventKind.ENGINE_FAULT, message="failed", data=fault)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = cast("str", widget.content)
        assert "engine tick: CommandFailed x3" in text
        assert "oldest escalation 0:00:00" in text

        events.append(EventKind.ENGINE_RECOVERED, message="ok", data={"scope": "engine tick"})

        await wait_until(lambda: "engine tick" not in cast("str", widget.content), timeout=5.0)


async def test_header_refreshes_when_the_writer_commits(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test(size=(30, 24)) as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
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

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.query_one("#task-browser-preview", Static).render())
        assert task.id in text
        assert "Add retry" in text
        assert "Dispatch priority: 3" in text
        assert "slot" in text
