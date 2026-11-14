from __future__ import annotations

import json
import shutil
import threading
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, Select, Static
from textual.widgets._select import SelectOverlay

from shikiru.agents.contracts import (
    BILLING_MODE_SUBSCRIPTION,
    BILLING_SOURCE_OPERATOR_CONFIG,
    COST_BASIS_UNKNOWN,
    BillingDeclaration,
    Usage,
)
from shikiru.dashboard.contract import PaletteRequest
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    Commit,
    ImplementationReport,
    ReportKind,
    TriageReport,
    Verdict,
)
from shikiru.domain.reports import (
    TestRun as ReportTestRun,
)
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import Mode, Role, Status, Task
from shikiru.domain.token_usage import PROCESSED_TOKENS_NOTE
from shikiru.operator.commands import OperatorCommands
from shikiru.operator.evidence_export import (
    EXPORT_FORMAT_JSON,
    EXPORT_FORMAT_MARKDOWN,
    TaskEvidenceExporter,
)
from shikiru.operator.evidence_writer import LocalEvidenceWriter
from shikiru.operator.queries import OperatorQueries
from shikiru.store.commands import CommandQueue
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.workspace.reports import ENGINE_VERDICT_FILENAME
from tests.support.actions import ScreenHost, popup_screen
from tests.support.cells import rendered_select_label
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import focus, is_clickable
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG, LEADER_RUNG_2
from tests.support.store import seed_task
from tests.support.waiting import ACTION_CONFIRMATION_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.geometry import Region
    from textual.pilot import Pilot

    from shikiru.dashboard.palette import PaletteScreen
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

pytestmark = pytest.mark.asyncio

EXPORT_START = datetime(2026, 1, 1, tzinfo=UTC)
DEPENDENCY_COUNT = 27
TIMELINE_EVENT_COUNT = 55
TIMELINE_TURN = "turn-3-verify"
HOSTILE_CONTENT = "## forged heading\n`````json\n[link](https://example.invalid)\n`````"
SIGNAL_CONTENT = "normalized signal tail secret"
PARTIAL_SIGNAL_CONTENT = "partial signal tail with ```"
EXPORT_WAIT_SECONDS = 30
EXPORT_ATTEMPTS = (
    ((100, 40), None, None),
    ((60, 24), "task-evidence-60-24.json", EXPORT_FORMAT_JSON),
    ((48, 20), "task-evidence-48-20.json", EXPORT_FORMAT_JSON),
    ((40, 14), "task-evidence-40-14.json", EXPORT_FORMAT_JSON),
    ((40, 8), "task-evidence-40-8.json", EXPORT_FORMAT_JSON),
)
FAILED_EXPORT_PATH = "missing-parent/task-evidence.json"
EXISTING_CONTENT = "original evidence\n"
INVALID_UTF8_CONTENT = "valid prefix\n\ud800"
READ_ONLY_MODE = 0o555
WRITABLE_MODE = 0o755
PANE_SELECTION_ERROR = "task evidence export must not select a tmux pane"


def _queries(
    reader: StateReader,
    project_root: Path,
    clock: FakeClock,
) -> OperatorQueries:
    return OperatorQueries(
        reader,
        project="export-test",
        project_root=project_root,
        engine_up=lambda: False,
        max_parallel=2,
        clock=clock,
    )


def _snapshot(connection: Connection, reader: StateReader, task_id: str) -> tuple[object, ...]:
    task = reader.get_task(task_id)
    events = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, ts, task_id, kind, message, data FROM events WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    )
    commands = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, ts, kind, payload, status, result FROM commands ORDER BY id"
        )
    )
    return task, events, commands


def _write_turn(
    project_root: Path,
    task_id: str,
    turn: str,
    kind: ReportKind,
    report: TriageReport | ImplementationReport | Verdict,
    *,
    facts: str,
    engine_verdict: Verdict | None = None,
) -> None:
    turn_dir = project_root / ".shikiru" / "runs" / task_id / "turns" / turn
    turn_dir.mkdir(parents=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text(kind.value)
    (turn_dir / REPORT_FILENAME).write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    )
    (turn_dir / FACTS_FILENAME).write_text(facts)
    if engine_verdict is not None:
        (turn_dir / ENGINE_VERDICT_FILENAME).write_text(
            json.dumps(engine_verdict.model_dump(mode="json"), ensure_ascii=False)
        )


