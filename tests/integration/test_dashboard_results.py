from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console

from shikiru.dashboard.fold_bodies import DecisionsBody, ResultsBody
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    FACTS_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    Commit,
    ImplementationReport,
    Issue,
    PlanReport,
    Verdict,
)
from shikiru.domain.reports import TestRun as AgentTestRun
from shikiru.domain.results import ResultOutcome
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import Mode, Status
from shikiru.engine.events import emit_verdict
from shikiru.operator.queries import TASK_DETAIL_EVENT_LIMIT
from shikiru.paths import turns_dir
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from shikiru.ui.tokens import SAGE, SECONDARY_TEXT_COLOR, STEEL_BLUE
from shikiru.workspace.facts import WorkspaceFacts, render_facts
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, state_views
from tests.support.render import RENDER_WIDTH, plain_text, rendered_lines
from tests.support.store import seed_task

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import RenderableType

    from shikiru.dashboard.views import StateDashboardViews
    from shikiru.domain.results import TaskResults
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
PLAN_SUMMARY = "Split the admin work into two tasks " + "and keep the parser untouched " * 3
LONG_SCOPE = "drop the legacy CLI flag and rewrite the parser " * 3 + "end-marker"


def _running(
    connection: Connection, clock: FakeClock, project_root: Path, *, plan: bool = False
) -> str:
    tasks = TaskStore(connection, clock)
    task = seed_task(
        connection, clock, title="Add retry", mode=Mode.PLAN if plan else Mode.IMPLEMENT
    )
    tasks.set_slot(task.id, task.seq)
    tasks.apply_transition(task.id, first_step(task))
    current = tasks.get(task.id)
    assert current is not None
    tasks.apply_transition(
        task.id, Transition.stay(current, step=Step.IMPLEMENT, status=Status.RUNNING)
    )
    turn = turns_dir(project_root, task.id) / "02-implement-i1"
    turn.mkdir(parents=True)
    (turn / FACTS_FILENAME).write_text(
        render_facts(
            WorkspaceFacts(
                commits=("abc1234 first", "def5678 second"),
                diff_stat=" a.py | 3 ++-\n 2 files changed, 12 insertions(+), 3 deletions(-)\n",
            )
        )
    )
    report: ImplementationReport | PlanReport = (
        PlanReport(summary=PLAN_SUMMARY, commits=[Commit(sha="9999999abc", subject="plan spec")])
        if plan
        else ImplementationReport(
            status="completed",
            summary="s",
            commits=[],
            tests=[AgentTestRun(name="a", result="passed")],
            trackers_updated=False,
        )
    )
    (turn / REPORT_KIND_FILENAME).write_text("plan" if plan else "implementation")
    (turn / REPORT_FILENAME).write_text(report.model_dump_json())
    return task.id


def _foregrounds(body: RenderableType, needles: tuple[str, ...]) -> dict[str, str | None]:
    console = Console(width=RENDER_WIDTH, force_terminal=True, color_system="truecolor")
    cells = [
        (
            char,
            segment.style.color.get_truecolor().hex
            if segment.style and segment.style.color
            else None,
        )
        for segment in console.render(body)
        for char in segment.text
    ]
    text = "".join(char for char, _ in cells)
    return {needle: cells[text.index(needle)][1] for needle in needles}


async def _results(views: StateDashboardViews, task_id: str) -> TaskResults:
    detail = await views.task_detail(task_id)
    assert detail is not None
    assert detail.results is not None
    return detail.results


async def _assert_plan_results(views: StateDashboardViews, planned: str) -> None:
    plan_detail = await views.task_detail(planned)
    assert plan_detail is not None
    lines = rendered_lines(ResultsBody(plan_detail.results), width=60)
    flat = " ".join(" ".join(lines).split())
    assert lines[1].startswith("Plan")
    assert "added t-90" in lines[1]
    assert flat.index("Summary") < flat.index("Commits") < flat.index("9999999 plan spec")
    assert _foregrounds(ResultsBody(plan_detail.results), ("Summary",)) == {
        "Summary": SECONDARY_TEXT_COLOR
    }
    assert "and keep the parser untouched" in flat
    assert "added t-91" in flat


