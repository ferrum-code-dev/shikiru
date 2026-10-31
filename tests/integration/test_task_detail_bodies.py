from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum, auto
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from rich.cells import cell_len
from textual.widgets import Static

from shikiru.agents.contracts import Rung
from shikiru.clock import SystemClock
from shikiru.dashboard.blocker_rows import BLOCKERS_LABEL
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_CONTEXT_INFO,
    TASK_DETAIL_EVIDENCE,
    TASK_DETAIL_FOLDS,
    TASK_DETAIL_HINT,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RELATED_LIST,
    TASK_VIEW_AGENT,
    TASK_VIEW_BLOCKER_EVIDENCE,
    TASK_VIEW_EVIDENCE,
    TASK_VIEW_RELATIONSHIPS,
    TASK_VIEW_REQUEST,
    TASK_VIEW_WORKSPACE,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import CLOSED_MARK, DetailFold, DetailFolds
from shikiru.dashboard.detail_hint import HintTarget, hint_for
from shikiru.dashboard.details_list_view import DetailsSection, section_headline
from shikiru.dashboard.fold_bodies import CRITERIA_LABEL, CRITERION_LEAD, NO_CRITERIA
from shikiru.domain.quota_waits import quota_reset_clock
from shikiru.domain.reports import REPORT_FILENAME, REPORT_KIND_FILENAME, TriageReport
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition, first_step
from shikiru.domain.tasks import Hold, Mode, Role, Status, Task
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.glyphs import GLYPH_WARNING
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SECONDARY_TEXT_COLOR
from tests.support.cells import CYAN_BOLD, GREY, GREY_BOLD, Cell, foregrounds, styles
from tests.support.cells import rendered_lines as painted_lines
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import ManualFocusHost, fold_headline, widget_context
from tests.support.render import plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, WaitTimeoutError, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.config.settings import ComplexityName
    from shikiru.dashboard.contract import DashboardViews
    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_WIDE_TITLES = ("Command history", "Header", "Escalations", "Queue", "Diagnostics")
PANEL_WIDTH = 100
COMPACT_PANEL_WIDTH = 40
LEADER_AGENT = "claude"
MAX_PARALLEL = 2
QUOTA_BLOCK_UNTIL = START + timedelta(minutes=80)
QUOTA_RESET = "01:20Z"
QUOTA_CAUSE = "Waiting on quota"
QUOTA_REMAINING = "in 1h 20m"
QUOTA_REMAINING_LATER = "in 1h 9m"
QUOTA_REMAINING_79_MINUTES = "in 1h 19m"
QUOTA_REMAINING_78_MINUTES = "in 1h 18m"
MIDNIGHT_EVE = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)
MIDNIGHT_RESET = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)
MIDNIGHT_RESET_DATED = "Jan 02 01:00Z"
MIDNIGHT_RESET_CLOCK = "01:00Z"
MIDNIGHT_REMAINING = "in 1h 0m"
TIMELINE_REFRESH_SECONDS = 1
COUNTDOWN_SECONDS = 10 * 60
ONE_MINUTE_SECONDS = 60
ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T")


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


def _write_triage(project_root: Path, task: Task, criteria: list[str]) -> None:
    triage_dir = project_root / ".shikiru" / "runs" / task.id / "turns" / "01-triage-i0"
    triage_dir.mkdir(parents=True)
    (triage_dir / REPORT_KIND_FILENAME).write_text("triage")
    triage = TriageReport(
        title=task.title,
        mode=Mode.IMPLEMENT,
        complexity="normal",
        acceptance_criteria=criteria,
        references=[],
    )
    (triage_dir / REPORT_FILENAME).write_text(triage.model_dump_json())


