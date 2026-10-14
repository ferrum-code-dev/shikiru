from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from textual.containers import Vertical
from textual.events import MouseScrollUp
from textual.widgets import Input, Select, Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import TaskTimelineCategory
from shikiru.dashboard.detail import (
    TASK_DETAIL_BODY,
    TASK_DETAIL_TIMELINE_LIST,
    TASK_DETAIL_TIMELINE_ROLE,
    TASK_DETAIL_TIMELINE_SELECTED,
    TASK_DETAIL_TIMELINE_TITLE,
    TASK_VIEW_EVIDENCE,
    TIMELINE_SECTIONS,
    TaskDetailBody,
    TaskDetailPanel,
)
from shikiru.dashboard.details_list_view import DetailsSection
from shikiru.dashboard.timeline_section import TimelineHost, TimelineSection
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    NewTask,
    Verdict,
)
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import Mode, Role, Status, StatusReason, Task
from shikiru.paths import run_dir, task_root
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import (
    StubServices,
    WidgetHostApp,
    choose_details_entry,
    list_rows,
    state_views,
    widget_context,
)
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import (
        DashboardViews,
        TaskDetailView,
        TaskTimelineEntryView,
        TaskTimelinePageView,
        WidgetContext,
    )
    from tests.support.store import Connection, Database

pytestmark = pytest.mark.asyncio

TIMELINE_START = datetime(2026, 1, 1, tzinfo=UTC)
TIMELINE_PAGE_LIMIT = 20
TIMELINE_ROW_COUNT = 24
LONG_FILTERED_SIGNAL_ROW_COUNT = 300
TIMELINE_TIED_EVENT_COUNT = 22
TIMELINE_ROLE = Role.IMPLEMENTER.value
TIMELINE_TURN = "turn-0"
TIMELINE_CATEGORY_ALL = "all"
TIMELINE_CATEGORY_TOOL = "tool"
TIMELINE_CATEGORY_EVIDENCE = "evidence"
TIMELINE_CATEGORY_TRANSITION = "transition"
FAILED_COMMAND_EXIT_CODE = 2


async def _open_timeline_view(panel: TaskDetailPanel, pilot: Pilot[None]) -> None:
    panel.query_one(f"#{TASK_DETAIL_BODY}", TaskDetailBody).focus()
    await choose_details_entry(pilot, TASK_VIEW_EVIDENCE)
    await wait_until(lambda: panel.active_view == TASK_VIEW_EVIDENCE, timeout=UI_WAIT_SECONDS)


async def _prepare_timeline_filter(panel: TaskDetailPanel, pilot: Pilot[None]) -> None:
    await _open_timeline_view(panel, pilot)
    await wait_until(lambda: bool(_rows(panel)), timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS)


TIMELINE_MISSING_SUMMARY = "Normalized signal evidence is unavailable"
TIMELINE_UI_REFRESH_TIMEOUT_SECONDS = 10
TIMELINE_CONCURRENT_LEADER_MESSAGE = "timeline-concurrent-leader"
TIMELINE_CONCURRENT_IMPLEMENTER_MESSAGE = "timeline-concurrent-implementer"


@dataclass
class _TimelineUITest:
    connection: Connection
    clock: FakeClock
    task: Task
    context: WidgetContext
    settings: Settings
    panel: TaskDetailPanel
    pilot: Pilot[None]


class _RecordedSignalRow(TypedDict):
    ts: str
    turn: str
    signal: str
    ordinal: int


