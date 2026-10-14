from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from textual.widgets import Button, Static

from shikiru.agents.contracts import Usage
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.detail import (
    TASK_DETAIL_ACTION_PREFIX,
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_IDENTITY,
)
from shikiru.domain.briefing import BriefingAction
from shikiru.domain.escalation import options_for
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    ImplementationReport,
    Issue,
    Verdict,
)
from shikiru.domain.reports import TestRun as AgentTestRun
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status
from shikiru.paths import run_dir, turns_dir
from shikiru.plugins.widgets.queue import QueuePanel
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.view_log import (
    VIEW_MESSAGE_SIGNAL,
    VIEW_MESSAGE_TEXT_KEY,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TURN_KEY,
    view_log_path,
)
from shikiru.workspace.facts import WorkspaceFacts, render_facts
from shikiru.workspace.reports import ENGINE_VERDICT_FILENAME
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, ignore_palette_action, state_views
from tests.support.factories import IMPLEMENTER_RUNGS, LEADER_RUNG
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
WIDE_SIZE = (100, 40)
COMPACT_SIZE = (40, 40)
QUESTION = "Which failures should the parser retry?"
LAST_WORDS = "Running the parser tests after the retry change."
REPORT_SUMMARY = "Added bounded retries to the parser."
RESCUED_ITERATION = 3
ELAPSED_MINUTES = 31
CHECKS = ("Needed from you", "Now", "Produced", "Verdict", "PR", "Cost")
NEVER_SHOWN = ("ToolStarted", "Last recorded signal", "Work is active", "Queued for execution")


@dataclass(frozen=True)
class _Case:
    seed: Callable[[Connection, FakeClock, Path], Task]
    rows: frozenset[str]
    texts: tuple[str, ...]
    verbs: frozenset[BriefingAction]
    missing: tuple[str, ...] = ()


def _turn_dir(project_root: Path, task: Task, iteration: int) -> Path:
    turn = turns_dir(project_root, task.id) / f"02-implement-i{iteration}"
    turn.mkdir(parents=True, exist_ok=True)
    return turn


def _facts(turn: Path) -> None:
    (turn / FACTS_FILENAME).write_text(
        render_facts(
            WorkspaceFacts(
                commits=("abc1234 first", "def5678 second", "0123456 third"),
                diff_stat=" a.py | 3 ++-\n 5 files changed, 120 insertions(+), 34 deletions(-)\n",
            )
        )
    )


def _report(turn: Path, report: ImplementationReport) -> None:
    (turn / REPORT_KIND_FILENAME).write_text("implementation")
    (turn / REPORT_FILENAME).write_text(report.model_dump_json())


def _implementation(summary: str, *, question: str = "") -> ImplementationReport:
    return ImplementationReport(
        status="needs_decision" if question else "completed",
        summary=summary,
        commits=[],
        tests=[
            AgentTestRun(name="a", result="passed"),
            AgentTestRun(name="b", result="failed"),
        ],
        trackers_updated=False,
        question=question,
    )


def _running(connection: Connection, clock: FakeClock, iteration: int) -> Task:
    tasks = TaskStore(connection, clock)
    task = seed_task(connection, clock, title="Add retry to parser", mode=Mode.IMPLEMENT)
    tasks.set_slot(task.id, task.seq)
    tasks.apply_transition(task.id, first_step(task))
    current = tasks.get(task.id)
    assert current is not None
    tasks.apply_transition(
        task.id,
        dataclasses.replace(
            Transition.stay(current, step=Step.IMPLEMENT, status=Status.RUNNING),
            iteration=iteration,
        ),
    )
    stored = tasks.get(task.id)
    assert stored is not None
    return stored


def _spend(connection: Connection, clock: FakeClock, task: Task) -> None:
    runs = RunStore(connection, clock)
    leader = runs.start(task.id, Role.LEADER, LEADER_RUNG)
    runs.record_usage(leader.id, Usage(10, 5, 0, 0, 0.42, "billed", 8))
    implementer = runs.start(task.id, Role.IMPLEMENTER, IMPLEMENTER_RUNGS[0])
    runs.record_usage(implementer.id, Usage(30, 20, 0, 0, 1.10, "billed", 12))


def _live_log(project_root: Path, task: Task, clock: FakeClock) -> None:
    role_root = run_dir(project_root, task.id) / Role.IMPLEMENTER.value
    role_root.mkdir(parents=True)
    turn = turns_dir(project_root, task.id) / f"001-implement-i{task.iteration}"
    turn.mkdir(parents=True)
    (role_root / "current_turn").symlink_to(turn, target_is_directory=True)
    row = {
        VIEW_TIME_KEY: clock.now().isoformat(),
        VIEW_TURN_KEY: turn.name,
        VIEW_SIGNAL_KEY: VIEW_MESSAGE_SIGNAL,
        VIEW_MESSAGE_TEXT_KEY: LAST_WORDS,
    }
    view_log_path(run_dir(project_root, task.id), Role.IMPLEMENTER.value).write_text(
        json.dumps(row) + "\n"
    )


def _seed_running(connection: Connection, clock: FakeClock, project_root: Path) -> Task:
    task = _running(connection, clock, iteration=RESCUED_ITERATION)
    _spend(connection, clock, task)
    _facts(_turn_dir(project_root, task, task.iteration))
    _report(_turn_dir(project_root, task, task.iteration), _implementation(REPORT_SUMMARY))
    clock.advance(ELAPSED_MINUTES * 60)
    _live_log(project_root, task, clock)
    return task