_HEADED_REQUEST = "Make the parser strict\n\n## Acceptance criteria\n- from the request\n"
_PLAIN_REQUEST = "Make the parser strict\n\n- a list that is not criteria\n"
_WRAPPED_REQUEST = (
    "Make the parser strict\n\n## Acceptance criteria\n- Reject invalid input and\n"
    "  preserve the original file.\n- Keep valid input.\n"
)
_FENCED_EXAMPLE_REQUEST = (
    "Make the parser strict\n\n```markdown\n## Acceptance criteria\n- example only\n```\n"
)
_BOLD_COLON_REQUEST = (
    "Before the section.\n\n**Acceptance criteria:**\n- Reject invalid input\n"
    "\nAfter the section.\n"
)
DESCRIPTION_CRITERIA_CASES = [
    pytest.param(
        ["from triage"],
        _HEADED_REQUEST,
        ("from triage",),
        ("Make the parser strict", ""),
        id="triage-criteria-without-duplicating-the-request-heading",
    ),
    pytest.param(
        ["from triage"],
        _PLAIN_REQUEST,
        ("from triage",),
        ("Make the parser strict", "", "- a list that is not criteria", ""),
        id="triage-criteria-without-a-request-heading",
    ),
    pytest.param(
        [],
        _HEADED_REQUEST,
        ("from the request",),
        ("Make the parser strict", ""),
        id="empty-triage-criteria-fall-back-to-the-request-heading",
    ),
    pytest.param(
        None,
        _HEADED_REQUEST,
        ("from the request",),
        ("Make the parser strict", ""),
        id="skipped-triage-reads-the-request-heading",
    ),
    pytest.param(
        None,
        _PLAIN_REQUEST,
        (),
        ("Make the parser strict", "", "- a list that is not criteria", ""),
        id="skipped-triage-without-a-heading-keeps-the-notice",
    ),
    pytest.param(
        None,
        _WRAPPED_REQUEST,
        ("Reject invalid input and preserve the original file.", "Keep valid input."),
        ("Make the parser strict", ""),
        id="skipped-triage-keeps-wrapped-criteria-whole",
    ),
    pytest.param(
        None,
        _FENCED_EXAMPLE_REQUEST,
        (),
        (
            "Make the parser strict",
            "",
            "```markdown",
            "## Acceptance criteria",
            "- example only",
            "```",
            "",
        ),
        id="skipped-triage-ignores-a-fenced-example",
    ),
    pytest.param(
        None,
        _BOLD_COLON_REQUEST,
        ("Reject invalid input",),
        ("Before the section.", "", "After the section.", ""),
        id="skipped-triage-reads-a-bold-colon-heading-and-keeps-the-text-around-it",
    ),
]