class _GatedDashboardViews:
    def __init__(self, delegate: DashboardViews) -> None:
        self._delegate = delegate
        self._detail_gates: list[tuple[asyncio.Event, asyncio.Event]] = []
        self._timeline_gates: list[tuple[asyncio.Event, asyncio.Event]] = []
        self.detail_requests: list[tuple[str | None, str | None, str]] = []
        self.detail_pages: list[TaskDetailView | None] = []

    def gate_next_detail(self) -> tuple[asyncio.Event, asyncio.Event]:
        gate = (asyncio.Event(), asyncio.Event())
        self._detail_gates.append(gate)
        return gate

    def gate_next_timeline(self) -> tuple[asyncio.Event, asyncio.Event]:
        gate = (asyncio.Event(), asyncio.Event())
        self._timeline_gates.append(gate)
        return gate

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def task_detail(
        self,
        task_id: str,
        *,
        relationship_offset: int = 0,
        timeline_cursor: str | None = None,
        timeline_role: str | None = None,
        timeline_turn: str | None = None,
        timeline_category: str = TaskTimelineCategory.ACTIVITY.value,
        include_timeline: bool = True,
    ) -> TaskDetailView | None:
        self.detail_requests.append((timeline_role, timeline_turn, timeline_category))
        result = await self._delegate.task_detail(
            task_id,
            relationship_offset=relationship_offset,
            timeline_cursor=timeline_cursor,
            timeline_role=timeline_role,
            timeline_turn=timeline_turn,
            timeline_category=timeline_category,
            include_timeline=include_timeline,
        )
        self.detail_pages.append(result)
        await self._wait_at_gate(self._detail_gates)
        return result

    async def task_timeline(
        self,
        task_id: str,
        *,
        cursor: str | None,
        role: str | None,
        turn: str | None,
        category: str,
    ) -> TaskTimelinePageView | None:
        result = await self._delegate.task_timeline(
            task_id,
            cursor=cursor,
            role=role,
            turn=turn,
            category=category,
        )
        await self._wait_at_gate(self._timeline_gates)
        return result

    @staticmethod
    async def _wait_at_gate(gates: list[tuple[asyncio.Event, asyncio.Event]]) -> None:
        if not gates:
            return
        started, release = gates.pop(0)
        started.set()
        await release.wait()


async def test_timeline_pages_keep_bounded_rows_and_stable_source_order(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Timeline pagination")
    runs = RunStore(connection, clock)
    runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    events = EventLog(connection, clock)
    event_ids: list[int] = []
    turns = [f"turn-{index}" for index in range(TIMELINE_ROW_COUNT)]
    log_paths = {
        role: run_dir(project_root, task.id) / role.value / "view.jsonl"
        for role in (Role.LEADER, Role.IMPLEMENTER)
    }
    for path in log_paths.values():
        path.parent.mkdir(parents=True)
    with_paths: dict[Role, list[_RecordedSignalRow]] = {role: [] for role in log_paths}
    for index, turn in enumerate(turns):
        clock.advance(1)
        timestamp = clock.now().isoformat()
        event = events.append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"recorded recoverable failure {turn}",
            data={"role": TIMELINE_ROLE, "turn": turn},
        )
        event_ids.append(event.id)
        for role, signal in (
            (Role.LEADER, "Message"),
            (Role.IMPLEMENTER, "ToolFinished"),
        ):
            row: _RecordedSignalRow = {
                "ts": timestamp,
                "turn": turn,
                "signal": signal,
                "ordinal": index,
            }
            with_paths[role].append(row)
            with log_paths[role].open("ab") as output:
                output.write((json.dumps(row) + "\n").encode())

    views = state_views(reader, project_root, clock, StubServices())
    cursor: str | None = None
    entries: list[TaskTimelineEntryView] = []
    while True:
        page = await views.task_timeline(
            task.id,
            cursor=cursor,
            role=None,
            turn=None,
            category=TIMELINE_CATEGORY_ALL,
        )
        assert page is not None
        assert len(page.entries) <= TIMELINE_PAGE_LIMIT
        entries.extend(page.entries)
        if not page.has_older:
            break
        assert page.older_cursor is not None
        cursor = page.older_cursor

    identities = [entry.identity for entry in entries]
    assert len(identities) == len(set(identities))
    assert len(entries) == TIMELINE_ROW_COUNT * 3
    assert {entry.identity for entry in entries if entry.source == "engine"} == {
        f"engine:{event_id}" for event_id in event_ids
    }
    for role, rows in with_paths.items():
        observed = [
            entry.turn for entry in entries if entry.source == "agent" and entry.role == role.value
        ]
        assert observed == [row["turn"] for row in reversed(rows)]