def _seed_decision(connection: Connection, clock: FakeClock, project_root: Path) -> Task:
    task = _running(connection, clock, iteration=1)
    _report(_turn_dir(project_root, task, 1), _implementation("Blocked", question=QUESTION))
    tasks = TaskStore(connection, clock)
    reason = EscalationReason.DECISION_REQUIRED
    tasks.apply_transition(task.id, Transition.escalate_current(task, reason))
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=reason,
        headline="decision",
        packet_path="unused.packet",
        options=options_for(reason),
    )
    return task


def _seed_rejected(connection: Connection, clock: FakeClock, project_root: Path) -> Task:
    task = _running(connection, clock, iteration=2)
    turn = _turn_dir(project_root, task, 2)
    _facts(turn)
    _report(turn, _implementation(REPORT_SUMMARY))
    verdict = Verdict(
        decision="changes_requested",
        summary="Needs work",
        issues=[
            Issue(severity="major", description="first"),
            Issue(severity="major", description="second"),
            Issue(severity="minor", description="third"),
        ],
    )
    (turn / ENGINE_VERDICT_FILENAME).write_text(verdict.model_dump_json())
    EventLog(connection, clock).append(
        EventKind.PR_OPENED,
        task_id=task.id,
        message="PR opened",
        data={"url": "https://example.test/o/r/pull/41", "branch": "b", "commits": []},
    )
    return task


def _seed_done(connection: Connection, clock: FakeClock, project_root: Path) -> Task:
    task = _running(connection, clock, iteration=1)
    turn = _turn_dir(project_root, task, 1)
    _facts(turn)
    _report(turn, _implementation(REPORT_SUMMARY))
    tasks = TaskStore(connection, clock)
    tasks.finish(
        task.id,
        Transition.stay(task, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={"summary": "done"},
    )
    return task


def _seed_cancelled(connection: Connection, clock: FakeClock, project_root: Path) -> Task:
    del project_root
    task = _running(connection, clock, iteration=1)
    TaskStore(connection, clock).finish(
        task.id,
        Transition.stay(task, step=None, status=Status.CANCELLED),
        EventKind.TASK_CANCELLED,
        message="cancelled",
        data={},
    )
    return task


CASES = {
    "running": _Case(
        _seed_running,
        frozenset({"Now", "Produced", "Cost"}),
        (
            "rework 2 of 4",
            "31m total",
            "active 0s ago",
            LAST_WORDS,
            "tests 1 passed · 1 failed (agent-reported)",
            "Leader $0.42 · Impl $1.10 billed",
        ),
        frozenset({BriefingAction.TAKE_OVER, BriefingAction.MESSAGE, BriefingAction.PAUSE}),
    ),
    "waiting-on-decision": _Case(
        _seed_decision,
        frozenset({"Needed from you", "Produced"}),
        (QUESTION,),
        frozenset({BriefingAction.ANSWER, BriefingAction.MESSAGE, BriefingAction.PAUSE}),
        ("rework",),
    ),
    "rejected-with-verdict": _Case(
        _seed_rejected,
        frozenset({"Now", "Produced", "Verdict", "PR"}),
        ("3 commits · 5 files · +120 \N{MINUS SIGN}34", "Rejected · 2 major · 1 minor", "#41 open"),
        frozenset({BriefingAction.TAKE_OVER, BriefingAction.MESSAGE, BriefingAction.PAUSE}),
    ),
    "done-with-report": _Case(
        _seed_done,
        frozenset({"Produced"}),
        (REPORT_SUMMARY, "3 commits"),
        frozenset(),
        ("rework",),
    ),
    "cancelled": _Case(_seed_cancelled, frozenset(), (), frozenset(), ("rework",)),
}


def _app(reader: StateReader, project_root: Path, clock: FakeClock) -> DashboardApp:
    settings = load_settings(project_root)
    return DashboardApp(
        focus_task=None,
        views=state_views(reader, project_root, clock, StubServices()),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        open_palette=ignore_palette_action,
        widget_names=["queue"],
    )


def _is_loaded(identity: Static, task: Task) -> bool:
    text = plain_text(identity.content)
    return text.startswith(task.id) and "loading" not in text


def _shown_verbs(app: DashboardApp) -> frozenset[BriefingAction]:
    return frozenset(
        action
        for action in BriefingAction
        if app.query_one(f"#{TASK_DETAIL_ACTION_PREFIX}{action.value}", Button).display
    )


async def test_briefing_shows_only_the_rows_the_task_has_data_for(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks = {name: case.seed(connection, clock, project_root) for name, case in CASES.items()}
    app = _app(reader, project_root, clock)
    async with app.run_test(size=WIDE_SIZE):
        briefing = app.query_one(f"#{TASK_DETAIL_BRIEFING}", Static)
        identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
        for name, case in CASES.items():
            task = tasks[name]
            app.query_one("#queue", QueuePanel).select_task(task.id)
            await wait_until(lambda task=task: _is_loaded(identity, task), timeout=UI_WAIT_SECONDS)
            await wait_until(
                lambda case=case: _shown_verbs(app) == case.verbs, timeout=UI_WAIT_SECONDS
            )
            text = f"{plain_text(identity.content)} {plain_text(briefing.content)}"
            for row in CHECKS:
                assert (row in text) is (row in case.rows), (name, row)
            for expected in case.texts:
                assert expected in text, (name, expected)
            for never in (*NEVER_SHOWN, *case.missing):
                assert never not in text, (name, never)