async def test_results_show_what_exists_so_far_and_what_a_done_task_merged(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    running = _running(connection, clock, project_root)
    planned = _running(connection, clock, project_root, plan=True)
    finished = _running(connection, clock, project_root)
    log = EventLog(connection, clock)
    rejected = Verdict(
        decision="changes_requested",
        summary="rework",
        issues=[Issue(severity="major", description="x"), Issue(severity="major", description="y")],
    )
    emit_verdict(log, TaskStore(connection, clock).require(finished), rejected, rejected.summary)
    emit_verdict(
        log,
        TaskStore(connection, clock).require(finished),
        Verdict(decision="accept", summary="ok"),
        "ok",
    )
    log.append(
        EventKind.ESCALATION_RESOLVED,
        task_id=finished,
        message="resolved",
        data={"resolution": "answer", "text": "Use mocked QA"},
    )
    log.append(
        EventKind.SCOPE_CHANGE,
        task_id=finished,
        message="scope",
        data={"new_task_ids": ["t-99"], "scope_changes": [LONG_SCOPE]},
    )
    log.append(
        EventKind.SCOPE_CHANGE,
        task_id=planned,
        message="scope",
        data={"new_task_ids": ["t-90", "t-91"], "scope_changes": ["added two tasks"]},
    )
    for _ in range(TASK_DETAIL_EVENT_LIMIT + 1):
        log.append(EventKind.NUDGE, task_id=finished, message="nudged", data={})
    current = TaskStore(connection, clock).get(finished)
    assert current is not None
    TaskStore(connection, clock).finish(
        finished,
        Transition.stay(current, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={"summary": "done", "commits": ["fedcba9 merged work"], "residual_risks": ["risk"]},
    )
    views = state_views(reader, project_root, clock, StubServices())

    so_far = await _results(views, running)
    merged = await _results(views, finished)
    assert so_far.produced is not None
    assert so_far.tests is not None

    assert so_far.outcome is ResultOutcome.SO_FAR
    assert [commit.sha for commit in so_far.commits] == ["abc1234", "def5678"]
    assert (so_far.produced.files, so_far.produced.insertions) == (2, 12)
    assert so_far.tests.passed == 1
    assert merged.outcome is ResultOutcome.MERGED
    assert [commit.subject for commit in merged.commits] == ["merged work"]
    assert merged.residual_risks == ("risk",)
    assert merged.plan_changes[0].new_tasks == (("t-99", None),)

    detail = await views.task_detail(finished)
    assert detail is not None
    results_text = plain_text(ResultsBody(detail.results))
    decisions_text = plain_text(DecisionsBody(detail))
    assert "2 files +12 \N{MINUS SIGN}3" in results_text
    assert "fedcba9 merged work" in results_text
    assert "added t-99" in results_text
    assert "reported by the Implementer" in plain_text(ResultsBody(await _results(views, running)))
    assert "end-marker" in results_text
    assert "\N{HORIZONTAL ELLIPSIS}" not in results_text
    colours = _foregrounds(ResultsBody(detail.results), ("fedcba9", "added t-99"))
    assert colours["fedcba9"] == STEEL_BLUE
    assert colours["added t-99"] == SAGE
    deciders = ("Leader", "You", "Engine")
    assert _foregrounds(DecisionsBody(detail), deciders) == dict.fromkeys(deciders, STEEL_BLUE)
    assert decisions_text.index("changes requested") < decisions_text.index("accepted")
    assert '00:00 You answered "Use mocked QA"' in decisions_text
    assert "so far" in plain_text(ResultsBody(await _results(views, running)))

    await _assert_plan_results(views, planned)
