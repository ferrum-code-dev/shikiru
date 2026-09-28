from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Button, Input, Select, Static, TextArea

from shikiru.dashboard.palette import PaletteApp, PaletteScreen
from shikiru.domain.commands import (
    AnswerResolution,
    Pause,
    Prioritize,
    Resolve,
    Submit,
    TakeOver,
)
from shikiru.domain.escalation import options_for
from shikiru.domain.tasks import EscalationReason, Role, Step
from shikiru.errors import ConfigError
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.queries import OperatorQueries
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from tests.support.clock import FakeClock
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text
    from textual.pilot import Pilot

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


_START = datetime(2026, 1, 1, tzinfo=UTC)
_POLL_SECONDS = 0.25
_APP_SIZE = (120, 32)


def _services(
    connection: Connection,
    reader: StateReader,
    clock: FakeClock,
    project_dir: Path,
    *,
    engine_up: bool = False,
) -> tuple[OperatorCommands, OperatorQueries, CommandQueue]:
    queue = CommandQueue(connection, clock)
    for command in queue.pending():
        queue.mark_done(command.id)
    commands = OperatorCommands(queue, clock, engine_up=lambda: engine_up)
    queries = OperatorQueries(
        reader,
        project="palette-test",
        project_root=project_dir,
        engine_up=lambda: engine_up,
        max_parallel=2,
    )
    return commands, queries, queue


def _screen(app: PaletteApp) -> PaletteScreen:
    return cast("PaletteScreen", app.screen)


async def _open_action(pilot: Pilot[None], screen: PaletteScreen, action: str) -> None:
    screen.query_one("#action", Select).value = action
    await pilot.pause()


async def _wait_for_ack(screen: PaletteScreen, status: str = "queued") -> None:
    await wait_until(
        lambda: (
            status in _static_text(screen, "#palette-result")
            and not screen.query_one("#palette-submit", Button).disabled
            and screen.query_one("#palette-result-scroll").region.y
            >= screen.query_one("#palette-form").region.y
            and screen.query_one("#palette-result-scroll").region.bottom
            <= screen.query_one("#palette-form").region.bottom
        ),
        timeout=2,
    )


async def _selection_must_not_run(pane_id: str) -> None:
    del pane_id
    raise AssertionError


async def _enter_text(pilot: Pilot[None], text: str) -> None:
    await pilot.press(*("enter" if character == "\n" else character for character in text))


def _static_text(screen: PaletteScreen, selector: str) -> str:
    rendered = cast("Text", screen.query_one(selector, Static).render())
    return rendered.plain


