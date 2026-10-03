from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from textual.widgets import OptionList, Select, Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.detail import (
    TASK_DETAIL_TIMELINE_ENTRY,
    TASK_DETAIL_TIMELINE_FOLLOW,
    TASK_DETAIL_TIMELINE_OLDER,
    TASK_DETAIL_TIMELINE_SELECTED,
    TASK_DETAIL_TIMELINE_STATUS,
    TaskDetailPanel,
)
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    NewTask,
    Verdict,
)
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import Mode, Role, Status, StatusReason
from shikiru.paths import run_dir, task_root
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, state_views, widget_context
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from rich.text import Text

    from shikiru.dashboard.contract import TaskTimelineEntryView
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
TIMELINE_MISSING_SUMMARY = "Normalized signal evidence is unavailable"
TIMELINE_UI_REFRESH_TIMEOUT_SECONDS = 10


class _RecordedSignalRow(TypedDict):
    ts: str
    turn: str
    signal: str
    ordinal: int


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
    assert {entry.event_id for entry in entries if entry.event_id is not None} == set(event_ids)
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
        {"ts": clock.now().isoformat(), "turn": TIMELINE_TURN, "signal": "ToolFinished"},
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
    assert transition_page is not None
    assert {entry.identity for entry in transition_page.entries if entry.event_id is not None} == {
        f"engine:{event.id}"
    }
    assert any(
        entry.source == "agent" and entry.turn == TIMELINE_TURN for entry in transition_page.entries
    )
    assert missing_page is not None
    assert missing_page.entries[0].summary == TIMELINE_MISSING_SUMMARY
    assert missing_page.entries[0].timestamp is None


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
    assert [entry.event_id for entry in first.entries] == list(
        reversed(event_ids[-TIMELINE_PAGE_LIMIT:])
    )
    second = await views.task_timeline(
        task.id,
        cursor=first.older_cursor,
        role=None,
        turn=None,
        category=TIMELINE_CATEGORY_ALL,
    )

    assert second is not None
    assert [entry.event_id for entry in second.entries[:2]] == list(
        reversed(event_ids[:-TIMELINE_PAGE_LIMIT])
    )
    assert [entry.turn for entry in second.entries[2:]] == [
        "implementer-tied",
        "leader-new-unknown",
        "leader-old",
    ]
    assert second.entries[3].timestamp is None
    assert second.entries[3].timestamp_error is not None
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
    assert partial.entries[0].incomplete
    assert not complete.entries[0].incomplete


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


async def test_detail_preserves_selection_while_following_and_pauses_on_older_page(
    connection: Connection,
    database: Database,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(TIMELINE_START)
    task = seed_task(connection, clock, title="Following timeline")
    events: list[int] = []
    for index in range(TIMELINE_ROW_COUNT):
        clock.advance(1)
        event = EventLog(connection, clock).append(
            EventKind.TURN_FAILED,
            task_id=task.id,
            message=f"timeline-entry-{index}",
            data={"role": Role.LEADER.value, "turn": f"turn-{index}"},
        )
        events.append(event.id)
    reader = StateReader(database, clock)
    context = widget_context(reader, project_root, clock)
    panel = TaskDetailPanel(context, task.id, fixed=False)
    app = WidgetHostApp(panel)
    settings = load_settings(project_root)
    selected_identity = f"engine:{events[-2]}"

    try:
        async with app.run_test(size=(100, 40)) as pilot:
            entry_select = cast(
                "Select[str]", panel.query_one(f"#{TASK_DETAIL_TIMELINE_ENTRY}", Select)
            )
            await wait_until(
                lambda: entry_select.value == f"engine:{events[-1]}",
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            entry_select.value = selected_identity
            await wait_until(
                lambda: (
                    f"timeline-entry-{TIMELINE_ROW_COUNT - 2}"
                    in _static_text(panel, TASK_DETAIL_TIMELINE_SELECTED)
                ),
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            await _wake_clock(clock, settings.dashboard.timeline_refresh_seconds)
            await _wake_clock(clock, settings.dashboard.timeline_refresh_seconds)

            EventLog(connection, clock).append(
                EventKind.TURN_FAILED,
                task_id=task.id,
                message="timeline-followed-new-entry",
                data={"role": Role.LEADER.value, "turn": "new-turn"},
            )
            await _wake_clock(clock, settings.dashboard.timeline_refresh_seconds)
            await wait_until(
                lambda: _has_option(
                    entry_select.query_one(OptionList), "timeline-followed-new-entry"
                ),
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            assert entry_select.value == selected_identity

            await pilot.click(f"#{TASK_DETAIL_TIMELINE_OLDER}")
            await wait_until(
                lambda: (
                    "Follow paused while viewing older evidence"
                    in _static_text(panel, TASK_DETAIL_TIMELINE_STATUS)
                ),
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            older_options = _option_prompts(entry_select.query_one(OptionList))
            assert not any("timeline-followed-new-entry" in option for option in older_options)
            assert entry_select.value == selected_identity

            EventLog(connection, clock).append(
                EventKind.TURN_FAILED,
                task_id=task.id,
                message="timeline-after-scroll-away",
                data={"role": Role.LEADER.value, "turn": "newer-turn"},
            )
            latest = await context.views.task_timeline(
                task.id,
                cursor=None,
                role=None,
                turn=None,
                category=TIMELINE_CATEGORY_ALL,
            )
            assert latest is not None
            assert any(entry.summary == "timeline-after-scroll-away" for entry in latest.entries)
            await _wake_clock(clock, settings.dashboard.timeline_refresh_seconds)
            assert not _has_option(entry_select.query_one(OptionList), "timeline-after-scroll-away")
            assert entry_select.value == selected_identity

            await pilot.click(f"#{TASK_DETAIL_TIMELINE_FOLLOW}")
            await wait_until(
                lambda: (
                    "Following latest" in _static_text(panel, TASK_DETAIL_TIMELINE_STATUS)
                    and _has_option(
                        entry_select.query_one(OptionList), "timeline-after-scroll-away"
                    )
                ),
                timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS,
            )
            assert entry_select.value == selected_identity
    finally:
        reader.close()


def _static_text(panel: TaskDetailPanel, element_id: str) -> str:
    return cast("Text", panel.query_one(f"#{element_id}", Static).render()).plain


def _option_prompts(options: OptionList) -> tuple[str, ...]:
    return tuple(str(option.prompt) for option in options.options)


def _has_option(options: OptionList, content: str) -> bool:
    return any(content in prompt for prompt in _option_prompts(options))


async def _wake_clock(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=TIMELINE_UI_REFRESH_TIMEOUT_SECONDS)
    clock.advance(seconds)
