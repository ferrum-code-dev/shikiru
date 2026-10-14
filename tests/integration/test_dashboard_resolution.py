from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from textual.geometry import Region
from textual.widgets import Button, Select, Static, TextArea

from shikiru.clock import SystemClock
from shikiru.dashboard.palette import PaletteApp, PaletteScreen
from shikiru.dashboard.resolution import ResolutionDrafts, ResolutionScreen
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Resolve,
    RetryResolution,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.operator.commands import COMMAND_POLL_SECONDS
from shikiru.store.escalations import EscalationStore
from shikiru.store.reader import DASHBOARD_CHANGE_POLL_SECONDS, StateReader
from shikiru.ui.tokens import (
    AMBER,
    DUSTY_ROSE,
    SAGE,
    SECONDARY_TEXT_COLOR,
    SELECTED_SURFACE_COLOR,
    STEEL_BLUE,
)
from tests.support.cells import foregrounds, painted_backgrounds
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import dashboard_app
from tests.support.escalations import (
    ResolutionHost,
    assert_engine_applies_resolution,
    enqueue_and_cancel,
    operator_services,
    resolution_factory,
    resolution_title,
    seed_escalation,
    wait_for_resolution,
)
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from textual.pilot import Pilot
    from textual.widget import Widget

    from shikiru.dashboard.app import DashboardApp
    from shikiru.dashboard.contract import PaletteRequest
    from shikiru.store.database import Database
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_SIZE = (100, 40)
_HOME_SIZE = (160, 50)
_OWN_ANSWER_ROW = 2
_ACCEPT_ROW = 3
_CANCEL_ROW_AFTER_RETRY = 1
_ALTERNATIVE = "Change behavior"
_PREFILL = " \N{EM DASH} "
_DETAILS = "backend at 10.0.0.1"
_OWN_TEXT = "Keep the current behavior."
_ARMED_TEXT = "Press Enter again to cancel"
_ATTEMPTED = (
    "The Leader escalated.\n"
    "Rounds: implement \N{MULTIPLICATION SIGN}3 \N{MIDDLE DOT} verify \N{MULTIPLICATION SIGN}2\n"
    "Implementer: 3 commits, 1 failed\n"
    "Leader verdict: changes requested (1 blocker)"
)
_SELECTED_CLASS = "-selected"


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
            "flagged-enter-details",
            AnswerResolution(text=f"{_ALTERNATIVE}{_PREFILL}{_DETAILS}"),
            id="flagged-alternative-sends-label-and-details",
        ),
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "flagged-ctrl+j",
            AnswerResolution(text=f"{_ALTERNATIVE}{_PREFILL}{_DETAILS}"),
            id="ctrl-j-sends",
        ),
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "flagged-ctrl+enter",
            AnswerResolution(text=f"{_ALTERNATIVE}{_PREFILL}{_DETAILS}"),
            id="ctrl-enter-sends",
        ),
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "flagged-empty",
            AnswerResolution(text=_ALTERNATIVE),
            id="empty-box-sends-the-label-alone",
        ),
        pytest.param(
            EscalationReason.DECISION_REQUIRED,
            "own-draft",
            AnswerResolution(text=_OWN_TEXT),
            id="own-answer-draft-is-restored-and-sent",
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
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, reason, needs_input=act.startswith("flagged")
    )
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    drafts = ResolutionDrafts()
    if act == "own-draft":
        drafts.remember(task.id, escalation.id, _OWN_TEXT)
    app = ResolutionHost(
        resolution_factory(commands, queries, clock, reader, drafts=drafts), escalation.id
    )

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        assert queue.pending() == []
        if act == "alternative":
            await pilot.press("down", "enter")
        elif act.startswith("flagged") or act == "own-draft":
            await pilot.press(*(["enter"] if act == "own-draft" else ["down", "enter"]))
            answer = screen.query_one("#resolution-answer", TextArea)
            await wait_until(lambda: app.focused is answer, timeout=UI_WAIT_SECONDS)
            assert queue.pending() == []
            assert answer.text == (_OWN_TEXT if act == "own-draft" else "")
            if act in {"flagged-enter-details", "flagged-ctrl+j", "flagged-ctrl+enter"}:
                await _type(pilot, _DETAILS)
            await pilot.press(act.removeprefix("flagged-") if "+" in act else "ctrl+s")
        elif act == "own-answer":
            await pilot.click(_rows(screen)[_OWN_ANSWER_ROW])
            answer = screen.query_one("#resolution-answer", TextArea)
            await wait_until(lambda: app.focused is answer, timeout=UI_WAIT_SECONDS)
            await _type(pilot, _OWN_TEXT)
            assert queue.pending() == []
            await pilot.press("ctrl+s")
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