@pytest.mark.usefixtures("colour_terminal")
async def test_task_description_comes_first_with_criteria_as_a_list_and_no_project_folds(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Described", request="Make the parser strict")
    _write_triage(project_root, task, ["rejects bad input", "keeps good input"])
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
            main_folds = panel.query_one(f"#{TASK_DETAIL_FOLDS}", DetailFolds)
            titles = [fold.title for fold in main_folds.query(DetailFold)]
            assert titles == [
                section.value
                for section in DetailsSection
                if section is not DetailsSection.BLOCKER_EVIDENCE
            ]
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


@pytest.mark.parametrize(
    ("triage_criteria", "request_text", "expected", "description"), DESCRIPTION_CRITERIA_CASES
)
async def test_description_lists_triage_criteria_else_the_request_heading_else_the_notice(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    *,
    triage_criteria: list[str] | None,
    request_text: str,
    expected: tuple[str, ...],
    description: tuple[str, ...],
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(
        connection,
        clock,
        title="Described",
        request=request_text,
        mode=None if triage_criteria is not None else Mode.IMPLEMENT,
    )
    if triage_criteria is not None:
        _write_triage(project_root, task, triage_criteria)
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
            await wait_until(
                lambda: any(
                    line.startswith(CRITERIA_LABEL)
                    for line in _body_lines(panel, "task-detail-request")
                ),
                timeout=UI_WAIT_SECONDS,
            )
            lines = _body_lines(panel, "task-detail-request")
            criteria = tuple(
                line.removeprefix(CRITERION_LEAD)
                for line in lines
                if line.startswith(CRITERION_LEAD)
            )
            assert tuple(lines[: lines.index(CRITERIA_LABEL)]) == description
            assert criteria == expected
            assert (NO_CRITERIA in lines) == (not expected)
            stored = reader.get_task(task.id)
            assert stored is not None
            assert stored.request == request_text
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


class _Blocker(StrEnum):
    SLOT = auto()
    QUOTA = auto()
    DEPENDENCY = auto()


def _start_on_slot(connection: Connection, clock: FakeClock, task_id: str, slot: int) -> None:
    store = TaskStore(connection, clock)
    task = store.require(task_id)
    store.set_slot(task_id, slot)
    store.apply_transition(task_id, first_step(task))


def _seed_blocked_task(
    connection: Connection,
    clock: FakeClock,
    blocker: _Blocker,
    *,
    until: datetime = QUOTA_BLOCK_UNTIL,
) -> tuple[str, str]:
    store = TaskStore(connection, clock)
    match blocker:
        case _Blocker.SLOT:
            for slot in range(MAX_PARALLEL):
                occupant = seed_task(
                    connection, clock, title=f"Slot occupant {slot}", mode=Mode.IMPLEMENT
                )
                _start_on_slot(connection, clock, occupant.id, slot)
            waiter = seed_task(connection, clock, title="Slot waiter")
            store.set_wait_reason(waiter.id, WaitReason.SLOT)
            return waiter.id, f"Waiting for a slot · {MAX_PARALLEL}/{MAX_PARALLEL} busy"
        case _Blocker.QUOTA:
            waiter = seed_task(connection, clock, title="Quota waiter")
            store.set_wait_reason(waiter.id, WaitReason.QUOTA)
            QuotaStore(connection).block(LEADER_AGENT, until, "quota")
            reset = quota_reset_clock(until, clock.now())
            return waiter.id, f"Waiting on quota · {LEADER_AGENT} quota blocked until {reset}"
        case _Blocker.DEPENDENCY:
            dependency = seed_task(connection, clock, title="Add database")
            waiter = seed_task(
                connection, clock, title="Add admin page", depends_on=(dependency.id,)
            )
            store.set_wait_reason(waiter.id, WaitReason.DEPENDENCY)
            return waiter.id, f"Waiting on dependency · {dependency.id} queued"


def _widget_text(widget: Widget) -> str:
    return "\n".join("".join(char for char, _ in row) for row in painted_lines(widget))


def _briefing_text(panel: TaskDetailPanel) -> str:
    return _widget_text(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic))


def _quota_headline(panel: TaskDetailPanel) -> str:
    return next((line for line in _briefing_text(panel).splitlines() if QUOTA_CAUSE in line), "")


def _trace_statements(reader: StateReader) -> list[str]:
    statements: list[str] = []
    reader._connection.set_trace_callback(statements.append)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    return statements


def _reads(statements: list[str]) -> list[str]:
    return [statement for statement in statements if not statement.startswith("PRAGMA")]


async def _live_work_reads(views: DashboardViews, task_id: str, statements: list[str]) -> list[str]:
    statements.clear()
    await views.live_work(task_id)
    return _reads(statements)


@pytest.mark.usefixtures("colour_terminal")
@pytest.mark.parametrize(
    ("blocker", "details"),
    [
        (_Blocker.SLOT, ("Starts when a slot frees.",)),
        (_Blocker.QUOTA, (f"Blocked agent · {LEADER_AGENT}",)),
        (_Blocker.DEPENDENCY, ()),
    ],
)
async def test_blocker_headline_has_an_amber_glyph_and_normal_cause_with_grey_details(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    *,
    blocker: _Blocker,
    details: tuple[str, ...],
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, cause = _seed_blocked_task(connection, clock, blocker)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task_id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task_id)
            await wait_until(lambda: cause in _briefing_text(panel), timeout=UI_WAIT_SECONDS)
            briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
            assert set(styles(briefing, GLYPH_WARNING)) == {(True, AMBER)}
            assert AMBER not in foregrounds(briefing, cause)
            assert set(foregrounds(briefing, BLOCKERS_LABEL)) == {SECONDARY_TEXT_COLOR}
            for detail in details:
                assert set(foregrounds(briefing, detail)) == {SECONDARY_TEXT_COLOR}
            assert ISO_TIMESTAMP.search(_briefing_text(panel)) is None
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_a_cancelled_blocker_paints_the_blocker_glyph_and_headline_in_alert_colour(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    dependency = seed_task(connection, clock, title="Add database")
    blocked = seed_task(connection, clock, title="Add admin page", depends_on=(dependency.id,))
    store = TaskStore(connection, clock)
    store.set_wait_reason(blocked.id, WaitReason.DEPENDENCY)
    store.apply_transition(
        dependency.id, Transition.stay(dependency, step=None, status=Status.CANCELLED)
    )
    cause = f"Waiting on dependency · {dependency.id} cancelled"
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        blocked.id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, blocked.id)
            await wait_until(lambda: cause in _briefing_text(panel), timeout=UI_WAIT_SECONDS)
            briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
            assert set(styles(briefing, GLYPH_WARNING)) == {(True, ALERT_COLOR)}
            assert set(foregrounds(briefing, cause)) == {ALERT_COLOR}
    finally:
        reader.close()


async def test_a_task_without_a_blocker_shows_no_blockers_label_and_no_evidence_fold(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Free task")
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            evidence = _evidence_fold(panel)
            assert BLOCKERS_LABEL not in _briefing_text(panel)
            assert evidence.styles.display == "none"
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_simultaneous_blockers_share_one_blockers_label_and_one_evidence_fold(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    dependency = seed_task(connection, clock, title="Add database")
    blocked = seed_task(connection, clock, title="Add admin page", depends_on=(dependency.id,))
    store = TaskStore(connection, clock)
    store.set_wait_reason(blocked.id, WaitReason.DEPENDENCY)
    store.set_holds(blocked.id, frozenset({Hold.PAUSED}))
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        blocked.id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, blocked.id)
            await wait_until(
                lambda: "Paused by operator" in _briefing_text(panel), timeout=UI_WAIT_SECONDS
            )
            briefing = _briefing_text(panel)
            assert briefing.count(BLOCKERS_LABEL) == 1
            assert f"Waiting on dependency · {dependency.id}" in briefing
            evidence = plain_text(panel.query_one(f"#{TASK_DETAIL_EVIDENCE}", Static).content)
            assert "Recorded scheduler reason" in evidence
            assert dependency.id in evidence
            assert "Paused by operator" not in "\n".join(
                _painted_rows(panel, TASK_DETAIL_RELATED_LIST)
            )
            assert dependency.id in "\n".join(_painted_rows(panel, TASK_DETAIL_RELATED_LIST))
    finally:
        reader.close()


def _evidence_fold(panel: TaskDetailPanel) -> DetailFold:
    return next(
        fold for fold in panel.query(DetailFold) if fold.view_id == TASK_VIEW_BLOCKER_EVIDENCE
    )


def _blockers_value_x(panel: TaskDetailPanel) -> int | None:
    briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
    for cells in painted_lines(briefing):
        text = "".join(char for char, _ in cells)
        glyph = text.find(GLYPH_WARNING)
        if glyph >= 0:
            return briefing.region.x + cell_len(text[:glyph])
    return None


def _evidence_mark_x(evidence: DetailFold) -> int | None:
    for cells in painted_lines(evidence.header):
        text = "".join(char for char, _ in cells)
        if CLOSED_MARK in text:
            return evidence.header.region.x + text.index(CLOSED_MARK)
    return None


def _mark_sits_in_the_blockers_value_column(panel: TaskDetailPanel, evidence: DetailFold) -> bool:
    mark = _evidence_mark_x(evidence)
    return mark is not None and mark == _blockers_value_x(panel)


@pytest.mark.usefixtures("colour_terminal")
@pytest.mark.parametrize("width", [PANEL_WIDTH, COMPACT_PANEL_WIDTH, 32])
async def test_the_closed_evidence_fold_hangs_in_grey_from_the_blockers_value_column(
    connection: Connection,
    database: Database,
    tmp_path: Path,
    *,
    width: int,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed_blocked_task(connection, clock, _Blocker.DEPENDENCY)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task_id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(width, 40)) as pilot:
            await _loaded(panel, task_id)
            evidence = _evidence_fold(panel)
            await wait_until(
                lambda: _mark_sits_in_the_blockers_value_column(panel, evidence),
                timeout=UI_WAIT_SECONDS,
            )
            assert not evidence.is_open
            assert fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE) == "2 lines"
            assert set(styles(evidence.header, "Evidence")) == {GREY}
            assert set(foregrounds(evidence.header, "2 lines")) == {SECONDARY_TEXT_COLOR}
            if width == 32:
                briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
                stacked_value_x = _blockers_value_x(panel)
                assert stacked_value_x is not None
                stacked_value_indent = stacked_value_x - briefing.region.x
                assert stacked_value_indent == 0
                await pilot.resize_terminal(COMPACT_PANEL_WIDTH, 40)
                await wait_until(
                    lambda: _mark_sits_in_the_blockers_value_column(panel, evidence),
                    timeout=UI_WAIT_SECONDS,
                )
                briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
                column_value_x = _blockers_value_x(panel)
                assert column_value_x is not None
                column_value_indent = column_value_x - briefing.region.x
                assert column_value_indent > stacked_value_indent
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_a_pause_and_an_intervention_hold_share_one_blockers_label_and_one_evidence_fold(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Held task")
    runs = RunStore(connection, clock)
    run = runs.start(task.id, Role.LEADER, Rung("codex", "gpt-6-luna", "high"))
    runs.mark_taken_over(run.id)
    TaskStore(connection, clock).set_holds(task.id, frozenset({Hold.PAUSED, Hold.INTERVENTION}))
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task.id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task.id)
            await wait_until(
                lambda: fold_headline(panel, TASK_VIEW_BLOCKER_EVIDENCE) == "3 lines",
                timeout=UI_WAIT_SECONDS,
            )
            briefing = _briefing_text(panel)
            assert briefing.count(BLOCKERS_LABEL) == 1
            assert "Paused by operator" in briefing
            assert "Held by operator" in briefing
            evidence = plain_text(panel.query_one(f"#{TASK_DETAIL_EVIDENCE}", Static).content)
            assert "Recorded hold · paused" in evidence
            assert "Recorded hold · intervention" in evidence
            assert "Recorded takeover · leader" in evidence
    finally:
        reader.close()


SLOT_OCCUPANT_TITLES = ("abc", "数据库")


def _slot_occupant_rows(panel: TaskDetailPanel) -> list[str]:
    return [
        line
        for line in _body_lines(panel, TASK_DETAIL_BRIEFING)
        if "running" in line and any(title in line for title in SLOT_OCCUPANT_TITLES)
    ]


@pytest.mark.usefixtures("colour_terminal")
async def test_slot_occupant_status_column_aligns_by_terminal_cells_for_wide_titles(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    for slot, title in enumerate(SLOT_OCCUPANT_TITLES):
        occupant = seed_task(connection, clock, title=title, mode=Mode.IMPLEMENT)
        _start_on_slot(connection, clock, occupant.id, slot)
    waiter = seed_task(connection, clock, title="Slot waiter")
    TaskStore(connection, clock).set_wait_reason(waiter.id, WaitReason.SLOT)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        waiter.id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, waiter.id)
            await wait_until(
                lambda: len(_slot_occupant_rows(panel)) == len(SLOT_OCCUPANT_TITLES),
                timeout=UI_WAIT_SECONDS,
            )
            status_columns = {
                cell_len(row[: row.index("running")]) for row in _slot_occupant_rows(panel)
            }
            assert len(status_columns) == 1
    finally:
        reader.close()


async def _elapse(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
    clock.advance(seconds)
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)


@pytest.mark.usefixtures("colour_terminal")
async def test_a_quota_wait_reset_duration_counts_down_with_the_clock_and_no_database_write(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed_blocked_task(connection, clock, _Blocker.QUOTA)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task_id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task_id)
            await wait_until(
                lambda: QUOTA_REMAINING in _quota_headline(panel), timeout=UI_WAIT_SECONDS
            )
            await _elapse(clock, TIMELINE_REFRESH_SECONDS)
            await _elapse(clock, COUNTDOWN_SECONDS)
            await wait_until(
                lambda: QUOTA_REMAINING_LATER in _quota_headline(panel), timeout=UI_WAIT_SECONDS
            )
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_a_quota_countdown_tick_reads_only_the_live_work_and_repaints_only_the_blocker(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = _seed_blocked_task(connection, clock, _Blocker.QUOTA)
    reader = StateReader(database, SystemClock())
    context = widget_context(reader, project_root, clock)
    panel = TaskDetailPanel(context, task_id, fixed=True, initial_view=TASK_VIEW_RELATIONSHIPS)
    statements = _trace_statements(reader)
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task_id)
            await wait_until(
                lambda: QUOTA_REMAINING in _quota_headline(panel), timeout=UI_WAIT_SECONDS
            )
            settings = panel.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static)
            briefing = panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic)
            settings_shown = settings.content
            await _elapse(clock, ONE_MINUTE_SECONDS)
            await wait_until(
                lambda: QUOTA_REMAINING_79_MINUTES in _quota_headline(panel),
                timeout=UI_WAIT_SECONDS,
            )
            assert settings.content is settings_shown
            assert QUOTA_RESET in _briefing_text(panel)
            await _elapse(clock, TIMELINE_REFRESH_SECONDS)
            await wait_until(
                lambda: QUOTA_REMAINING_78_MINUTES in _quota_headline(panel),
                timeout=UI_WAIT_SECONDS,
            )
            briefing_shown = briefing.content
            statements.clear()
            await _elapse(clock, TIMELINE_REFRESH_SECONDS)
            tick_reads = _reads(statements)
            assert tick_reads == await _live_work_reads(context.views, task_id, statements)
            assert briefing.content is briefing_shown
            assert settings.content is settings_shown
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_a_quota_wait_crossing_utc_midnight_rewords_the_blocker_without_a_task_reload(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(MIDNIGHT_EVE)
    task_id, _ = _seed_blocked_task(connection, clock, _Blocker.QUOTA, until=MIDNIGHT_RESET)
    reader = StateReader(database, SystemClock())
    context = widget_context(reader, project_root, clock)
    panel = TaskDetailPanel(context, task_id, fixed=True, initial_view=TASK_VIEW_RELATIONSHIPS)
    statements = _trace_statements(reader)
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task_id)
            await wait_until(
                lambda: MIDNIGHT_RESET_DATED in _briefing_text(panel), timeout=UI_WAIT_SECONDS
            )
            settings = panel.query_one(f"#{TASK_DETAIL_CONTEXT_INFO}", Static)
            settings_shown = settings.content
            await _elapse(clock, ONE_MINUTE_SECONDS - TIMELINE_REFRESH_SECONDS)
            await wait_until(
                lambda: MIDNIGHT_REMAINING in _briefing_text(panel), timeout=UI_WAIT_SECONDS
            )
            assert MIDNIGHT_RESET_DATED in _briefing_text(panel)
            statements.clear()
            await _elapse(clock, TIMELINE_REFRESH_SECONDS)
            midnight_reads = _reads(statements)
            await wait_until(
                lambda: f"quota blocked until {MIDNIGHT_RESET_CLOCK}" in _briefing_text(panel),
                timeout=UI_WAIT_SECONDS,
            )
            assert MIDNIGHT_RESET_DATED not in _briefing_text(panel)
            assert midnight_reads == await _live_work_reads(context.views, task_id, statements)
            assert settings.content is settings_shown
    finally:
        reader.close()


@pytest.mark.usefixtures("colour_terminal")
async def test_a_quota_block_that_lifts_reloads_the_task_and_drops_the_quota_headline(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, cause = _seed_blocked_task(connection, clock, _Blocker.QUOTA)
    reader = StateReader(database, SystemClock())
    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock),
        task_id,
        fixed=True,
        initial_view=TASK_VIEW_RELATIONSHIPS,
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(PANEL_WIDTH, 40)):
            await _loaded(panel, task_id)
            await wait_until(lambda: cause in _briefing_text(panel), timeout=UI_WAIT_SECONDS)
            await _elapse(clock, (QUOTA_BLOCK_UNTIL - START).total_seconds())
            await wait_until(lambda: cause not in _briefing_text(panel), timeout=UI_WAIT_SECONDS)
            assert QUOTA_RESET not in _briefing_text(panel)
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
