from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.widgets import Static

from shikiru.clock import SystemClock
from shikiru.dashboard.detail import (
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RELATED_LIST,
    TASK_DETAIL_WORKSPACE_TOOLS,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_REQUEST,
    TASK_VIEW_WORKSPACE,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold
from shikiru.dashboard.details_list_view import DetailsSection
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, TriageReport
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Status
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.ui.tokens import ALERT_COLOR
from tests.support.cells import foregrounds
from tests.support.cells import rendered_lines as painted_lines
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import WidgetHostApp, widget_context
from tests.support.render import plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_WIDE_TITLES = ("Command history", "Header", "Escalations", "Queue", "Diagnostics")
PANEL_WIDTH = 100


async def _loaded(panel: TaskDetailPanel, task_id: str) -> None:
    await wait_until(
        lambda: task_id in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content),
        timeout=UI_WAIT_SECONDS,
    )


def _painted_rows(panel: TaskDetailPanel, list_id: str) -> list[str]:
    return [
        "".join(char for cells in painted_lines(row) for char, _ in cells)
        for row in panel.query(f"#{list_id} .row-list-row").results(Static)
    ]


def _body_lines(panel: TaskDetailPanel, widget_id: str) -> list[str]:
    return rendered_lines(panel.query_one(f"#{widget_id}", Static).content, width=PANEL_WIDTH)


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
        async with WidgetHostApp(panel).run_test(size=(PANEL_WIDTH, 40)):
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
        async with WidgetHostApp(panel).run_test(size=(PANEL_WIDTH, 40)) as pilot:
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


@pytest.mark.parametrize(
    ("status", "tools_shown"),
    [(Status.RUNNING, True), (Status.DONE, False)],
)
async def test_workspace_tools_show_only_for_live_tasks_and_tables_repeat_no_title(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    status: Status,
    tools_shown: bool,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Workspace target", priority=3)
    TaskStore(connection, clock).apply_transition(
        task.id, Transition.stay(task, step=None, status=status)
    )
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_WORKSPACE,
    )
    try:
        async with WidgetHostApp(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            tools = panel.query_one(f"#{TASK_DETAIL_WORKSPACE_TOOLS}")
            await wait_until(
                lambda: (
                    tools.display is tools_shown
                    and bool(_body_lines(panel, "task-detail-workspace"))
                ),
                timeout=UI_WAIT_SECONDS,
            )
            await wait_until(
                lambda: bool(_body_lines(panel, "task-detail-context-info")),
                timeout=UI_WAIT_SECONDS,
            )
            workspace = _body_lines(panel, "task-detail-workspace")
            settings = _body_lines(panel, "task-detail-context-info")
            assert workspace[0].startswith("Path")
            assert settings[0].startswith("Submitted")
            assert any(line.startswith("Priority") and line.endswith("3") for line in settings)
    finally:
        reader.close()