async def test_timeline_filters_roles_turns_categories_and_missing_files(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Timeline filters")
    RunStore(connection, clock).start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signal_path = run_dir(project_root, task.id) / Role.IMPLEMENTER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    signals = (
        {
            "ts": clock.now().isoformat(),
            "turn": TIMELINE_TURN,
            "signal": "ToolFinished",
            "name": "shikiru report",
            "outcome": "failure",
            "exit_code": FAILED_COMMAND_EXIT_CODE,
        },
        {"ts": clock.now().isoformat(), "turn": "turn-other", "signal": "Message"},
        {"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "TurnFailed"},
    )
    signal_path.write_text("".join(json.dumps(row) + "\n" for row in signals))
    events = EventLog(connection, clock)
    event = events.append(
        EventKind.TURN_FAILED,
        task_id=task.id,
        message="turn error was recovered",
        data={"role": TIMELINE_ROLE, "turn": TIMELINE_TURN},
    )
    events.append(
        EventKind.TASK_DONE,
        task_id=task.id,
        message="task completed",
        data={"role": Role.LEADER.value, "turn": "turn-final"},
    )
    views = state_views(reader, project_root, clock, StubServices())

    tool_page = await views.task_timeline(
        task.id,
        cursor=None,
        role=TIMELINE_ROLE,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_TOOL,
    )
    transition_page = await views.task_timeline(
        task.id,
        cursor=None,
        role=TIMELINE_ROLE,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_TRANSITION,
    )
    missing_page = await views.task_timeline(
        task.id,
        cursor=None,
        role=Role.LEADER.value,
        turn=None,
        category=TIMELINE_CATEGORY_EVIDENCE,
    )

    assert tool_page is not None
    assert [entry.identity for entry in tool_page.entries] == ["signal:implementer:0"]
    assert all(entry.category == TIMELINE_CATEGORY_TOOL for entry in tool_page.entries)
    assert "failure" in tool_page.entries[0].summary
    assert f"exit {FAILED_COMMAND_EXIT_CODE}" in tool_page.entries[0].summary
    assert transition_page is not None
    assert {entry.identity for entry in transition_page.entries if entry.source == "engine"} == {
        f"engine:{event.id}"
    }
    assert any(
        entry.source == "agent" and entry.turn == TIMELINE_TURN for entry in transition_page.entries
    )
    assert missing_page is not None
    assert missing_page.entries[0].summary == TIMELINE_MISSING_SUMMARY
    assert missing_page.entries[0].timestamp is None


async def test_one_unreadable_signal_source_keeps_other_evidence_and_task_state(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Unreadable signal source")
    runs = RunStore(connection, clock)
    runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    leader_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    implementer_path = run_dir(project_root, task.id) / Role.IMPLEMENTER.value / "view.jsonl"
    leader_path.parent.mkdir(parents=True)
    leader_path.mkdir()
    implementer_path.parent.mkdir(parents=True)
    implementer_path.write_text(
        json.dumps({"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "Message"})
        + "\n"
    )
    event = EventLog(connection, clock).append(
        EventKind.TURN_FAILED,
        task_id=task.id,
        message="recorded engine failure",
        data={"role": Role.LEADER.value, "turn": TIMELINE_TURN},
    )
    views = state_views(reader, project_root, clock, StubServices())

    detail = await views.task_detail(task.id, timeline_category=TIMELINE_CATEGORY_ALL)

    assert detail is not None
    assert detail.status == Status.QUEUED.value
    assert detail.timeline is not None
    assert f"engine:{event.id}" in {entry.identity for entry in detail.timeline.entries}
    assert any(
        entry.role == Role.IMPLEMENTER.value and entry.turn == TIMELINE_TURN
        for entry in detail.timeline.entries
    )
    assert any(
        entry.role == Role.LEADER.value and "view.jsonl" in entry.detail
        for entry in detail.timeline.entries
    )


async def test_truncated_signal_cursor_is_visible_without_blocking_task_detail(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Truncated signal cursor")
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signal_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(
        json.dumps({"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "Message"})
        + "\n"
    )
    events = EventLog(connection, clock)
    for index in range(TIMELINE_ROW_COUNT + 1):
        clock.advance(1)
        events.append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"saved cursor event {index}",
            data={"role": Role.LEADER.value, "turn": f"turn-{index}"},
        )
    views = state_views(reader, project_root, clock, StubServices())
    first = await views.task_timeline(
        task.id,
        cursor=None,
        role=None,
        turn=None,
        category=TIMELINE_CATEGORY_ALL,
    )
    assert first is not None
    assert first.has_older
    assert first.older_cursor is not None
    signal_path.write_text("")

    detail = await views.task_detail(
        task.id,
        timeline_cursor=first.older_cursor,
        timeline_category=TIMELINE_CATEGORY_ALL,
    )

    assert detail is not None
    assert detail.status == Status.QUEUED.value
    assert detail.timeline is not None
    assert any(
        entry.role == Role.LEADER.value and "cursor" in entry.detail
        for entry in detail.timeline.entries
    )


async def test_duplicate_and_missing_timestamps_keep_deterministic_source_order(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Timeline timestamp ties")
    runs = RunStore(connection, clock)
    runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    events = EventLog(connection, clock)
    event_ids = [
        events.append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"tied event {index}",
            data={"role": Role.LEADER.value, "turn": f"turn-{index}"},
        ).id
        for index in range(TIMELINE_TIED_EVENT_COUNT)
    ]
    leader_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    implementer_path = run_dir(project_root, task.id) / Role.IMPLEMENTER.value / "view.jsonl"
    leader_path.parent.mkdir(parents=True)
    implementer_path.parent.mkdir(parents=True)
    leader_path.write_text(
        json.dumps({"ts": clock.now().isoformat(), "turn": "leader-old", "signal": "Message"})
        + "\n"
        + json.dumps({"turn": "leader-new-unknown", "signal": "Message"})
        + "\n"
    )
    implementer_path.write_text(
        json.dumps(
            {"ts": clock.now().isoformat(), "turn": "implementer-tied", "signal": "ToolFinished"}
        )
        + "\n"
    )
    views = state_views(reader, project_root, clock, StubServices())

    first = await views.task_timeline(
        task.id,
        cursor=None,
        role=None,
        turn=None,
        category=TIMELINE_CATEGORY_ALL,
    )
    assert first is not None
    assert first.has_older
    assert [entry.identity for entry in first.entries] == [
        f"engine:{event_id}" for event_id in reversed(event_ids[-TIMELINE_PAGE_LIMIT:])
    ]
    second = await views.task_timeline(
        task.id,
        cursor=first.older_cursor,
        role=None,
        turn=None,
        category=TIMELINE_CATEGORY_ALL,
    )

    assert second is not None
    assert [entry.identity for entry in second.entries[:2]] == [
        f"engine:{event_id}" for event_id in reversed(event_ids[:-TIMELINE_PAGE_LIMIT])
    ]
    assert [entry.turn for entry in second.entries[2:]] == [
        "implementer-tied",
        "leader-new-unknown",
        "leader-old",
    ]
    assert second.entries[3].timestamp is None
    assert not second.has_older


async def test_partial_live_row_keeps_identity_when_it_becomes_complete(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Partial timeline row")
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signal_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    raw = json.dumps(
        {"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "ToolStarted"}
    ).encode()
    signal_path.write_bytes(raw)
    views = state_views(reader, project_root, clock, StubServices())

    partial = await views.task_timeline(
        task.id,
        cursor=None,
        role=Role.LEADER.value,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_ALL,
    )
    with signal_path.open("ab") as output:
        output.write(b"\n")
    complete = await views.task_timeline(
        task.id,
        cursor=None,
        role=Role.LEADER.value,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_ALL,
    )

    assert partial is not None
    assert complete is not None
    assert partial.entries[0].identity == complete.entries[0].identity


async def test_long_filtered_signal_source_continues_from_a_bounded_scan(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Long filtered signal source")
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signal_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    rows = [
        {"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "ToolFinished"},
        *(
            {"ts": clock.now().isoformat(), "turn": "other-turn", "signal": "Message"}
            for _ in range(LONG_FILTERED_SIGNAL_ROW_COUNT)
        ),
    ]
    signal_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    views = state_views(reader, project_root, clock, StubServices())

    first = await views.task_timeline(
        task.id,
        cursor=None,
        role=Role.LEADER.value,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_TOOL,
    )
    assert first is not None
    assert first.entries == ()
    assert first.has_older
    assert first.older_cursor is not None
    second = await views.task_timeline(
        task.id,
        cursor=first.older_cursor,
        role=Role.LEADER.value,
        turn=TIMELINE_TURN,
        category=TIMELINE_CATEGORY_TOOL,
    )

    assert second is not None
    assert [entry.turn for entry in second.entries] == [TIMELINE_TURN]
    assert not second.has_older


async def test_terminal_detail_keeps_reports_and_facts_after_workspace_cleanup(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Completed history", mode=Mode.IMPLEMENT)
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    turn = "02-verify-i1"
    turn_dir = run_dir(project_root, task.id) / "turns" / turn
    turn_dir.mkdir(parents=True)
    verdict = Verdict(decision="accept", summary="Validated completion")
    (turn_dir / REPORT_KIND_FILENAME).write_text("verdict")
    (turn_dir / REPORT_FILENAME).write_text(verdict.model_dump_json())
    (turn_dir / FACTS_FILENAME).write_text("branch shikiru/T-1\nworking tree clean\n")
    EventLog(connection, clock).append(
        EventKind.TURN_FAILED,
        task_id=task.id,
        message="a recovered turn error",
        data={"role": Role.LEADER.value, "turn": turn, "result": "Failed"},
    )
    terminal = Transition.stay(task, step=Step.VERIFY, status=Status.DONE)
    TaskStore(connection, clock).finish(
        task.id,
        terminal,
        EventKind.TASK_DONE,
        message="task integrated successfully",
        data={"summary": "integrated summary", "turn": turn},
    )
    workspace = task_root(project_root, task.id)
    workspace.mkdir(parents=True)
    (workspace / "leftover").write_text("unneeded worktree payload")
    shutil.rmtree(workspace)

    views = state_views(reader, project_root, clock, StubServices())
    detail = await views.task_detail(task.id)

    assert detail is not None
    assert detail.status == Status.DONE.value
    assert detail.status_reason is None
    assert detail.latest_verdict is not None
    assert detail.latest_verdict.summary == "Validated completion"
    assert detail.recorded_facts == "branch shikiru/T-1\nworking tree clean\n"
    assert detail.facts_turn == turn
    timeline = await views.task_timeline(
        task.id,
        cursor=None,
        role=None,
        turn=None,
        category=TIMELINE_CATEGORY_ALL,
    )
    assert timeline is not None
    assert [entry.summary for entry in timeline.entries[:2]] == [
        "task integrated successfully",
        "a recovered turn error",
    ]
    assert detail.status == Status.DONE.value


async def test_cancelled_task_keeps_its_supersede_reason_after_cleanup(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Superseded task", mode=Mode.IMPLEMENT)
    turn_dir = run_dir(project_root, task.id) / "turns" / "02-verify-i1"
    turn_dir.mkdir(parents=True)
    verdict = Verdict(
        decision="supersede",
        summary="Replace this task",
        new_tasks=[NewTask(title="Replacement", request="Continue the work")],
    )
    (turn_dir / REPORT_KIND_FILENAME).write_text("verdict")
    (turn_dir / REPORT_FILENAME).write_text(verdict.model_dump_json())
    terminal = replace(
        Transition.stay(task, step=Step.VERIFY, status=Status.CANCELLED),
        status_reason=StatusReason.SUPERSEDED,
    )
    TaskStore(connection, clock).finish(
        task.id,
        terminal,
        EventKind.TASK_CANCELLED,
        message="task superseded",
        data={"reason": StatusReason.SUPERSEDED.value},
    )
    workspace = task_root(project_root, task.id)
    workspace.mkdir(parents=True)
    shutil.rmtree(workspace)

    detail = await state_views(reader, project_root, clock, StubServices()).task_detail(task.id)

    assert detail is not None
    assert detail.status == Status.CANCELLED.value
    assert detail.status_reason == StatusReason.SUPERSEDED.value
    assert detail.latest_verdict is not None
    assert detail.latest_verdict.summary == "Replace this task"


async def test_following_tracks_the_latest_entry_and_pauses_when_the_reader_moves_away(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Following timeline")
    for index in range(TIMELINE_ROW_COUNT):
        clock.advance(1)
        EventLog(connection, clock).append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"timeline-entry-{index}",
            data={"role": Role.LEADER.value, "turn": f"turn-{index}"},
        )
    reader = StateReader(database, clock)
    context = widget_context(reader, project_root, clock)
    panel = TaskDetailPanel(context, task.id, fixed=False)
    app = WidgetHostApp(panel)
    settings = load_settings(project_root)

    try:
        async with app.run_test(size=(100, 40)) as pilot:
            await _open_timeline_view(panel, pilot)
            case = _TimelineUITest(connection, clock, task, context, settings, panel, pilot)
            latest = f"timeline-entry-{TIMELINE_ROW_COUNT - 1}"
            await wait_until(
                lambda: bool(_rows(panel)) and latest in _rows(panel)[0],
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            assert "following" in _heading(panel)

            await _append_and_wake(case, "timeline-followed-new-entry", "new-turn")
            await wait_until(
                lambda: "timeline-followed-new-entry" in _rows(panel)[0],
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )

            await _assert_move_pauses_until_end(case, "timeline-after-move", "move-turn")
            await _assert_scroll_pauses_following(
                case, "timeline-after-keyboard-scroll", "keyboard-turn", mouse=False
            )
            await _assert_scroll_pauses_following(
                case, "timeline-after-mouse-scroll", "mouse-turn", mouse=True
            )
            await _assert_older_page_stays_paused(case)
    finally:
        reader.close()


async def test_in_flight_timeline_page_does_not_replace_changed_filter(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Concurrent timeline filter")
    runs = RunStore(connection, clock)
    _start_role_runs(runs, task.id)
    events = EventLog(connection, clock)
    _append_timeline_events(
        events,
        task.id,
        (
            ("timeline baseline leader", Role.LEADER.value, "leader-baseline"),
            ("timeline baseline implementer", Role.IMPLEMENTER.value, "implementer-baseline"),
        ),
    )
    reader = StateReader(database, clock)
    context = widget_context(reader, project_root, clock)
    gated_views = _GatedDashboardViews(context.views)
    context = replace(
        context,
        views=cast("DashboardViews", gated_views),
        on_change=_ignore_dashboard_changes,
    )
    panel = TaskDetailPanel(context, task.id, fixed=False)
    app = WidgetHostApp(panel)
    settings = load_settings(project_root)
    timeline_gate: tuple[asyncio.Event, asyncio.Event] | None = None
    detail_gate: tuple[asyncio.Event, asyncio.Event] | None = None

    try:
        async with app.run_test(size=(100, 40)) as pilot:
            await _prepare_timeline_filter(panel, pilot)
            _append_timeline_events(
                events,
                task.id,
                (
                    (TIMELINE_CONCURRENT_LEADER_MESSAGE, Role.LEADER.value, "leader-new"),
                    (
                        TIMELINE_CONCURRENT_IMPLEMENTER_MESSAGE,
                        Role.IMPLEMENTER.value,
                        "implementer-new",
                    ),
                ),
            )
            timeline_gate = gated_views.gate_next_timeline()
            await _wake_clock(clock, settings.dashboard.timeline_refresh_seconds)
            await wait_until(
                timeline_gate[0].is_set,
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )

            detail_gate = gated_views.gate_next_detail()
            panel.query_one(f"#{TASK_DETAIL_TIMELINE_ROLE}", Select).value = Role.IMPLEMENTER.value
            await pilot.pause()
            timeline_gate[1].set()
            await wait_until(
                detail_gate[0].is_set,
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            assert gated_views.detail_requests[-1][0] == Role.IMPLEMENTER.value
            page = gated_views.detail_pages[-1]
            assert page is not None
            assert page.timeline is not None
            _assert_role_filtered_entries(
                page.timeline.entries,
                Role.IMPLEMENTER.value,
                TIMELINE_CONCURRENT_LEADER_MESSAGE,
            )
            detail_gate[1].set()
            await wait_until(
                lambda: _has_row(panel, TIMELINE_CONCURRENT_IMPLEMENTER_MESSAGE),
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            assert not _has_row(panel, TIMELINE_CONCURRENT_LEADER_MESSAGE)
    finally:
        if timeline_gate is not None:
            timeline_gate[1].set()
        if detail_gate is not None:
            detail_gate[1].set()
        reader.close()


async def test_shown_timelines_keep_their_own_paging_filters_and_refresh(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Independent timelines")
    RunStore(connection, clock).start(task.id, Role.LEADER, LEADER_RUNG)
    signal_path = run_dir(project_root, task.id) / Role.LEADER.value / "view.jsonl"
    signal_path.parent.mkdir(parents=True)
    events = EventLog(connection, clock)

    def append_signal(turn: str) -> None:
        row = {"ts": clock.now().isoformat(), "turn": turn, "signal": "Message", "ordinal": 0}
        with signal_path.open("a") as output:
            output.write(json.dumps(row) + "\n")

    for index in range(TIMELINE_ROW_COUNT):
        clock.advance(1)
        events.append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"event-{index}",
            data={"role": Role.LEADER.value, "turn": f"event-turn-{index}"},
        )
        append_signal(f"signal-turn-{index % 4}")

    context = widget_context(reader, project_root, clock)
    refreshing: list[TimelineSection] = []

    def refresh_all() -> None:
        app.run_worker(_refresh_pages(refreshing))

    host = TimelineHost(
        task_id=lambda: task.id,
        lock=asyncio.Lock(),
        queue_refresh=refresh_all,
        report=_raise_report,
    )
    sections = {
        section: TimelineSection(context, host, ids=ids, section=section, category=category)
        for section, (ids, category) in TIMELINE_SECTIONS.items()
    }
    refreshing.extend(sections.values())
    activity = sections[DetailsSection.EVENTS]
    signals = sections[DetailsSection.AGENT_ACTIVITY]
    app = WidgetHostApp(Vertical(*sections.values()))

    async with app.run_test(size=(100, 60)) as pilot:
        refresh_all()
        await wait_until(
            lambda: activity.page is not None and signals.page is not None,
            timeout=UI_WAIT_SECONDS,
        )
        newest_events = _identities(activity)
        newest_signals = _identities(signals)
        assert newest_events != newest_signals

        app.query_one(f"#{activity.ids.list}").focus()
        await pilot.press("pagedown")
        await wait_until(lambda: _identities(activity) != newest_events, timeout=UI_WAIT_SECONDS)
        older_events = _identities(activity)
        assert (activity.following, signals.following) == (False, True)
        assert _identities(signals) == newest_signals

        append_signal("signal-turn-fresh")
        events.append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message="event-fresh",
            data={"role": Role.LEADER.value, "turn": "event-turn-fresh"},
        )
        await activity.refresh_when_due()
        await signals.refresh_when_due()
        assert _identities(activity) == older_events
        assert _turns(signals)[0] == "signal-turn-fresh"

        turn_input = app.query_one(f"#{signals.ids.turn}", Input)
        turn_input.focus()
        turn_input.value = "signal-turn-3"
        await pilot.press("enter")
        await wait_until(lambda: set(_turns(signals)) == {"signal-turn-3"}, timeout=UI_WAIT_SECONDS)
        assert _identities(activity) == older_events
        assert activity.request.turn is None
        assert app.query_one(f"#{activity.ids.turn}", Input).value == ""


async def _refresh_pages(sections: list[TimelineSection]) -> None:
    for section in sections:
        await section.refresh_page()


def _raise_report(message: str) -> None:
    raise AssertionError(message)


def _identities(section: TimelineSection) -> list[str]:
    return [entry.identity for entry in section.page.entries] if section.page else []


def _turns(section: TimelineSection) -> list[str | None]:
    return [entry.turn for entry in section.page.entries] if section.page else []


def _rows(panel: TaskDetailPanel) -> list[str]:
    return list_rows(panel, TASK_DETAIL_TIMELINE_LIST)


def _has_row(panel: TaskDetailPanel, content: str) -> bool:
    return any(content in row for row in _rows(panel))


def _heading(panel: TaskDetailPanel) -> str:
    return plain_text(panel.query_one(f"#{TASK_DETAIL_TIMELINE_TITLE}", Static).content)


def _selected_text(panel: TaskDetailPanel) -> str:
    return plain_text(panel.query_one(f"#{TASK_DETAIL_TIMELINE_SELECTED}", Static).content)


def _start_role_runs(runs: RunStore, task_id: str) -> None:
    runs.start(task_id, Role.LEADER, LEADER_RUNG)
    runs.start(task_id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])


def _append_timeline_events(
    events: EventLog, task_id: str, rows: tuple[tuple[str, str, str], ...]
) -> None:
    for message, role, turn in rows:
        events.append(
            EventKind.TURN_FAILED,
            task_id=task_id,
            message=message,
            data={"role": role, "turn": turn},
        )


def _assert_role_filtered_entries(
    entries: tuple[TaskTimelineEntryView, ...], role: str, excluded_summary: str
) -> None:
    assert not any(entry.summary == excluded_summary for entry in entries)
    assert all(entry.role == role for entry in entries)


async def _append_and_wake(case: _TimelineUITest, message: str, turn: str) -> None:
    EventLog(case.connection, case.clock).append(
        EventKind.TURN_FAILED,
        task_id=case.task.id,
        message=message,
        data={"role": Role.LEADER.value, "turn": turn},
    )
    await _wake_clock(case.clock, case.settings.dashboard.timeline_refresh_seconds)


async def _focus_list(case: _TimelineUITest) -> None:
    case.panel.query_one(f"#{TASK_DETAIL_TIMELINE_LIST}").focus()
    await case.pilot.pause()


async def _assert_paused_then_end_follows(case: _TimelineUITest, message: str, turn: str) -> None:
    await wait_until(
        lambda: "paused" in _heading(case.panel), timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS
    )
    selected = _selected_text(case.panel)
    await _append_and_wake(case, message, turn)
    assert not _has_row(case.panel, message)
    assert _selected_text(case.panel) == selected
    await _focus_list(case)
    await case.pilot.press("end")
    await wait_until(
        lambda: _has_row(case.panel, message) and "following" in _heading(case.panel),
        timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
    )
    assert message in _rows(case.panel)[0]


async def _assert_move_pauses_until_end(case: _TimelineUITest, message: str, turn: str) -> None:
    await _focus_list(case)
    await case.pilot.press("down")
    await _assert_paused_then_end_follows(case, message, turn)


async def _assert_scroll_pauses_following(
    case: _TimelineUITest, message: str, turn: str, *, mouse: bool
) -> None:
    body = case.panel.query_one("#task-detail-body", TaskDetailBody)
    body.scroll_end(animate=False, force=True)
    if mouse:
        mouse_event = MouseScrollUp(
            body,
            body.region.x + 1,
            body.region.y + 1,
            0,
            0,
            0,
            False,
            False,
            False,
        )
        case.pilot.app.post_message(mouse_event)
    else:
        body.focus()
        await case.pilot.pause()
        await case.pilot.press("up")
    await _assert_paused_then_end_follows(case, message, turn)


async def _assert_older_page_stays_paused(case: _TimelineUITest) -> None:
    await _focus_list(case)
    await case.pilot.press("pagedown")
    await wait_until(
        lambda: "paused" in _heading(case.panel) and not _has_row(case.panel, "timeline-followed"),
        timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
    )
    await _append_and_wake(case, "timeline-after-scroll-away", "newer-turn")
    assert not _has_row(case.panel, "timeline-after-scroll-away")
    await case.pilot.press("pageup")
    await wait_until(
        lambda: (
            "following" in _heading(case.panel)
            and _has_row(case.panel, "timeline-after-scroll-away")
        ),
        timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
    )


async def _wake_clock(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS)
    clock.advance(seconds)


async def _ignore_dashboard_changes(callback: object) -> None:
    del callback
