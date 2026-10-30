"""Integration tests for a task's Results following its worktree while a turn is active."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.config.loader import load_settings
from shikiru.dashboard.briefing_view import LIVE_LABEL
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_IDENTITY,
    TASK_DETAIL_RESULTS,
    TASK_VIEW_RESULTS,
    TaskDetailPanel,
)
from shikiru.dashboard.fold_bodies import ResultsBody
from shikiru.domain.briefing import ProducedNumbers
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    Commit,
    ImplementationReport,
)
from shikiru.domain.results import ResultOutcome
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import RepoState, Status
from shikiru.paths import task_root, turns_dir
from shikiru.process import SubprocessRunner
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.workspace.facts import WorkspaceFacts, render_facts
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import ManualFocusHost, StubServices, state_views, widget_context
from tests.support.git_repo import commit_all, init_repo, run_git
from tests.support.render import RENDER_WIDTH, plain_text, rendered_lines
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.dashboard.views import StateDashboardViews
    from shikiru.domain.results import TaskResults
    from tests.support.store import Connection, Database

START = datetime(2026, 1, 1, tzinfo=UTC)
BASE_BRANCH = "main"
TASK_BRANCH = "shikiru/live"
TRACKED_FILE = "app.py"
TRACKED_TEXT = "one\ntwo\nthree\n"
EDITED_TEXT = "one\nTWO\nthree\n"
NO_BASE_SHA = "0" * 40
NO_WORK = ProducedNumbers(commits=0, files=0, insertions=0, deletions=0)


async def _init_worktree(runner: SubprocessRunner, worktree: Path) -> str:
    await init_repo(runner, worktree)
    (worktree / TRACKED_FILE).write_text(TRACKED_TEXT)
    await commit_all(runner, worktree, "base")
    base_sha = await run_git(runner, worktree, "rev-parse", "HEAD")
    await run_git(runner, worktree, "checkout", "-b", TASK_BRANCH)
    return base_sha


async def _worktree_task(
    connection: Connection, clock: FakeClock, project_root: Path, runner: SubprocessRunner
) -> tuple[str, Path]:
    task = seed_task(connection, clock, title="Retry on timeout")
    worktree = task_root(project_root, task.id)
    base_sha = await _init_worktree(runner, worktree)
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    return task.id, worktree


def _missing_worktree_task(connection: Connection, clock: FakeClock) -> str:
    task = seed_task(connection, clock, title="Retry on timeout")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=NO_BASE_SHA, branch=TASK_BRANCH),
    )
    return task.id


def _record_implementation(project_root: Path, task_id: str) -> None:
    turn = turns_dir(project_root, task_id) / "01-implement-i1"
    turn.mkdir(parents=True)
    (turn / FACTS_FILENAME).write_text(
        render_facts(WorkspaceFacts(commits=("abc1234 recorded first",)))
    )
    report = ImplementationReport(
        status="completed",
        summary="s",
        commits=[Commit(sha="abc1234", subject="recorded first")],
        tests=[],
        trackers_updated=False,
    )
    (turn / REPORT_KIND_FILENAME).write_text("implementation")
    (turn / REPORT_FILENAME).write_text(report.model_dump_json())


async def _results(views: StateDashboardViews, task_id: str) -> TaskResults:
    detail = await views.task_detail(task_id)
    assert detail is not None
    assert detail.results is not None
    return detail.results


def _subjects(results: TaskResults) -> list[str]:
    return [commit.subject for commit in results.commits]


def _results_text(panel: TaskDetailPanel) -> str:
    return plain_text(panel.query_one(f"#{TASK_DETAIL_RESULTS}", Static).content)


async def _wake(clock: FakeClock, seconds: float) -> None:
    await wait_until(clock.has_pending_sleep, timeout=UI_WAIT_SECONDS)
    clock.advance(seconds)


async def test_results_follow_edits_and_commits_without_a_new_report(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _worktree_task(connection, clock, project_root, runner)
    views = state_views(reader, project_root, clock, StubServices())
    window = load_settings(project_root).dashboard.task_work_cache_seconds
    assert (await _results(views, task_id)).produced == NO_WORK

    (worktree / TRACKED_FILE).write_text(EDITED_TEXT)
    clock.advance(window)
    edited = await _results(views, task_id)
    assert edited.produced == ProducedNumbers(commits=0, files=1, insertions=1, deletions=1)

    await commit_all(runner, worktree, "uppercase two")
    clock.advance(window)
    committed = await _results(views, task_id)
    assert _subjects(committed) == ["uppercase two"]
    assert committed.produced == ProducedNumbers(commits=1, files=1, insertions=1, deletions=1)
    assert committed.live_error is None


async def test_live_work_is_read_again_only_once_the_cache_window_has_passed(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _worktree_task(connection, clock, project_root, runner)
    views = state_views(reader, project_root, clock, StubServices())
    window = load_settings(project_root).dashboard.task_work_cache_seconds
    assert (await _results(views, task_id)).produced == NO_WORK

    (worktree / TRACKED_FILE).write_text(EDITED_TEXT)
    assert (await _results(views, task_id)).produced == NO_WORK

    clock.advance(window)
    assert (await _results(views, task_id)).produced == ProducedNumbers(
        commits=0, files=1, insertions=1, deletions=1
    )


async def test_recorded_commits_do_not_show_once_the_work_is_reverted(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _worktree_task(connection, clock, project_root, runner)
    _record_implementation(project_root, task_id)
    (worktree / TRACKED_FILE).write_text(EDITED_TEXT)
    (worktree / TRACKED_FILE).write_text(TRACKED_TEXT)
    views = state_views(reader, project_root, clock, StubServices())

    results = await _results(views, task_id)

    assert results.commits == ()
    assert results.produced == NO_WORK


async def test_an_unreadable_worktree_falls_back_to_recorded_results_and_says_so(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _missing_worktree_task(connection, clock)
    _record_implementation(project_root, task_id)
    views = state_views(reader, project_root, clock, StubServices())

    results = await _results(views, task_id)

    assert _subjects(results) == ["recorded first"]
    assert results.live_error is not None
    lines = rendered_lines(ResultsBody(results), width=RENDER_WIDTH)
    assert any(line.startswith(LIVE_LABEL) for line in lines)


async def test_a_finished_task_keeps_its_recorded_results(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _worktree_task(connection, clock, project_root, runner)
    (worktree / TRACKED_FILE).write_text(EDITED_TEXT)
    await commit_all(runner, worktree, "live work")
    tasks = TaskStore(connection, clock)
    tasks.finish(
        task_id,
        Transition.stay(tasks.require(task_id), step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={"commits": ["abc1234 recorded first"], "summary": "done"},
    )
    views = state_views(reader, project_root, clock, StubServices())

    results = await _results(views, task_id)

    assert results.outcome is ResultOutcome.MERGED
    assert _subjects(results) == ["recorded first"]
    assert results.live_error is None
    assert await views.live_work(task_id) is None


async def test_an_open_results_fold_follows_a_commit_without_a_database_event(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _worktree_task(connection, clock, project_root, runner)
    reader = StateReader(database, clock)
    settings = load_settings(project_root)
    panel = TaskDetailPanel(widget_context(reader, project_root, clock), task_id, fixed=True)
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 60)):
            await wait_until(
                lambda: (
                    task_id
                    in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
                    and bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            panel.open_view(TASK_VIEW_RESULTS)
            (worktree / TRACKED_FILE).write_text(EDITED_TEXT)
            await commit_all(runner, worktree, "uppercase two")
            assert "uppercase two" not in _results_text(panel)

            await _wake(clock, settings.dashboard.task_work_cache_seconds)

            await wait_until(
                lambda: "uppercase two" in _results_text(panel), timeout=UI_WAIT_SECONDS
            )
    finally:
        reader.close()