def _seed_export_task(
    connection: Connection,
    project_root: Path,
    clock: FakeClock,
) -> tuple[Task, tuple[int, ...]]:
    dependencies = [
        seed_task(connection, clock, title=f"Dependency {index}")
        for index in range(DEPENDENCY_COUNT)
    ]
    task = seed_task(
        connection,
        clock,
        title="Evidence target",
        request=f"Keep this request verbatim:\n{HOSTILE_CONTENT}",
        depends_on=tuple(item.id for item in dependencies),
        leader=LEADER_RUNG_2,
        implementer=IMPLEMENTER_RUNGS[1],
    )
    runs = RunStore(connection, clock)
    measured_zero = runs.start(
        task.id,
        Role.LEADER,
        LEADER_RUNG,
        billing_declaration=BillingDeclaration(
            BILLING_MODE_SUBSCRIPTION,
            BILLING_SOURCE_OPERATOR_CONFIG,
        ),
    )
    runs.set_session(measured_zero.id, "leader-session", "resume leader-session")
    runs.record_usage(
        measured_zero.id,
        Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, None),
    )
    legacy_unknown = runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    connection.execute(
        "UPDATE runs SET usage_reported = NULL, uncached_input_tokens = NULL, "
        "output_tokens = NULL, cache_read_tokens = NULL, cache_write_tokens = NULL, "
        "cost_usd = NULL, cost_basis = ? WHERE id = ?",
        (COST_BASIS_UNKNOWN, legacy_unknown.id),
    )
    legacy_billed = runs.start(task.id, Role.LEADER, LEADER_RUNG_2)
    connection.execute(
        "UPDATE runs SET usage_reported = NULL, uncached_input_tokens = 11, output_tokens = 2, "
        "cache_read_tokens = 3, cache_write_tokens = 4, cost_usd = 0, "
        "cost_basis = 'billed' WHERE id = ?",
        (legacy_billed.id,),
    )
    api_run = runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[1])
    runs.set_session(api_run.id, "implementer-session", "resume implementer-session")
    runs.record_usage(
        api_run.id,
        Usage(4, 5, 6, 7, 0.25, "api_equivalent", 40),
    )
    task = TaskStore(connection, clock).require(task.id)
    transition = replace(
        Transition.stay(task, step=Step.INTEGRATE, status=Status.DONE),
        rung_index={Role.LEADER: 2, Role.IMPLEMENTER: 1},
    )
    TaskStore(connection, clock).apply_transition(task.id, transition)
    reports = (
        (
            "1-triage",
            ReportKind.TRIAGE,
            TriageReport(
                title=task.title,
                mode=Mode.IMPLEMENT,
                complexity="normal",
                acceptance_criteria=["Preserve recorded evidence."],
                references=[],
            ),
            "triage facts",
            None,
        ),
        (
            "2-implement-i1",
            ReportKind.IMPLEMENTATION,
            ImplementationReport(
                status="completed",
                summary=f"Implementation summary\n{HOSTILE_CONTENT}",
                commits=[Commit(sha="abc123", subject="Recorded commit")],
                tests=[ReportTestRun(name="check", result="passed")],
                trackers_updated=True,
            ),
            f"Implementation facts\n{HOSTILE_CONTENT}",
            None,
        ),
        (
            "3-verify-i1",
            ReportKind.VERDICT,
            Verdict(
                decision="accept",
                summary="Leader's recorded verdict.",
                commit_subject="feat: change",
            ),
            "verification facts",
            Verdict(
                decision="accept",
                summary=f"Engine override\n{HOSTILE_CONTENT}",
                commit_subject="feat: change",
            ),
        ),
    )
    for turn, kind, report, facts, engine_verdict in reports:
        _write_turn(
            project_root,
            task.id,
            turn,
            kind,
            report,
            facts=facts,
            engine_verdict=engine_verdict,
        )
    signal_root = project_root / ".shikiru" / "runs" / task.id
    leader_signals = signal_root / Role.LEADER.value / "view.jsonl"
    implementer_signals = signal_root / Role.IMPLEMENTER.value / "view.jsonl"
    leader_signals.parent.mkdir(parents=True)
    implementer_signals.parent.mkdir(parents=True)
    clock.advance(1)
    leader_signals.write_text(
        json.dumps(
            {
                "ts": clock.now().isoformat(),
                "turn": TIMELINE_TURN,
                "signal": "Message",
                "text": SIGNAL_CONTENT,
            }
        )
        + "\n"
    )
    implementer_signals.write_text(
        json.dumps(
            {
                "ts": clock.now().isoformat(),
                "turn": TIMELINE_TURN,
                "signal": "Message",
                "text": PARTIAL_SIGNAL_CONTENT,
            }
        )
    )
    event_log = EventLog(connection, clock)
    event_ids: list[int] = []
    for index in range(TIMELINE_EVENT_COUNT):
        clock.advance(1)
        event = event_log.append(
            EventKind.OPERATOR_MESSAGE,
            task_id=task.id,
            message=f"Recorded engine evidence {index}",
            data={"role": Role.LEADER.value, "turn": TIMELINE_TURN},
        )
        event_ids.append(event.id)
    return TaskStore(connection, clock).require(task.id), tuple(event_ids)


