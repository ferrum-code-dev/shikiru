from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from textual.widgets import Static

from shikiru.clock import SystemClock
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_HINT,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RELATED_LIST,
    TASK_VIEW_AGENT,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_REQUEST,
    TASK_VIEW_WORKSPACE,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold
from shikiru.dashboard.detail_hint import HintTarget, hint_for
from shikiru.dashboard.details_list_view import DetailsSection, section_headline
from shikiru.dashboard.fold_bodies import CRITERIA_LABEL
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, TriageReport
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Status
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.tokens import ALERT_COLOR
from tests.support.cells import CYAN_BOLD, GREY, GREY_BOLD, Cell, foregrounds, styles
from tests.support.cells import rendered_lines as painted_lines
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import ManualFocusHost, widget_context
from tests.support.render import plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, WaitTimeoutError, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import ComplexityName
    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_WIDE_TITLES = ("Command history", "Header", "Escalations", "Queue", "Diagnostics")
PANEL_WIDTH = 100
COMPACT_PANEL_WIDTH = 40


async def _loaded(panel: TaskDetailPanel, task_id: str) -> None:
    await wait_until(
        lambda: (
            task_id in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
            and bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content)
        ),
        timeout=UI_WAIT_SECONDS,
    )


def _painted_rows(panel: TaskDetailPanel, list_id: str) -> list[str]:
    return [
        "".join(char for cells in painted_lines(row) for char, _ in cells)
        for row in panel.query(f"#{list_id} .row-list-row").results(Static)
    ]


def _body_lines(panel: TaskDetailPanel, widget_id: str) -> list[str]:
    return rendered_lines(panel.query_one(f"#{widget_id}", Static).content, width=PANEL_WIDTH)


def _first_line_starts(panel: TaskDetailPanel, widget_id: str, prefix: str) -> bool:
    lines = _body_lines(panel, widget_id)
    return bool(lines) and lines[0].startswith(prefix)


def _criteria_heading(body: Static) -> list[Cell]:
    none: list[Cell] = []
    return next(
        (
            row[: len(CRITERIA_LABEL)]
            for row in painted_lines(body)
            if "".join(char for char, _ in row).startswith(CRITERIA_LABEL)
        ),
        none,
    )


