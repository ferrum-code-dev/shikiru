from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from textual.widgets import Button, Static

from shikiru.agents.contracts import Usage
from shikiru.config.loader import load_settings
from shikiru.dashboard.app import DashboardApp
from shikiru.dashboard.detail import (
    TASK_DETAIL_ACTION_PREFIX,
    TASK_DETAIL_ACTIONS,
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_DETAILS_BUTTON,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_VERBS,
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
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Mode, RepoState, Role, Status
from shikiru.operator.queries import TASK_DETAIL_EVENT_LIMIT
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
from tests.support.render import plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

LONG_WORDS = " ".join(f"w{index:03d}" for index in range(120))
NOW_WORD = re.compile(r"\bw\d{3}\b")
START = datetime(2026, 1, 1, tzinfo=UTC)
WIDE_SIZE = (100, 40)
COMPACT_SIZE = (40, 40)
TINY_SIZE = (40, 8)
BRIEFING_SIZES = (WIDE_SIZE, (60, 40), (48, 40), COMPACT_SIZE, TINY_SIZE)
BUTTON_LINE_PAD = 1
BRANCH = "task/t-12"
QUESTION = "Which failures should the parser retry?"
LAST_WORDS = "Running the parser tests after the retry change."
REPORT_SUMMARY = "Added bounded retries to the parser."
RESCUED_ITERATION = 3
ELAPSED_MINUTES = 31
TITLES = ("Progress", "Result so far", "Result", "Problems")
LABELS = (
    "Step",
    "Rework",
    "Now",
    "Summary",
    "Branch",
    "Merged",
    "Commits",
    "Tests",
    "PR",
    "Verdict",
    "Needed",
    "Risks",
    "Cost",
)
NEVER_SHOWN = ("ToolStarted", "Last recorded signal", "Work is active", "Queued for execution")


@dataclass(frozen=True)
class _Case:
    seed: Callable[[Connection, FakeClock, Path], Task]
    titles: frozenset[str]
    labels: frozenset[str]
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
    tasks.set_repo(task.id, RepoState(base="main", base_ref="main", base_sha="0", branch=BRANCH))
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


def _live_log(project_root: Path, task: Task, clock: FakeClock, words: str = LAST_WORDS) -> None:
    role_root = run_dir(project_root, task.id) / Role.IMPLEMENTER.value
    role_root.mkdir(parents=True)
    turn = turns_dir(project_root, task.id) / f"001-implement-i{task.iteration}"
    turn.mkdir(parents=True)
    (role_root / "current_turn").symlink_to(turn, target_is_directory=True)
    row = {
        VIEW_TIME_KEY: clock.now().isoformat(),
        VIEW_TURN_KEY: turn.name,
        VIEW_SIGNAL_KEY: VIEW_MESSAGE_SIGNAL,
        VIEW_MESSAGE_TEXT_KEY: words,
    }
    view_log_path(run_dir(project_root, task.id), Role.IMPLEMENTER.value).write_text(
        json.dumps(row) + "\n"
    )


def _seed_running(
    connection: Connection, clock: FakeClock, project_root: Path, words: str = LAST_WORDS
) -> Task:
    task = _running(connection, clock, iteration=RESCUED_ITERATION)
    _spend(connection, clock, task)
    _facts(_turn_dir(project_root, task, task.iteration))
    _report(_turn_dir(project_root, task, task.iteration), _implementation(REPORT_SUMMARY))
    clock.advance(ELAPSED_MINUTES * 60)
    _live_log(project_root, task, clock, words)
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


def _seed_waiting_on_dependency(
    connection: Connection, clock: FakeClock, project_root: Path
) -> Task:
    del project_root
    task = seed_task(connection, clock, title="Add retry to parser", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.DEPENDENCY)
    return task


def _seed_cancelled_while_consulting(
    connection: Connection, clock: FakeClock, project_root: Path
) -> Task:
    del project_root
    task = _running(connection, clock, iteration=1)
    tasks = TaskStore(connection, clock)
    tasks.apply_transition(task.id, Transition.stay(task, step=Step.CONSULT, status=Status.RUNNING))
    tasks.set_wait_reason(task.id, WaitReason.DEPENDENCY)
    tasks.finish(
        task.id,
        Transition.stay(tasks.require(task.id), step=Step.CONSULT, status=Status.CANCELLED),
        EventKind.TASK_CANCELLED,
        message="cancelled",
        data={},
    )
    return tasks.require(task.id)


def _seed_done_after_long_history(
    connection: Connection, clock: FakeClock, project_root: Path
) -> Task:
    del project_root
    task = _running(connection, clock, iteration=1)
    log = EventLog(connection, clock)
    for _ in range(TASK_DETAIL_EVENT_LIMIT + 1):
        log.append(EventKind.NUDGE, task_id=task.id, message="nudged", data={})
    log.append(
        EventKind.PR_OPENED,
        task_id=task.id,
        message="PR opened",
        data={"url": "https://example.test/o/r/pull/41", "branch": "b", "commits": []},
    )
    TaskStore(connection, clock).finish(
        task.id,
        Transition.stay(task, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={"summary": "done"},
    )
    return task


TERMINAL_NEVER = ("unknown", "Consulting", "in step", "waiting", "open", "Implementer")

MINUS = "\N{MINUS SIGN}"
TAKE_OVER_VERBS = frozenset(
    {
        BriefingAction.TAKE_OVER,
        BriefingAction.MESSAGE,
        BriefingAction.PAUSE,
        BriefingAction.CANCEL,
    }
)

CASES = {
    "running": _Case(
        _seed_running,
        frozenset({"Progress", "Result so far"}),
        frozenset({"Step", "Rework", "Now", "Branch", "Commits", "Tests", "Cost"}),
        (
            "rework 2 of 4",
            "31m total",
            "active 0s ago",
            LAST_WORDS,
            f"{BRANCH} \N{RIGHTWARDS ARROW} main",
            "1 passed · 1 failed · agent-reported",
            "Leader $0.42 · Impl $1.10 billed",
        ),
        TAKE_OVER_VERBS,
    ),
    "waiting-on-dependency": _Case(
        _seed_waiting_on_dependency,
        frozenset({"Problems"}),
        frozenset({"Needed"}),
        ("Waiting on dependency",),
        frozenset({BriefingAction.PAUSE, BriefingAction.CANCEL}),
    ),
    "waiting-on-decision": _Case(
        _seed_decision,
        frozenset({"Progress", "Result so far", "Problems"}),
        frozenset({"Step", "Branch", "Tests", "Needed"}),
        (QUESTION,),
        frozenset(
            {
                BriefingAction.ANSWER,
                BriefingAction.MESSAGE,
                BriefingAction.PAUSE,
                BriefingAction.CANCEL,
            }
        ),
        ("rework",),
    ),
    "rejected-with-verdict": _Case(
        _seed_rejected,
        frozenset({"Progress", "Result so far", "Problems"}),
        frozenset({"Step", "Rework", "Now", "Branch", "Commits", "Tests", "PR", "Verdict"}),
        (
            f"3 commits · 5 files · +120 {MINUS}34",
            "Rejected · 2 major · 1 minor",
            "#41 open",
        ),
        TAKE_OVER_VERBS,
    ),
    "done-with-report": _Case(
        _seed_done,
        frozenset({"Result"}),
        frozenset({"Summary", "Merged", "Commits", "Tests"}),
        (REPORT_SUMMARY, f"{BRANCH} \N{RIGHTWARDS ARROW} main", "3 commits"),
        frozenset(),
        ("rework", "Progress", "Problems"),
    ),
    "cancelled": _Case(
        _seed_cancelled,
        frozenset({"Result"}),
        frozenset({"Branch"}),
        (f"{BRANCH} (kept, not merged)",),
        frozenset(),
        ("rework", "Progress", "Problems", "Summary"),
    ),
    "cancelled-while-consulting": _Case(
        _seed_cancelled_while_consulting,
        frozenset({"Result"}),
        frozenset({"Branch"}),
        ("Cancelled \N{MIDDLE DOT} stopped during consult",),
        frozenset(),
        TERMINAL_NEVER,
    ),
    "done-after-long-history": _Case(
        _seed_done_after_long_history,
        frozenset({"Result"}),
        frozenset({"Merged", "PR"}),
        ("Done", "#41 merged"),
        frozenset(),
        TERMINAL_NEVER,
    ),
}


def _app(
    reader: StateReader, project_root: Path, clock: FakeClock, focus_task: str | None = None
) -> DashboardApp:
    settings = load_settings(project_root)
    return DashboardApp(
        focus_task=focus_task,
        views=state_views(reader, project_root, clock, StubServices()),
        on_change=reader.watch,
        clock=clock,
        motion_interval_seconds=None,
        resources_refresh_seconds=settings.orchestration.resources.sample_seconds,
        docker_refresh_seconds=settings.dashboard.docker_refresh_seconds,
        disk_refresh_seconds=settings.dashboard.disk_refresh_seconds,
        quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
        quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
        now_max_lines=settings.dashboard.now_max_lines,
        now_compact_lines=settings.dashboard.now_compact_lines,
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
            lines = [
                line.strip() for line in rendered_lines(briefing.content, width=briefing.size.width)
            ]
            for title in TITLES:
                assert (title in lines) is (title in case.titles), (name, title)
            for label in LABELS:
                shown = any(line.startswith(f"{label} ") for line in lines)
                assert shown is (label in case.labels), (name, label)
            for expected in case.texts:
                assert expected in text, (name, expected)
            for never in (*NEVER_SHOWN, *case.missing):
                assert never not in text, (name, never)


async def test_every_briefing_stays_inside_its_width(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks = {name: case.seed(connection, clock, project_root) for name, case in CASES.items()}
    for size in BRIEFING_SIZES:
        app = _app(reader, project_root, clock)
        async with app.run_test(size=size):
            briefing = app.query_one(f"#{TASK_DETAIL_BRIEFING}", Static)
            identity = app.query_one(f"#{TASK_DETAIL_IDENTITY}", Static)
            for name in CASES:
                task = tasks[name]
                app.query_one("#queue", QueuePanel).select_task(task.id)
                await wait_until(
                    lambda task=task, identity=identity: _is_loaded(identity, task),
                    timeout=UI_WAIT_SECONDS,
                )
                lines = rendered_lines(briefing.content, width=briefing.size.width)
                assert all(len(line) <= briefing.size.width for line in lines), (size, name)


@pytest.mark.parametrize(
    ("size", "compact"),
    [((100, 40), False), ((60, 40), False), ((48, 40), True), ((40, 40), True), ((40, 8), True)],
)
async def test_a_long_last_message_wraps_within_the_width_up_to_the_configured_lines(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    size: tuple[int, int],
    compact: bool,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = _seed_running(connection, clock, project_root, LONG_WORDS)
    dashboard = load_settings(project_root).dashboard
    cap = dashboard.now_compact_lines if compact else dashboard.now_max_lines
    app = _app(reader, project_root, clock, focus_task=task.id)
    async with app.run_test(size=size):
        briefing = app.query_one(f"#{TASK_DETAIL_BRIEFING}", Static)
        await wait_until(
            lambda: "w000" in plain_text(briefing.content, width=size[0]), timeout=UI_WAIT_SECONDS
        )
        lines = rendered_lines(briefing.content, width=briefing.size.width)
        quoted = [line for line in lines if NOW_WORD.search(line)]
        assert len(quoted) == cap
        assert "…" in quoted[-1]
        assert not any("…" in line for line in quoted[:-1])
        assert all(len(line) <= briefing.size.width for line in lines)
        assert briefing.region.bottom <= app.screen.size.height


async def test_every_offered_verb_stays_inside_the_actions_row_at_compact_width(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = _seed_running(connection, clock, project_root)
    for size in (COMPACT_SIZE, TINY_SIZE):
        app = _app(reader, project_root, clock, focus_task=task.id)
        async with app.run_test(size=size):
            await wait_until(
                lambda app=app: _shown_verbs(app) == CASES["running"].verbs,
                timeout=UI_WAIT_SECONDS,
            )
            row = app.query_one(f"#{TASK_DETAIL_ACTIONS}").region
            details = app.query_one(f"#{TASK_DETAIL_DETAILS_BUTTON}").region
            for button in app.query(f"#{TASK_DETAIL_VERBS} Button"):
                if button.display:
                    assert row.contains_region(button.region), (size, button.id)
                    assert button.region.right - BUTTON_LINE_PAD <= details.x, (size, button.id)