def _assert_complete_exported_document(
    document: dict[str, Any],
    task: Task,
    event_ids: tuple[int, ...],
    content: str,
) -> None:
    assert document["format"] == "shikiru.task-evidence"
    assert document["version"] == 3
    assert document["task_id"] == task.id
    assert datetime.fromisoformat(document["exported_at"]).utcoffset() is not None
    assert document["request"] == f"Keep this request verbatim:\n{HOSTILE_CONTENT}"
    assert document["outcome"]["status"] == Status.DONE.value
    assert {item["id"] for item in document["relationships"]["dependencies"]} == set(
        task.depends_on
    )
    assert document["task"]["depends_on"] == task.depends_on
    assert document["metadata"]["relationship_pages_exhausted"] is True
    assert document["metadata"]["timeline_pages_exhausted"] is True
    assert document["metadata"]["timeline_order"] == "newest_first"
    assert document["metadata"]["normalized_signals_included"] is False
    assert len(document["timeline"]) == TIMELINE_EVENT_COUNT
    assert tuple(item["event_id"] for item in document["timeline"]) == tuple(reversed(event_ids))
    assert all(item["source"] == "engine" for item in document["timeline"])
    assert SIGNAL_CONTENT not in content
    assert PARTIAL_SIGNAL_CONTENT not in content
    assert [report["turn"] for report in document["reports"]] == [
        "1-triage",
        "2-implement-i1",
        "3-verify-i1",
        "3-verify-i1",
    ]
    assert [item["turn"] for item in document["facts"]] == [
        "1-triage",
        "2-implement-i1",
        "3-verify-i1",
    ]
    timestamps = [document["exported_at"], document["outcome"]["created_at"]]
    timestamps.extend(item["timestamp"] for item in document["timeline"])
    timestamps.extend(item["started_at"] for item in document["sessions"])
    assert all(datetime.fromisoformat(value).utcoffset() is not None for value in timestamps)


def _assert_usage_provenance(document: dict[str, Any]) -> None:
    sessions = {item["session_id"]: item for item in document["sessions"]}
    assert sessions["leader-session"]["tokens"]["uncached_input"] == 0
    assert sessions["leader-session"]["usage_provenance"]["tokens"] == "reported"
    assert sessions["leader-session"]["billing"] == {
        "mode": BILLING_MODE_SUBSCRIPTION,
        "source": BILLING_SOURCE_OPERATOR_CONFIG,
    }
    assert sessions["leader-session"]["cost"]["usd"] is None
    assert sessions["leader-session"]["cost"]["basis"] == COST_BASIS_UNKNOWN
    legacy_unknown = next(
        item
        for item in document["sessions"]
        if item["usage_provenance"]["reported_marker"] is None
        and item["cost"]["basis"] == COST_BASIS_UNKNOWN
    )
    assert legacy_unknown["tokens"]["uncached_input"] is None
    assert legacy_unknown["cost"]["usd"] is None
    legacy_billed = next(item for item in document["sessions"] if item["cost"]["basis"] == "billed")
    assert legacy_billed["cost"]["usd"] == 0
    assert legacy_billed["usage_provenance"]["cost"] == "legacy_recorded"
    usage = {item["agent"]: item for item in document["usage"]}
    assert usage["codex"]["billed_cost_usd"] == 0
    assert usage["codex"]["api_equivalent_cost_usd"] == 0.25
    assert usage["claude"]["unknown_cost_runs"] == 1