async def test_the_answer_box_follows_its_row_and_the_keys_move_between_box_and_list(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Box follows row")
    escalation, _ = seed_escalation(
        connection,
        clock,
        task.id,
        tmp_path,
        EscalationReason.DECISION_REQUIRED,
        needs_input=True,
    )
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)

    async def focused_box_after(row: int) -> TextArea:
        screen = app.screen
        assert isinstance(screen, ResolutionScreen)
        answer = screen.query_one("#resolution-answer", TextArea)
        await wait_until(lambda: app.focused is answer, timeout=UI_WAIT_SECONDS)
        assert answer.parent is not None
        siblings = list(answer.parent.children)
        assert siblings[siblings.index(answer) - 1] is _rows(screen)[row]
        return answer

    async def back_in_list() -> None:
        await wait_until(
            lambda: app.focused is not None and app.focused.id == "resolution-choices",
            timeout=UI_WAIT_SECONDS,
        )
        assert isinstance(app.screen, ResolutionScreen)

    async with app.run_test(size=_SIZE) as pilot:
        await _opened(app)
        await pilot.press("down", "right")
        answer = await focused_box_after(1)
        await _type(pilot, _DETAILS)
        await pilot.press("enter")
        await wait_until(lambda: answer.document.line_count == 2, timeout=UI_WAIT_SECONDS)
        await pilot.press("left")
        assert app.focused is answer
        await pilot.press("up", "home")
        await wait_until(lambda: answer.cursor_location == (0, 0), timeout=UI_WAIT_SECONDS)
        await pilot.press("left")
        await back_in_list()
        await pilot.press("right")
        await focused_box_after(1)
        await pilot.press("escape")
        await back_in_list()
        await pilot.press("down", "enter")
        answer = await focused_box_after(2)
        assert answer.text == ""
        await _type(pilot, _OWN_TEXT)
        await pilot.press("escape")
        await back_in_list()
        await pilot.press("up", "enter")
        answer = await focused_box_after(1)
        assert answer.text == ""
        await pilot.press("ctrl+s")
        await wait_until(lambda: app.results == [True], timeout=UI_WAIT_SECONDS)

    (queued,) = queue.pending()
    assert isinstance(queued.command, Resolve)
    assert queued.command.resolution == AnswerResolution(text=_ALTERNATIVE)


@pytest.mark.parametrize("size", [(40, 14), (40, 8)])
async def test_down_selects_the_next_choice_when_the_panel_itself_scrolls(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    size: tuple[int, int],
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Small pane")
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.DECISION_REQUIRED
    )
    commands, queries, _ = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)

    async with app.run_test(size=size) as pilot:
        screen = await _opened(app)
        await pilot.press("down")
        await wait_until(
            lambda: _rows(screen)[1].has_class(_SELECTED_CLASS), timeout=UI_WAIT_SECONDS
        )


@pytest.mark.parametrize("record", ["missing-packet", "malformed-packet", "unknown-decision"])
async def test_a_degraded_or_unknown_decision_opens_with_its_reason_and_dispatches_nothing(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    record: str,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Degraded record")
    escalation, packet = seed_escalation(
        connection,
        clock,
        task.id,
        tmp_path,
        EscalationReason.DECISION_REQUIRED,
        write_packet=record != "missing-packet",
    )
    if record == "malformed-packet":
        malformed = packet.replace("## Agent alternatives", "{ ?? }")
        await asyncio.to_thread(Path(escalation.packet_path).write_text, malformed)
    target = "E-404" if record == "unknown-decision" else escalation.id
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), target)

    async with app.run_test(size=_SIZE) as pilot:
        screen = await wait_for_resolution(app)
        assert target in resolution_title(screen)
        assert bool(_rows(screen)) is (record != "unknown-decision")
        await pilot.press("enter")
        await pilot.pause()
        assert queue.pending() == []


