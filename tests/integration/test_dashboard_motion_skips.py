from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from textual.widgets import Static

from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.domain.tasks import EscalationReason
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.strips import StripsPanel, StripsWidget
from shikiru.plugins.widgets.usage import UsagePanel, UsageWidget
from shikiru.store.escalations import EscalationStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import WidgetHostApp, widget_context
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.dashboard.contract import WidgetContext
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
LATER = timedelta(hours=3)
NARROW_WIDTH = 30
WAIT_SECONDS = 2


class MotionTicks:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[], Awaitable[None]]] = []

    def register(self, callback: Callable[[], Awaitable[None]]) -> Callable[[], None]:
        self.callbacks.append(callback)
        return lambda: None

    async def tick(self) -> None:
        for callback in tuple(self.callbacks):
            await callback()


def _context(
    reader: StateReader, project_dir: Path, clock: FakeClock, ticks: MotionTicks
) -> WidgetContext:
    return replace(widget_context(reader, project_dir, clock), on_motion=ticks.register)


def _count_content(queue: QueuePanel) -> object:
    return queue.query_one("#task-browser-count", Static).content


def _preview_text(queue: QueuePanel) -> str:
    return plain_text(queue.query_one("#task-browser-preview", Static).render())


async def test_header_motion_tick_with_unchanged_state_keeps_the_rendered_content(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    ticks = MotionTicks()
    context = _context(reader, write_minimal_project(tmp_path), FakeClock(START), ticks)
    header = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(header).run_test() as pilot:
        await pilot.pause()
        rendered = header.content

        await ticks.tick()

        assert header.content is rendered


async def test_header_motion_tick_shows_a_data_change(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    header = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(header).run_test() as pilot:
        await pilot.pause()
        await ticks.tick()
        seed_task(connection, clock, title="Add retry")

        await wait_until(lambda: "Waiting 1" in plain_text(header.content), timeout=WAIT_SECONDS)


async def test_header_motion_tick_shows_a_time_only_change(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    task = seed_task(connection, clock, title="stuck")
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="question",
        packet_path="packet.md",
        options=(),
    )
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    header = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(header).run_test() as pilot:
        await pilot.pause()
        assert "oldest escalation 0:00:00" in plain_text(header.content)

        clock.advance(LATER.total_seconds())
        await ticks.tick()

        assert "oldest escalation 3:00:00" in plain_text(header.content)


async def test_header_resize_renders_even_when_the_view_is_unchanged(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    ticks = MotionTicks()
    context = _context(reader, write_minimal_project(tmp_path), FakeClock(START), ticks)
    header = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(header).run_test() as pilot:
        await pilot.pause()
        rendered = header.content

        header.styles.width = NARROW_WIDTH
        await wait_until(lambda: header.content is not rendered, timeout=WAIT_SECONDS)


async def test_queue_motion_tick_with_unchanged_state_keeps_the_rendered_widgets(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    seed_task(connection, clock, title="Add retry")
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    queue = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(queue).run_test() as pilot:
        await pilot.pause()
        rendered = _count_content(queue)

        await ticks.tick()

        assert _count_content(queue) is rendered


async def test_queue_motion_tick_shows_a_data_change(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    seed_task(connection, clock, title="Add retry")
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    queue = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(queue).run_test() as pilot:
        await pilot.pause()
        await ticks.tick()
        seed_task(connection, clock, title="Fix parser")

        await wait_until(
            lambda: any(
                "Fix parser" in plain_text(item.query_one(Static).render())
                for item in queue.query("ListItem")
            ),
            timeout=WAIT_SECONDS,
        )


async def test_queue_motion_tick_shows_a_time_only_change(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    seed_task(connection, clock, title="Add retry")
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    queue = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(queue).run_test() as pilot:
        await pilot.pause()
        await ticks.tick()
        before = _preview_text(queue)

        clock.advance(LATER.total_seconds())
        await ticks.tick()

        assert _preview_text(queue) != before


async def test_queue_filter_change_renders_after_skipped_ticks(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    seed_task(connection, clock, title="Add retry")
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    queue = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(queue).run_test() as pilot:
        await pilot.pause()
        await ticks.tick()
        assert "History" not in plain_text(_count_content(queue))

        await queue.activate_filter(TaskBrowserFilter.HISTORY)

        assert "History" in plain_text(_count_content(queue))


async def test_strips_motion_tick_with_unchanged_state_keeps_the_rendered_content(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(START)
    ticks = MotionTicks()
    seed_task(connection, clock, title="Add retry")
    context = _context(reader, write_minimal_project(tmp_path), clock, ticks)
    strips = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(strips).run_test() as pilot:
        await pilot.pause()
        rendered = strips.content

        await ticks.tick()

        assert strips.content is rendered


async def test_usage_motion_tick_with_unchanged_state_keeps_the_rendered_content(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    ticks = MotionTicks()
    context = _context(reader, write_minimal_project(tmp_path), FakeClock(START), ticks)
    usage = cast("UsagePanel", UsageWidget().create(context))
    async with WidgetHostApp(usage).run_test() as pilot:
        await pilot.pause()
        rendered = usage.content

        await ticks.tick()

        assert usage.content is rendered