def _markdown_json_blocks(content: str) -> tuple[list[object], str, list[int]]:
    blocks: list[object] = []
    outside: list[str] = []
    block_lines: list[str] = []
    opening_fence: str | None = None
    fence_lengths: list[int] = []
    for line in content.splitlines():
        if opening_fence is None:
            if line.startswith("`") and line.endswith("json"):
                opening_fence = line.removesuffix("json")
                fence_lengths.append(len(opening_fence))
                block_lines = []
            else:
                outside.append(line)
        elif line == opening_fence:
            blocks.append(json.loads("\n".join(block_lines)))
            opening_fence = None
        else:
            block_lines.append(line)
    assert opening_fence is None
    return blocks, "\n".join(outside), fence_lengths


def _form_viewport(screen: PaletteScreen) -> Region:
    form = screen.query_one("#palette-form", VerticalScroll)
    frame = screen.query_one("#palette-frame")
    return form.content_region.intersection(frame.content_region)


def _contains_nonempty_region(viewport: Region, region: Region) -> bool:
    return region.width > 0 and region.height > 0 and viewport.contains_region(region)


def _intersects_nonempty_region(viewport: Region, region: Region) -> bool:
    visible = viewport.intersection(region)
    return visible.width > 0 and visible.height > 0


async def _choose_export_format(
    pilot: Pilot[None],
    field: Select[str],
    value: str,
    *,
    mouse: bool,
) -> None:
    if field.value != value:
        if mouse:
            await pilot.click(field)
            await wait_until(lambda: field.expanded, timeout=EXPORT_WAIT_SECONDS)
            overlay = field.query_one(SelectOverlay)
            option_index = 0 if value == EXPORT_FORMAT_MARKDOWN else 1
            await pilot.click(overlay, offset=(2, option_index + 1))
        else:
            await focus(pilot, field)
            await pilot.press("enter")
            await wait_until(lambda: field.expanded, timeout=EXPORT_WAIT_SECONDS)
            direction = "down" if value == EXPORT_FORMAT_JSON else "up"
            await pilot.press(direction, "enter")
        await wait_until(
            lambda: field.value == value and not field.expanded,
            timeout=EXPORT_WAIT_SECONDS,
        )


async def _reach_export_controls(
    screen: PaletteScreen,
    pilot: Pilot[None],
    destination: str | None,
    export_format: str | None,
    *,
    mouse: bool,
) -> None:
    destination_field = screen.query_one("#export-destination", Input)
    format_field = cast("Select[str]", screen.query_one("#export-format", Select))
    if destination is not None:
        destination_field.value = destination
    if export_format is not None:
        await _choose_export_format(pilot, format_field, export_format, mouse=mouse)
    controls: tuple[tuple[Input | Select[str], str | None], ...] = (
        (destination_field, None),
        (format_field, "JSON" if format_field.value == EXPORT_FORMAT_JSON else "Markdown"),
        (cast("Select[str]", screen.query_one("#export-overwrite", Select)), "Refuse"),
        (cast("Select[str]", screen.query_one("#export-signals", Select)), "Exclude"),
    )
    for control, label in controls:
        await focus(pilot, control)
        await wait_until(
            lambda control=control: _contains_nonempty_region(
                _form_viewport(screen), control.region
            ),
            timeout=EXPORT_WAIT_SECONDS,
        )
        if label is not None:
            select = cast("Select[str]", control)
            await wait_until(
                lambda select=select, label=label: rendered_select_label(select) == label,
                timeout=EXPORT_WAIT_SECONDS,
            )
    destination_field.focus()
    await wait_until(
        lambda: (
            destination_field.has_focus
            and _contains_nonempty_region(_form_viewport(screen), destination_field.region)
        ),
        timeout=EXPORT_WAIT_SECONDS,
    )