@pytest.mark.usefixtures("colour_terminal")
async def test_details_is_its_own_tab_stop_whose_highlight_excludes_the_rule(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Details stop")
    escalation, _ = seed_escalation(
        connection,
        clock,
        task.id,
        tmp_path,
        EscalationReason.DECISION_REQUIRED,
        attempted=_ATTEMPTED,
    )
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = ResolutionHost(resolution_factory(commands, queries, clock, reader), escalation.id)

    async with app.run_test(size=_SIZE) as pilot:
        screen = await _opened(app)
        rows = _rows(screen)
        details = screen.query_one("#resolution-tried")
        siblings = screen.query_one("#resolution-body").children
        rule = siblings[siblings.index(details) - 1]
        choose = screen.query_one("#resolution-choose")
        assert set(foregrounds(choose, "Enter")) == {STEEL_BLUE}
        await pilot.press(*["down"] * len(rows))
        await wait_until(lambda: rows[-1].has_class(_SELECTED_CLASS), timeout=UI_WAIT_SECONDS)
        assert app.focused is not None
        assert app.focused.id == "resolution-choices"
        await pilot.press("tab")
        await wait_until(lambda: app.focused is details, timeout=UI_WAIT_SECONDS)
        assert painted_backgrounds(details) == {SELECTED_SURFACE_COLOR}
        assert SELECTED_SURFACE_COLOR not in painted_backgrounds(rule)
        await pilot.press("enter")
        await wait_until(lambda: "Reason" in _text_of(details), timeout=UI_WAIT_SECONDS)
        assert queue.pending() == []
        assert app.results == []
        assert set(foregrounds(details, "Rounds")) == {SECONDARY_TEXT_COLOR}
        assert set(foregrounds(details, "implement")) == {STEEL_BLUE}
        assert set(foregrounds(details, "3 commits")) == {SAGE}
        assert set(foregrounds(details, "1 failed")) == {DUSTY_ROSE}
        assert set(foregrounds(details, "changes requested")) == {AMBER}
        await pilot.press("space")
        await wait_until(lambda: "Reason" not in _text_of(details), timeout=UI_WAIT_SECONDS)
        await pilot.press("up")
        await wait_until(
            lambda: app.focused is not None and app.focused.id == "resolution-choices",
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.press("tab", "shift+tab")
        await wait_until(
            lambda: app.focused is not None and app.focused.id == "resolution-choices",
            timeout=UI_WAIT_SECONDS,
        )
        assert queue.pending() == []


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


async def _no_pane_selection(pane_id: str) -> None:
    raise AssertionError(pane_id)


@pytest.mark.parametrize(
    "route",
    ["bar-enter", "bar-click", "strip-click", "task-menu-answer", "ctrl-p-resolve"],
)
async def test_every_entry_route_opens_the_resolution_screen_without_dispatching(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    route: str,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = SystemClock()
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Entry route")
    escalation, _ = seed_escalation(
        connection, clock, task.id, project_dir, EscalationReason.DECISION_REQUIRED
    )
    commands, queries, queue = operator_services(connection, reader, clock, project_dir)
    factory = resolution_factory(commands, queries, clock, reader)
    apps: list[DashboardApp] = []

    async def open_palette_context(palette_request: PaletteRequest) -> None:
        await apps[0].push_screen(
            PaletteScreen(
                commands,
                queries,
                _no_pane_selection,
                on_change=reader.watch,
                initial_context=palette_request,
                task_routes_available=True,
                resolution_screen=factory,
            )
        )

    widgets = ["queue"] if route == "strip-click" else ["queue", "escalations"]
    app = dashboard_app(
        reader,
        project_dir,
        clock,
        widget_names=widgets,
        open_palette_context=open_palette_context,
        resolution_screen=factory,
    )
    apps.append(app)

    async with app.run_test(size=_HOME_SIZE) as pilot:
        bar = app.query_one("#escalations", Static)
        await wait_until(
            lambda: bar.display and escalation.id in _text_of(_bar_row(app, route)),
            timeout=UI_WAIT_SECONDS,
        )
        if route == "bar-enter":
            await pilot.press("enter")
        elif route in {"bar-click", "strip-click"}:
            await pilot.click(_bar_row(app, route))
        else:
            await pilot.press("ctrl+p")
            await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=UI_WAIT_SECONDS)
            if route == "task-menu-answer":
                await pilot.click("#palette-menu-answer")
            else:
                await pilot.click("#palette-menu-more-actions")
                resolve = app.screen.query_one("#palette-menu-action-resolve", Button)
                await wait_until(lambda: resolve.region.height > 0, timeout=UI_WAIT_SECONDS)
                resolve.press()
        screen = await wait_for_resolution(app)
        assert f"{escalation.id} · {task.id}" in resolution_title(screen)
        assert queue.pending() == []


def _bar_row(app: DashboardApp, route: str) -> Widget:
    bar = app.query_one("#escalations", Static)
    return bar if route == "strip-click" else bar.query_one("#escalation-panels").query_one(Static)


@pytest.mark.parametrize("pending", [True, False])
async def test_the_generic_resolve_action_opens_the_first_decision_or_says_none_is_needed(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    pending: bool,
) -> None:
    clock = FakeClock(_START)
    reader = StateReader(database, clock)
    request.addfinalizer(reader.close)
    task = seed_task(connection, clock, title="Generic resolve")
    escalation, _ = seed_escalation(
        connection, clock, task.id, tmp_path, EscalationReason.AGENT_ERROR
    )
    if not pending:
        EscalationStore(connection, clock).resolve(escalation.id, "closed elsewhere")
    commands, queries, queue = operator_services(connection, reader, clock, tmp_path)
    app = PaletteApp(
        commands,
        queries,
        _no_pane_selection,
        on_change=reader.watch,
        resolution_screen=resolution_factory(commands, queries, clock, reader),
    )

    async with app.run_test(size=_SIZE) as pilot:
        await wait_until(lambda: isinstance(app.screen, PaletteScreen), timeout=UI_WAIT_SECONDS)
        palette = app.screen
        action = cast("Select[str]", palette.query_one("#action", Select))
        action.focus()
        action.value = "resolve"
        if pending:
            assert escalation.id in resolution_title(await wait_for_resolution(app))
        else:
            await wait_until(
                lambda: _text_of(palette.query_one("#palette-result")).strip() != "",
                timeout=UI_WAIT_SECONDS,
            )
            assert app.screen is palette
        await pilot.pause()
        assert queue.pending() == []
