from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.briefing import (
    FACTS_COMMITS_HEADING,
    FACTS_DIFF_STAT_HEADING,
    ProducedNumbers,
    ReportedTests,
    parse_produced,
    pull_request_line,
)
from shikiru.domain.reports import Commit
from shikiru.domain.results import (
    LiveWork,
    PlanChange,
    ResultCommit,
    ResultOutcome,
    TaskResults,
    derive_task_results,
    tracks_live_work,
)
from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CAP = 3
FACTS = (
    f"{FACTS_COMMITS_HEADING}\n\naaa1111 facts one\nbbb2222 facts two\n\n{FACTS_DIFF_STAT_HEADING}"
    "\n\n a.py | 3 ++-\n 2 files changed, 10 insertions(+), 4 deletions(-)\n\n### status"
)
TESTS = ReportedTests(passed=4, failed=1, skipped=0)
DONE = {
    "summary": "s",
    "commits": ["1234567890 first", "abcdef0 second", "fedcba9 third", "0000000 fourth"],
    "residual_risks": ["slow on cold start"],
}
SCOPE = {"scope_changes": ["drop the CLI flag"], "new_task_ids": ["t-13", "t-14"]}
LIVE = LiveWork.from_oneline(
    ["1111111 live first", "2222222 live second"], files=2, insertions=5, deletions=1
)
REVERTED = LiveWork.from_oneline([], files=0, insertions=0, deletions=0)


def _derive(
    status: Status,
    *,
    done: Mapping[str, object] | None = None,
    report: Sequence[Commit] = (),
    facts: str | None = None,
    scope: Sequence[Mapping[str, object]] = (),
    verdict_risks: Sequence[str] = (),
    titles: Mapping[str, str] | None = None,
    live: LiveWork | None = None,
    live_error: str | None = None,
) -> TaskResults:
    return derive_task_results(
        status,
        branch="task/t-12",
        base="main",
        done=done,
        report_commits=report,
        facts=facts,
        tests=TESTS,
        pull_request=pull_request_line({"url": "https://h/o/r/pull/7"}, task_done=True),
        scope_events=scope,
        titles=titles or {},
        verdict_risks=verdict_risks,
        plan_summary=None,
        commit_cap=CAP,
        live=live,
        live_error=live_error,
    )


def test_a_done_task_reports_what_was_merged_from_its_done_record() -> None:
    results = _derive(
        Status.DONE,
        done=DONE,
        report=[Commit(sha="x", subject="y")],
        scope=[SCOPE],
        titles={"t-13": "Admin page"},
    )
    assert results.outcome is ResultOutcome.MERGED
    assert results.commits == (
        ResultCommit("1234567", "first"),
        ResultCommit("abcdef0", "second"),
        ResultCommit("fedcba9", "third"),
    )
    assert results.more_commits == 1
    assert results.residual_risks == ("slow on cold start",)
    assert results.plan_changes == (
        PlanChange(("drop the CLI flag",), (("t-13", "Admin page"), ("t-14", None))),
    )
    assert results.pull_request is not None
    assert results.pull_request.number == 7


def test_commits_come_from_the_first_source_that_has_some() -> None:
    report = [Commit(sha="9999999999", subject="from report")]
    cases = (
        (_derive(Status.RUNNING, report=report, facts=FACTS), "9999999", "from report"),
        (_derive(Status.RUNNING, facts=FACTS), "aaa1111", "facts one"),
        (_derive(Status.DONE, done={"summary": "s"}, report=report), "9999999", "from report"),
    )
    for results, sha, subject in cases:
        assert results.commits[0] == ResultCommit(sha, subject)


def test_unfinished_and_cancelled_tasks_show_what_exists_without_claiming_a_merge() -> None:
    so_far = _derive(Status.RUNNING, facts=FACTS, verdict_risks=["flaky test"])
    assert so_far.outcome is ResultOutcome.SO_FAR
    assert so_far.produced == ProducedNumbers(2, 2, 10, 4)
    assert so_far.more_commits == 0
    assert so_far.residual_risks == ("flaky test",)
    cancelled = _derive(Status.CANCELLED, facts=FACTS)
    assert cancelled.outcome is ResultOutcome.NOT_MERGED
    assert (cancelled.branch, cancelled.base) == ("task/t-12", "main")


def test_residual_risks_prefer_the_done_record_list_else_the_current_verdict() -> None:
    cases: tuple[tuple[Status, Mapping[str, object] | None, tuple[str, ...]], ...] = (
        (Status.DONE, {"residual_risks": ["done risk"]}, ("done risk",)),
        (Status.DONE, {"residual_risks": []}, ()),
        (Status.DONE, {"summary": "s"}, ("verdict risk",)),
        (Status.RUNNING, None, ("verdict risk",)),
    )
    for status, done, risks in cases:
        assert _derive(status, done=done, verdict_risks=["verdict risk"]).residual_risks == risks


def test_nothing_recorded_gives_empty_fields_not_guesses() -> None:
    results = _derive(Status.DONE)
    assert results.commits == ()
    assert results.more_commits == 0
    assert results.produced is None
    assert results.plan_changes == ()
    assert results.residual_risks == ()


def test_a_live_observation_outranks_every_recorded_source() -> None:
    report = [Commit(sha="9999999999", subject="from report")]
    recorded = (ResultCommit("9999999", "from report"),)
    cases = (
        (LIVE, None, LIVE.commits, LIVE.produced, None),
        (REVERTED, None, (), REVERTED.produced, None),
        (None, "worktree missing", recorded, parse_produced(FACTS), "worktree missing"),
    )
    for live, live_error, commits, produced, error in cases:
        results = _derive(
            Status.RUNNING,
            report=report,
            facts=FACTS,
            live=live,
            live_error=live_error,
        )
        assert (results.commits, results.produced, results.live_error) == (
            commits,
            produced,
            error,
        )


def test_only_unfinished_tasks_keep_tracking_their_live_work() -> None:
    cases = (
        (Status.QUEUED, True),
        (Status.RUNNING, True),
        (Status.INTEGRATING, True),
        (Status.ESCALATED, True),
        (Status.DONE, False),
        (Status.CANCELLED, False),
    )
    for status, tracked in cases:
        assert tracks_live_work(status) is tracked