@pytest.mark.usefixtures("colour_terminal")
async def test_task_description_comes_first_with_criteria_as_a_list_and_no_project_folds(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Described", request="Make the parser strict")
    triage_dir = project_root / ".shikiru" / "runs" / task.id / "turns" / "01-triage-i0"
    triage_dir.mkdir(parents=True)
    (triage_dir / REPORT_KIND_FILENAME).write_text("triage")
    triage = TriageReport(
        title=task.title,
        mode=Mode.IMPLEMENT,
        complexity="normal",
        acceptance_criteria=["rejects bad input", "keeps good input"],
        references=[],
    )
    (triage_dir / REPORT_FILENAME).write_text(triage.model_dump_json())
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            titles = [fold.title for fold in panel.query(DetailFold)]
            assert titles == [section.value for section in DetailsSection]
            assert titles[0] == "Task description"
            assert titles.index("Dependencies") < titles.index("Workspace")
            assert not [t for t in titles if t.startswith(PROJECT_WIDE_TITLES)]
            await wait_until(
                lambda: (
                    "keeps good input"
                    in plain_text(panel.query_one("#task-detail-request", Static).content)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            lines = _body_lines(panel, "task-detail-request")
            assert lines[0] == "Make the parser strict"
            assert [line for line in lines if "input" in line] == [
                "\N{BULLET} rejects bad input",
                "\N{BULLET} keeps good input",
            ]
            request = panel.query_one("#task-detail-request", Static)
            await wait_until(lambda: bool(_criteria_heading(request)), timeout=UI_WAIT_SECONDS)
            assert {style for _, style in _criteria_heading(request)} == {CYAN_BOLD}
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_dependencies_align_colour_a_cancelled_blocker_and_open_on_enter(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    blocker = seed_task(connection, clock, title="Add database")
    blocked = seed_task(connection, clock, title="Add admin page", depends_on=(blocker.id,))
    dependent = seed_task(connection, clock, title="Ship release", depends_on=(blocked.id,))
    store = TaskStore(connection, clock)
    store.set_wait_reason(blocked.id, WaitReason.DEPENDENCY)
    store.apply_transition(blocker.id, Transition.stay(blocker, step=None, status=Status.CANCELLED))
    opened: list[str] = []

    async def open_task_detail(task_id: str) -> None:
        opened.append(task_id)

    reader = StateReader(database, SystemClock())
    context = replace(
        widget_context(reader, project_root, clock), open_task_detail=open_task_detail
    )
    panel = TaskDetailPanel(context, blocked.id, fixed=True, initial_view=TASK_VIEW_RELATIONSHIPS)
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)) as pilot:
            await _loaded(panel, blocked.id)
            await wait_until(
                lambda: (
                    all(_painted_rows(panel, TASK_DETAIL_RELATED_LIST))
                    and len(_painted_rows(panel, TASK_DETAIL_RELATED_LIST)) == 2
                ),
                timeout=UI_WAIT_SECONDS,
            )
            rows = _painted_rows(panel, TASK_DETAIL_RELATED_LIST)
            assert rows[0].lstrip().startswith("Blocked by")
            assert rows[1].lstrip().startswith("Blocks")
            assert rows[0].index(blocker.id) == rows[1].index(dependent.id)
            assert rows[0].index("Add database") == rows[1].index("Ship release")
            assert rows[0].index("\N{BALLOT X} cancelled") == rows[1].index("\N{MIDDLE DOT} queued")
            first_title = panel.query(".row-list-row").first(Static)
            assert set(foregrounds(first_title, "Add database")) == {ALERT_COLOR}
            panel.query_one(f"#{TASK_DETAIL_RELATED_LIST}").focus()
            await pilot.press("enter")
            await wait_until(lambda: opened == [blocker.id], timeout=UI_WAIT_SECONDS)
    finally:
        reader.close()


def _title_line(panel: TaskDetailPanel) -> str:
    identity = panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
    return "".join(char for char, _ in painted_lines(identity)[0])


@pytest.mark.usefixtures("colour_terminal")
@pytest.mark.parametrize(
    ("complexity", "shown"), [("low", "Low"), ("normal", "Normal"), ("high", "High")]
)
async def test_recorded_complexity_follows_the_title_with_its_level_bold_and_the_word_grey(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    complexity: ComplexityName,
    shown: str,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ship parser", complexity=complexity)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            identity = panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            assert _title_line(panel) == f"{task.id} · Ship parser · {shown} complexity"
            assert set(styles(identity, shown)) == {GREY_BOLD}
            assert set(styles(identity, "complexity")) == {GREY}
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_compact_detail_keeps_the_recorded_complexity_on_the_title_line(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ship parser", complexity="high")
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(COMPACT_PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            assert _title_line(panel) == f"{task.id} · Ship parser · High complexity"
    finally:
        reader.close()


async def test_unset_complexity_leaves_the_title_without_a_suffix(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ship parser")
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            assert _title_line(panel) == f"{task.id} · Ship parser"
    finally:
        reader.close()


async def test_title_complexity_follows_the_selected_task(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    rated = seed_task(connection, clock, title="Ship parser", complexity="high")
    unrated = seed_task(connection, clock, title="Write docs")
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        rated.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, rated.id)
            panel.select_task(unrated.id)
            await _loaded(panel, unrated.id)
            assert _title_line(panel) == f"{unrated.id} · Write docs"
            panel.select_task(rated.id)
            await _loaded(panel, rated.id)
            assert _title_line(panel) == f"{rated.id} · Ship parser · High complexity"
    finally:
        reader.close()


async def test_recorded_complexity_reaches_the_title_on_the_next_refresh(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Ship parser")
    store = TaskStore(connection, clock)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_REQUEST,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            assert _title_line(panel) == f"{task.id} · Ship parser"
            store.apply_transition(
                task.id,
                replace(Transition.stay(task, step=None, status=task.status), complexity="normal"),
            )
            panel.refresh_after_resume()
            await wait_until(
                lambda: _title_line(panel) == f"{task.id} · Ship parser · Normal complexity",
                timeout=UI_WAIT_SECONDS,
            )
    finally:
        reader.close()


SETTINGS_HEADLINES = (
    ("implement", 0, "implement · standard priority"),
    ("plan", 5, "plan · high priority"),
    ("implement", -2, "implement · low priority"),
    (None, 1, "high priority"),
    ("plan", None, "plan"),
    (None, None, None),
)


async def test_workspace_and_settings_tables_repeat_no_title(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    reader = StateReader(database, SystemClock())
    try:
        for status in (Status.RUNNING, Status.DONE):
            task = seed_task(connection, clock, title="Workspace target", priority=3)
            TaskStore(connection, clock).apply_transition(
                task.id, Transition.stay(task, step=None, status=status)
            )
            panel = TaskDetailPanel(
                widget_context(reader, project_root, clock),
                task.id,
                fixed=True,
                initial_view=TASK_VIEW_WORKSPACE,
            )
            async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
                await _loaded(panel, task.id)
                await wait_until(
                    lambda panel=panel: _first_line_starts(panel, "task-detail-workspace", "Path"),
                    timeout=UI_WAIT_SECONDS,
                )
                await wait_until(
                    lambda panel=panel: _first_line_starts(
                        panel, "task-detail-context-info", "Submitted"
                    ),
                    timeout=UI_WAIT_SECONDS,
                )
                workspace = _body_lines(panel, "task-detail-workspace")
                settings = _body_lines(panel, "task-detail-context-info")
                assert workspace[0].startswith("Path"), status
                assert settings[0].startswith("Submitted"), status
                assert any(
                    line.startswith("Priority") and line.endswith("high (3)") for line in settings
                )
    finally:
        reader.close()
    for mode, priority, headline in SETTINGS_HEADLINES:
        view: Any = SimpleNamespace(mode=mode, priority=priority)
        assert section_headline(DetailsSection.SETTINGS, view) == headline


async def _shows_hint(panel: TaskDetailPanel, expected: str) -> None:
    hint = panel.query_one(f"#{TASK_DETAIL_HINT}", Static)
    try:
        await wait_until(
            lambda: plain_text(hint.content) == expected,
            timeout=UI_WAIT_SECONDS,
        )
    except WaitTimeoutError as error:
        focused = panel.screen.focused
        focused_fold = (
            next(
                (
                    ancestor
                    for ancestor in focused.ancestors_with_self
                    if isinstance(ancestor, DetailFold)
                ),
                None,
            )
            if focused is not None
            else None
        )
        message = (
            f"task detail hint did not settle for task {panel.task_id!r}: "
            f"expected={expected!r}, actual={plain_text(hint.content)!r}, "
            f"focused={focused!r}, "
            f"fold={focused_fold.view_id if focused_fold is not None else None}"
        )
        raise AssertionError(message) from error


ARROWS = "\N{UPWARDS ARROW}\N{DOWNWARDS ARROW}"
HEADER_HINT = f"{ARROWS} move · Enter open/close"
FOCUS_HINTS = (
    (TASK_VIEW_EVIDENCE, f"{ARROWS} select · End follow · Esc back"),
    (TASK_VIEW_AGENT, f"{ARROWS} select · End follow · f filter · Esc back"),
    (TASK_VIEW_RELATIONSHIPS, f"{ARROWS} select · Enter open · Esc back"),
    (TASK_VIEW_WORKSPACE, f"{ARROWS} move · Enter open/close · t task window"),
)


async def test_bottom_hint_follows_focus_and_t_opens_the_task_window_only_from_a_live_workspace(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    store = TaskStore(connection, clock)
    live = seed_task(connection, clock, title="Live")
    done = seed_task(connection, clock, title="Done")
    store.set_window(live.id, "@1")
    store.set_window(done.id, "@2")
    store.apply_transition(done.id, Transition.stay(done, step=None, status=Status.DONE))
    store.apply_transition(live.id, Transition.stay(live, step=None, status=Status.RUNNING))
    reader = StateReader(database, SystemClock())
    opened: list[str] = []

    async def open_window(task_id: str) -> str:
        opened.append(task_id)
        return task_id

    context = replace(widget_context(reader, project_root, clock), navigate_task_window=open_window)

    try:
        for task in (live, done):
            opened.clear()
            panel = TaskDetailPanel(context, task.id, fixed=True, initial_view=TASK_VIEW_REQUEST)
            async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)) as pilot:
                await _loaded(panel, task.id)
                panel.focus_folds()
                await _shows_hint(panel, HEADER_HINT)
                for view_id, hint in FOCUS_HINTS:
                    panel.open_view(view_id)
                    no_window = task is done and view_id == TASK_VIEW_WORKSPACE
                    await _shows_hint(panel, HEADER_HINT if no_window else hint)
                    await pilot.press("t")
                if task is live:
                    await wait_until(lambda: opened == [live.id], timeout=UI_WAIT_SECONDS)
                assert opened == ([live.id] if task is live else [])
    finally:
        reader.close()
    tiny = hint_for(HintTarget.EVENTS, task_window=False, keys_only=True)
    assert tiny.plain == f"{ARROWS} · End · Esc"