@pytest.mark.parametrize("write_succeeds", [True, False])
async def test_palette_export_controls_and_full_result_remain_reachable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    write_succeeds: bool,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task = seed_task(connection, clock, title="Compact export target")
    queries = _queries(reader, project_root, clock)
    queue = CommandQueue(connection, clock)
    commands = OperatorCommands(queue, clock, engine_up=lambda: False)
    writer = LocalEvidenceWriter(project_root)
    exporter = TaskEvidenceExporter(queries, writer, clock)
    app = ScreenHost(
        popup_screen(
            commands,
            queries,
            reader,
            _selection_must_not_run,
            PaletteRequest(action="export", task_id=task.id),
            action_confirmation_seconds=ACTION_CONFIRMATION_SECONDS,
            evidence_exporter=exporter,
        )
    )
    before = _snapshot(connection, reader, task.id)
    default_destination = f"{task.id}-evidence.md"

    async with app.run_test(size=EXPORT_ATTEMPTS[0][0]) as pilot:
        screen = cast("PaletteScreen", app.screen)
        for index, (size, filename, export_format) in enumerate(EXPORT_ATTEMPTS):
            if index:
                await pilot.resize_terminal(*size)
                await wait_until(
                    lambda size=size: (
                        screen.size.width == size[0] and screen.size.height == size[1]
                    ),
                    timeout=EXPORT_WAIT_SECONDS,
                )
            base_filename = default_destination if filename is None else filename
            if write_succeeds:
                if filename is None:
                    destination = default_destination
                else:
                    destination = f"exports/{filename}"
                    (project_root / "exports").mkdir(exist_ok=True)
            else:
                destination = f"existing/{base_filename}"
                existing_file = project_root / destination
                existing_file.parent.mkdir(parents=True, exist_ok=True)
                existing_file.write_text(EXISTING_CONTENT)
            await _reach_export_controls(
                screen,
                pilot,
                None if write_succeeds and filename is None else destination,
                export_format,
                mouse=index == 2,
            )
            result = screen.query_one("#palette-result", Static)
            previous_result = str(result.content)
            if write_succeeds and index == 0:
                await pilot.press("enter")
            else:
                submit = screen.query_one("#palette-submit")
                await wait_until(
                    partial(is_clickable, pilot.app, submit),
                    timeout=EXPORT_WAIT_SECONDS,
                )
                await pilot.click("#palette-submit")
            await wait_until(
                lambda result=result, previous=previous_result: (
                    str(result.content) != previous
                    and bool(str(result.content).strip())
                    and _intersects_nonempty_region(_form_viewport(screen), result.region)
                ),
                timeout=EXPORT_WAIT_SECONDS,
            )
            output_path = project_root / destination
            if write_succeeds:
                assert output_path.is_file()
                content = output_path.read_text()
                if export_format in {None, EXPORT_FORMAT_MARKDOWN}:
                    blocks, _, _ = _markdown_json_blocks(content)
                    assert any(
                        isinstance(block, dict)
                        and cast("dict[str, Any]", block)["task_id"] == task.id
                        for block in blocks
                    )
                else:
                    assert json.loads(content)["task_id"] == task.id
            else:
                assert output_path.read_text() == EXISTING_CONTENT

    assert _snapshot(connection, reader, task.id) == before