async def test_palette_submit_queues_a_typed_command_and_shows_its_ack(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await pilot.click("#action")
        await pilot.press("enter", "p", "enter", "enter", "s", "enter")
        assert cast("str", screen.query_one("#action", Select).value) == "submit"
        request = screen.query_one("#request", TextArea)
        for _ in range(12):
            if request.has_focus:
                break
            await pilot.press("tab")
        assert request.has_focus
        await _enter_text(pilot, "first line\nsecond line")
        assert screen.query_one("#request", TextArea).text == "first line\nsecond line"
        screen.query_one("#title", Input).value = "Palette"
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert isinstance(queued[0].command, Submit)
        assert queued[0].command.request == "first line\nsecond line"
        assert queued[0].command.title == "Palette"
        assert "queued" in _static_text(screen, "#palette-result")


async def test_palette_prioritize_queues_the_selected_priority(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "prioritize")
        screen.query_one("#task-id", Input).value = task.id
        screen.query_one("#priority", Input).value = "17"
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == Prioritize(task_id=task.id, priority=17)
        assert "queued" in _static_text(screen, "#palette-result")


async def test_palette_resolve_uses_the_packet_options_and_answer_form(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    packet_path = tmp_path / "E-1.md"
    packet = "Question: Which behavior should remain?\nanswer ★: keep existing behavior\n"
    packet += "\n".join(f"evidence line {line}" for line in range(60))
    packet_path.write_text(packet)
    reason = EscalationReason.DECISION_REQUIRED
    escalation = EscalationStore(connection, clock).open(
        task.id,
        step=Step.CONSULT,
        reason=reason,
        headline="A product decision is needed",
        packet_path=str(packet_path),
        options=options_for(reason),
    )
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "resolve")
        screen.query_one("#escalation", Select).value = escalation.id
        await pilot.pause()
        screen.query_one("#resolution", Select).value = "answer"
        screen.query_one("#resolution-text", TextArea).text = "Keep the current behavior."
        assert packet in _static_text(screen, "#palette-packet")
        assert "answer" in _static_text(screen, "#palette-packet")
        assert screen.query_one("#palette-packet-scroll").max_scroll_y > 0
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert isinstance(queued[0].command, Resolve)
        assert queued[0].command.escalation_id == escalation.id
        assert queued[0].command.resolution == AnswerResolution(text="Keep the current behavior.")
        assert "queued" in _static_text(screen, "#palette-result")


async def test_palette_takeover_queues_a_task_role_command(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%7"})
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        screen.query_one("#task-id", Input).value = task.id
        screen.query_one("#role", Select).value = Role.LEADER.value
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == TakeOver(task_id=task.id, role=Role.LEADER)
        assert "queued" in _static_text(screen, "#palette-result")


async def test_completed_takeover_selects_the_role_pane_and_its_window(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name, env=tmux_server.env)
    pane_id = await tmux.new_window(
        exact_target(tmux_server.session),
        name="task",
        cwd=tmp_path,
    )
    inactive_pane_id = await tmux.split_window(pane_id, axis="h", size_pct=50)
    other_pane_id = await tmux.new_window(
        exact_target(tmux_server.session),
        name="other",
        cwd=tmp_path,
    )
    other_active_pane_id = await tmux.split_window(other_pane_id, axis="h", size_pct=50)
    await tmux.select_pane(other_active_pane_id)
    TaskStore(connection, clock).set_panes(task.id, {Role.IMPLEMENTER: pane_id})
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = PaletteApp(commands, queries, tmux.select_pane)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        screen.query_one("#task-id", Input).value = task.id
        screen.query_one("#role", Select).value = Role.IMPLEMENTER.value
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "done")

    session_target = exact_target(tmux_server.session)
    windows = await tmux_server.run(
        "list-windows",
        "-t",
        session_target,
        "-F",
        "#{window_name} #{window_active}",
    )
    assert "task 1" in windows.stdout
    assert "other 0" in windows.stdout
    panes = await tmux_server.run(
        "list-panes",
        "-t",
        exact_target(tmux_server.session) + ":task",
        "-F",
        "#{pane_id} #{pane_active}",
    )
    assert f"{pane_id} 1" in panes.stdout.splitlines()
    assert f"{inactive_pane_id} 0" in panes.stdout.splitlines()


async def test_rejected_takeover_never_selects_the_role_pane(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        screen.query_one("#task-id", Input).value = task.id
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_rejected(queued[0].id, {"message": "take over unavailable"})
        clock.advance(_POLL_SECONDS)
        await _wait_for_ack(screen, "rejected")

        assert "rejected" in _static_text(screen, "#palette-result")


@pytest.mark.parametrize(
    ("task_exists", "expected"),
    [(True, "leader pane is missing"), (False, "task is missing")],
)
async def test_done_takeover_reports_missing_task_or_pane_and_keeps_ack(
    connection: Connection, reader: StateReader, tmp_path: Path, task_exists: bool, expected: str
) -> None:
    clock = FakeClock(_START)
    task_id = seed_task(connection, clock).id if task_exists else "T-404"
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        screen.query_one("#task-id", Input).value = task_id
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task_id})
        clock.advance(_POLL_SECONDS)
        await wait_until(
            lambda: (
                "done" in _static_text(screen, "#palette-result")
                and expected in _static_text(screen, "#palette-result")
            ),
            timeout=2,
        )


async def test_done_takeover_keeps_ack_with_pane_selection_error(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    TaskStore(connection, clock).set_panes(task.id, {Role.LEADER: "%not-a-real-pane"})
    commands, queries, queue = _services(connection, reader, clock, tmp_path, engine_up=True)

    async def pane_selection_fails(pane_id: str) -> None:
        message = f"could not select {pane_id}"
        raise ConfigError(message)

    app = PaletteApp(commands, queries, pane_selection_fails)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "take-over")
        screen.query_one("#task-id", Input).value = task.id
        await pilot.click("#palette-submit")
        await wait_until(clock.has_pending_sleep, timeout=2)
        queued = queue.pending()
        assert len(queued) == 1
        queue.mark_done(queued[0].id, {"task_id": task.id})
        clock.advance(_POLL_SECONDS)
        await wait_until(
            lambda: (
                "done" in _static_text(screen, "#palette-result")
                and "could not select %not-a-real-pane" in _static_text(screen, "#palette-result")
            ),
            timeout=2,
        )


async def test_palette_pause_now_queues_immediate_pause(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    task = seed_task(connection, clock)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        screen = _screen(app)
        await _open_action(pilot, screen, "pause-now")
        screen.query_one("#task-id", Input).value = task.id
        await pilot.click("#palette-submit")
        await _wait_for_ack(screen)

        queued = queue.pending()
        assert len(queued) == 1
        assert queued[0].command == Pause(task_id=task.id, now=True)


@pytest.mark.parametrize(
    ("route", "expected"),
    [("resolve E-404", "E-404 is not open"), ("make-it-up", "unknown palette action route")],
)
async def test_palette_preserves_invalid_and_missing_resolve_routes(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    route: str,
    expected: str,
) -> None:
    clock = FakeClock(_START)
    commands, queries, queue = _services(connection, reader, clock, tmp_path)
    app = PaletteApp(commands, queries, _selection_must_not_run)

    async with app.run_test(size=_APP_SIZE) as pilot:
        await pilot.pause()
        await app.pop_screen()
        await app.push_screen(
            PaletteScreen(commands, queries, _selection_must_not_run, initial_action=route)
        )
        await pilot.pause()
        screen = _screen(app)
        assert screen.query_one("#palette-submit", Button).disabled
        assert expected in _static_text(screen, "#palette-result")
        assert not queue.pending()
