from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.geometry import Region
from textual.widgets import TextArea

from shikiru.dashboard.resolution import ResolutionScreen
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Resolve,
    RetryResolution,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.operator.commands import COMMAND_POLL_SECONDS
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from tests.support.clock import FakeClock
from tests.support.escalations import (
    ResolutionHost,
    assert_engine_applies_resolution,
    enqueue_and_cancel,
    operator_services,
    resolution_factory,
    seed_escalation,
)
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.store.database import Database
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_SIZE = (100, 40)
_HOME_SIZE = (160, 50)
_OWN_ANSWER_ROW = 2
_ACCEPT_ROW = 3
_CANCEL_ROW_AFTER_RETRY = 1
_ALTERNATIVE = "Change behavior"
_OWN_TEXT = "Keep the current behavior."
_ARMED_TEXT = "Press Enter again to cancel"


def _text_of(widget: Widget) -> str:
    region = Region(0, 0, widget.size.width, widget.size.height)
    return "\n".join(line.text for line in widget.render_lines(region))


def _rows(screen: ResolutionScreen) -> list[Widget]:
    return list(screen.query(".resolution-choice"))


async def _opened(app: ResolutionHost) -> ResolutionScreen:
    await wait_until(
        lambda: (
            isinstance(app.screen, ResolutionScreen)
            and app.focused is not None
            and app.focused.id == "resolution-choices"
            and all(row.size.height > 0 for row in _rows(app.screen))
        ),
        timeout=UI_WAIT_SECONDS,
    )
    assert isinstance(app.screen, ResolutionScreen)
    return app.screen


async def _type(pilot: Pilot[None], text: str) -> None:
    await pilot.press(*text)


@pytest.mark.parametrize(
    ("reason", "act", "expected"),
    [
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "alternative",
            AnswerResolution(text=_ALTERNATIVE),
            id="leader-alternative",
        ),
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "own-answer",
            AnswerResolution(text=_OWN_TEXT),
            id="own-answer",
        ),
        pytest.param(EscalationReason.AGENT_ERROR, "preselected", RetryResolution(), id="retry"),
        pytest.param(
            EscalationReason.ITERATIONS_EXHAUSTED,
            "accept",
            AcceptResolution(),
            id="accept",
        ),
    ],
)
async def test_each_send_route_queues_one_resolution_the_engine_applies(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    reason: EscalationReason,
    act: str,
    expected: object,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Send route")
    escalation, _ = seed_escalation(connection, clock, task.id, tmp_path, reason)
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        assert queue.pending() == []
        if act == "alternative":
            await pilot.press("down", "enter")
        elif act == "own-answer":
            await pilot.click(_rows(screen)[_OWN_ANSWER_ROW])
            answer = screen.query_one("#resolution-answer", TextArea)
            await wait_until(lambda: app.focused is answer, timeout=UI_WAIT_SECONDS)
            await _type(pilot, _OWN_TEXT)
            assert queue.pending() == []
            await pilot.press("enter")
        elif act == "accept":
            await pilot.click(_rows(screen)[_ACCEPT_ROW])
        else:
            await pilot.press("enter")
        await wait_until(lambda: app.results == [True], timeout=UI_WAIT_SECONDS)

    (queued,) = queue.pending()
    assert isinstance(queued.command, Resolve)
    assert queued.command.escalation_id == escalation.id
    assert queued.command.resolution == expected
    await assert_engine_applies_resolution(
        connection,
        clock,
        tmp_path,
        queue,
        queued=queued,
        task_id=task.id,
        escalation_id=escalation.id,
        expected_type=type(expected),
    )


async def test_cancel_needs_a_second_enter_and_any_other_key_resets_it(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Cancel twice")
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.AGENT_ERROR
    )
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        cancel = _rows(screen)[_CANCEL_ROW_AFTER_RETRY]
        await pilot.press("down", "enter")
        await wait_until(lambda: _ARMED_TEXT in _text_of(cancel), timeout=UI_WAIT_SECONDS)
        assert queue.pending() == []
        await pilot.press("x")
        await wait_until(lambda: _ARMED_TEXT not in _text_of(cancel), timeout=UI_WAIT_SECONDS)
        await pilot.press("enter")
        await wait_until(lambda: _ARMED_TEXT in _text_of(cancel), timeout=UI_WAIT_SECONDS)
        assert queue.pending() == []
        await pilot.press("enter")
        await wait_until(lambda: app.results == [True], timeout=UI_WAIT_SECONDS)

    (queued,) = queue.pending()
    assert isinstance(queued.command, Resolve)
    assert queued.command.resolution == CancelResolution()


async def test_decision_resolved_elsewhere_disables_the_choices_and_keeps_the_draft(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Resolved elsewhere")
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.DECISION_REQUIRED
    )
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    factory = resolution_factory(commands, queries, clock, reader)
    app = ResolutionHost(factory, escalation.id)

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        await pilot.click(_rows(screen)[_OWN_ANSWER_ROW])
        answer = screen.query_one("#resolution-answer", TextArea)
        await wait_until(lambda: app.focused is answer, timeout=UI_WAIT_SECONDS)
        await _type(pilot, _OWN_TEXT)
        await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
        engine = database.connect()
        try:
            await enqueue_and_cancel(engine, clock, task.id, tmp_path)
        finally:
            engine.close()
        recorded_commands = queries.command_history().total_count
        clock.advance(DASHBOARD_CHANGE_POLL_SECONDS)
        await wait_until(
            lambda: screen.query_one("#resolution-notice").display and answer.disabled,
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.press("escape")
        await wait_until(lambda: app.results == [False], timeout=UI_WAIT_SECONDS)

    reopened = ResolutionHost(factory, escalation.id)
    async with reopened.run_test(size=_SIZE) as pilot:
        screen = await _opened(reopened)
        answer = screen.query_one("#resolution-answer", TextArea)
        assert answer.text == _OWN_TEXT
        await pilot.press("enter")
        await pilot.pause()
        assert reopened.results == []
        assert queue.pending() == []
        assert queries.command_history().total_count == recorded_commands


async def test_engine_rejection_keeps_the_screen_open_with_its_reason(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Rejected send")
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.AGENT_ERROR
    )
    commands, queries, queue = operator_services(
        connection, reader, clock, tmp_path, engine_up=True
    )
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)
    reason = f"{escalation.id} is already resolved"

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        recorded_commands = queries.command_history().total_count
        await pilot.press("enter", "enter")
        await wait_until(lambda: len(queue.pending()) == 1, timeout=UI_WAIT_SECONDS)
        (sent,) = queue.pending()
        queue.mark_rejected(sent.id, {"error": reason})
        clock.advance(max(COMMAND_POLL_SECONDS, DASHBOARD_CHANGE_POLL_SECONDS))
        notice = screen.query_one("#resolution-notice")
        await wait_until(lambda: reason in _text_of(notice), timeout=UI_WAIT_SECONDS)
        assert app.results == []
        assert queue.pending() == []
        assert queries.command_history().total_count == recorded_commands + 1