async def test_export_is_complete_read_only_and_preserves_usage_provenance(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task, event_ids = _seed_export_task(connection, project_root, clock)
    queries = _queries(reader, project_root, clock)
    writer = LocalEvidenceWriter(project_root)
    exporter = TaskEvidenceExporter(queries, writer, clock)
    worktree = project_root / ".shikiru" / "worktrees" / task.id
    worktree.mkdir(parents=True)
    (worktree / "untracked.txt").write_text("discarded during worktree cleanup")
    shutil.rmtree(worktree)
    assert not worktree.exists()
    before = _snapshot(connection, reader, task.id)

    result = await exporter.export(
        task.id,
        "complete.json",
        export_format=EXPORT_FORMAT_JSON,
        overwrite=False,
        include_signals=False,
    )

    content = result.destination.read_text()
    document = json.loads(content)
    _assert_complete_exported_document(document, task, event_ids, content)
    _assert_usage_provenance(document)
    assert not document["unavailable_sections"]
    assert _snapshot(connection, reader, task.id) == before
    assert not (project_root / ".shikiru" / "worktrees" / task.id).exists()


async def test_export_keeps_legacy_counters_separate_from_normalized_usage(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task, _ = _seed_export_task(connection, project_root, clock)
    legacy_run_id = connection.execute(
        "SELECT id FROM runs WHERE cost_usd = 0 AND cost_basis = 'billed'"
    ).fetchone()[0]
    connection.execute("UPDATE runs SET usage_accounting = 'legacy' WHERE id = ?", (legacy_run_id,))
    connection.execute(
        "INSERT INTO legacy_run_usage (run_id, source, usage_reported, input_tokens, "
        "output_tokens, cache_read_tokens, cache_write_tokens) "
        "VALUES (?, 'pre_category_columns', NULL, 11, 2, 3, 4)",
        (legacy_run_id,),
    )
    exporter = TaskEvidenceExporter(
        _queries(reader, project_root, clock), LocalEvidenceWriter(project_root), clock
    )

    result = await exporter.export(
        task.id,
        "legacy.json",
        export_format=EXPORT_FORMAT_JSON,
        overwrite=False,
        include_signals=False,
    )

    document = json.loads(result.destination.read_text())
    legacy = next(item for item in document["sessions"] if item["legacy_usage"] is not None)
    assert legacy["usage_provenance"]["accounting"] == "legacy"
    assert legacy["usage_provenance"]["tokens"] == "legacy_recorded"
    assert legacy["tokens"] == {
        "uncached_input": None,
        "cache_read": None,
        "cache_write": None,
        "output": None,
        "processed": None,
    }
    assert legacy["legacy_usage"] == {
        "source": "pre_category_columns",
        "reported_marker": None,
        "recorded_counters": {
            "input_tokens": 11,
            "uncached_input_tokens": None,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
            "output_tokens": 2,
        },
        "interpretation": (
            "Historical counters may be partial or use prior accounting; they are retained as "
            "evidence and excluded from normalized token totals."
        ),
    }
    codex_usage = next(item for item in document["usage"] if item["agent"] == "codex")
    assert codex_usage["legacy_token_runs"] == 1
    assert codex_usage["tokens"]["output"] == {
        "total": 5,
        "status": "partial",
        "reported_runs": 1,
        "unreported_runs": 2,
    }


async def test_partial_turn_files_are_identified_as_unavailable(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task, _ = _seed_export_task(connection, project_root, clock)
    queries = _queries(reader, project_root, clock)
    exporter = TaskEvidenceExporter(queries, LocalEvidenceWriter(project_root), clock)
    missing_fact = (
        project_root / ".shikiru" / "runs" / task.id / "turns" / "2-implement-i1" / FACTS_FILENAME
    )
    missing_fact.unlink()
    missing_report = project_root / ".shikiru" / "runs" / task.id / "turns" / "1-triage"
    (missing_report / REPORT_KIND_FILENAME).unlink()
    (missing_report / REPORT_FILENAME).unlink()
    before = _snapshot(connection, reader, task.id)

    result = await exporter.export(
        task.id,
        "partial.json",
        export_format=EXPORT_FORMAT_JSON,
        overwrite=False,
        include_signals=False,
    )

    unavailable = json.loads(result.destination.read_text())["unavailable_sections"]
    assert any(
        item["section"] == "facts" and "2-implement-i1" in item["reason"] for item in unavailable
    )
    assert any(
        item["section"] == "reports" and "1-triage" in item["reason"] for item in unavailable
    )
    assert _snapshot(connection, reader, task.id) == before


async def test_markdown_fences_untrusted_content_and_opt_in_exposes_partial_signal_evidence(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task, _ = _seed_export_task(connection, project_root, clock)
    exporter = TaskEvidenceExporter(
        _queries(reader, project_root, clock),
        LocalEvidenceWriter(project_root),
        clock,
    )

    result = await exporter.export(
        task.id,
        "hostile.md",
        export_format=EXPORT_FORMAT_MARKDOWN,
        overwrite=False,
        include_signals=True,
    )

    content = result.destination.read_text()
    blocks, outside_blocks, fence_lengths = _markdown_json_blocks(content)
    assert PROCESSED_TOKENS_NOTE in outside_blocks
    assert max(fence_lengths) > 5
    assert "## forged heading" not in outside_blocks
    assert "[link](https://example.invalid)" not in outside_blocks
    request_block = next(
        block
        for block in blocks
        if isinstance(block, str) and block.startswith("Keep this request verbatim:")
    )
    assert request_block == f"Keep this request verbatim:\n{HOSTILE_CONTENT}"
    task_block = next(
        cast("dict[str, Any]", block)
        for block in blocks
        if isinstance(block, dict) and "rung_index" in block
    )
    assert task_block["title"] == "Evidence target"
    assert task_block["rung_index"] == {Role.LEADER.value: 2, Role.IMPLEMENTER.value: 1}
    usage_block = next(
        cast("list[dict[str, Any]]", block)
        for block in blocks
        if isinstance(block, list)
        and block
        and isinstance(block[0], dict)
        and "run_count" in block[0]
    )
    usage = {str(item["agent"]): item for item in usage_block}
    assert usage["codex"]["billed_cost_usd"] == 0
    assert usage["codex"]["api_equivalent_cost_usd"] == 0.25
    assert usage["claude"]["tokens"]["uncached_input"]["total"] == 0
    assert SIGNAL_CONTENT in content
    assert PARTIAL_SIGNAL_CONTENT in content
    assert "jsonl row has not ended with a newline" in content.casefold()
    assert '"section": "signals"' in content


async def test_missing_evidence_and_destination_failures_are_visible_and_atomic(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(EXPORT_START)
    task = seed_task(connection, clock, title="Evidence with missing files")
    exporter = TaskEvidenceExporter(
        _queries(reader, project_root, clock),
        LocalEvidenceWriter(project_root),
        clock,
    )
    before = _snapshot(connection, reader, task.id)
    missing_result = await exporter.export(
        task.id,
        "missing-evidence.json",
        export_format=EXPORT_FORMAT_JSON,
        overwrite=False,
        include_signals=False,
    )
    missing_document = json.loads(missing_result.destination.read_text())
    unavailable = {item["section"] for item in missing_document["unavailable_sections"]}
    assert {"facts", "reports", "sessions", "timeline"} <= unavailable
    assert _snapshot(connection, reader, task.id) == before

    writer = LocalEvidenceWriter(project_root)
    existing = project_root / "existing.md"
    existing.write_text(EXISTING_CONTENT)
    with pytest.raises(FileExistsError):
        writer.write(
            "existing.md",
            "replacement",
            overwrite=False,
            cancelled=threading.Event(),
        )
    with pytest.raises(UnicodeEncodeError):
        writer.write(
            "existing.md",
            INVALID_UTF8_CONTENT,
            overwrite=True,
            cancelled=threading.Event(),
        )
    assert existing.read_text() == EXISTING_CONTENT
    assert list(project_root.glob(".shikiru-evidence-*")) == []
    read_only = project_root / "read-only"
    read_only.mkdir()
    read_only.chmod(READ_ONLY_MODE)
    try:
        with pytest.raises(PermissionError):
            writer.write(
                "read-only/evidence.json",
                "evidence",
                overwrite=False,
                cancelled=threading.Event(),
            )
    finally:
        read_only.chmod(WRITABLE_MODE)
    with pytest.raises(FileNotFoundError):
        await exporter.export(
            task.id,
            FAILED_EXPORT_PATH,
            export_format=EXPORT_FORMAT_JSON,
            overwrite=True,
            include_signals=False,
        )
    assert not (project_root / "missing-parent").exists()
    assert _snapshot(connection, reader, task.id) == before


async def _selection_must_not_run(_pane_id: str) -> None:
    raise AssertionError(PANE_SELECTION_ERROR)
