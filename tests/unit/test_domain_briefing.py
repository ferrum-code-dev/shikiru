from __future__ import annotations

from shikiru.domain.briefing import (
    FACTS_COMMITS_HEADING,
    FACTS_DIFF_STAT_HEADING,
    FACTS_NONE_MARKER,
    BriefingAction,
    ProducedNumbers,
    PullRequestLine,
    ReportedTests,
    VerdictCounts,
    briefing_actions,
    escalation_remaining,
    parse_produced,
    pull_request_line,
    reported_tests,
    verdict_counts,
)
from shikiru.domain.reports import Issue, Verdict
from shikiru.domain.reports import TestRun as AgentTestRun
from shikiru.domain.tasks import Status

_STAT = " a.py | 3 ++-\n 2 files changed, 120 insertions(+), 34 deletions(-)"


def _facts(commits: str, diff: str) -> str:
    sections = (FACTS_COMMITS_HEADING, commits, FACTS_DIFF_STAT_HEADING, diff, "### status")
    return "\n\n".join(sections)


def test_escalation_remaining_counts_rejections_left_on_a_rung_that_can_still_escalate() -> None:
    cases = (
        (0, 2, True, None),
        (1, 2, True, 1),
        (1, 3, True, 2),
        (2, 3, True, 1),
        (1, 2, False, None),
        (2, 2, True, None),
    )
    for rejections, escalate_after, can_escalate, expected in cases:
        assert (
            escalation_remaining(rejections, escalate_after, can_escalate=can_escalate) == expected
        )


def test_verdict_and_test_counts_tally_by_severity_and_result() -> None:
    verdict = Verdict(
        decision="changes_requested",
        summary="s",
        issues=[Issue(severity=name, description="d") for name in ("major", "minor", "major")],
    )
    assert verdict_counts(verdict) == VerdictCounts("changes_requested", 0, 2, 1)
    assert verdict_counts(Verdict(decision="accept", summary="s")) == VerdictCounts(
        "accept", 0, 0, 0
    )
    runs = [
        AgentTestRun(name=str(index), result=result)
        for index, result in enumerate(("passed", "failed", "passed", "skipped"))
    ]
    assert reported_tests(runs) == ReportedTests(passed=2, failed=1, skipped=1)
    assert reported_tests([]) == ReportedTests(0, 0, 0)


def test_parse_produced_reads_the_commit_list_and_diff_summary_or_nothing() -> None:
    cases: tuple[tuple[str | None, ProducedNumbers | None], ...] = (
        (_facts("abc one\ndef two\nghi three", _STAT), ProducedNumbers(3, 2, 120, 34)),
        (_facts("abc one", " 1 file changed, 5 insertions(+)"), ProducedNumbers(1, 1, 5, 0)),
        (_facts("abc one", " 1 file changed, 1 deletion(-)"), ProducedNumbers(1, 1, 0, 1)),
        (_facts(FACTS_NONE_MARKER, FACTS_NONE_MARKER), ProducedNumbers(0, 0, 0, 0)),
        (_facts("abc one", "garbled output"), None),
        ("branch b\nworking tree clean\n", None),
        (None, None),
    )
    for facts, expected in cases:
        assert parse_produced(facts) == expected


def test_pull_request_line_reads_state_number_and_only_recorded_checks() -> None:
    cases: tuple[tuple[dict[str, object], PullRequestLine], ...] = (
        (
            {"url": "https://h/o/r/pull/41"},
            PullRequestLine("OPEN", "https://h/o/r/pull/41", 41, None, None),
        ),
        (
            {"url": "https://h/pr/82", "state": "CLOSED"},
            PullRequestLine("CLOSED", "https://h/pr/82", None, None, None),
        ),
        (
            {"url": "https://h/o/r/pull/7", "checks_pending": True, "checks_failing": False},
            PullRequestLine("OPEN", "https://h/o/r/pull/7", 7, True, False),
        ),
        ({}, PullRequestLine("OPEN", None, None, None, None)),
    )
    for data, expected in cases:
        assert pull_request_line(data) == expected


def test_a_done_task_has_its_pull_request_merged() -> None:
    line = pull_request_line({"url": "https://h/o/r/pull/41"}, task_done=True)

    assert line == PullRequestLine("MERGED", "https://h/o/r/pull/41", 41, None, None)


def test_briefing_verbs_follow_the_task_state_and_never_exceed_three() -> None:
    take, hand, message = BriefingAction.TAKE_OVER, BriefingAction.HAND_BACK, BriefingAction.MESSAGE
    pause, resume, answer = BriefingAction.PAUSE, BriefingAction.RESUME, BriefingAction.ANSWER
    cases = (
        (Status.RUNNING, (), False, (take, message, pause)),
        (Status.INTEGRATING, (), False, (take, message, pause)),
        (Status.RUNNING, ("paused",), False, (take, message, resume)),
        (Status.RUNNING, ("intervention",), True, (hand, message)),
        (Status.RUNNING, ("paused", "intervention"), True, (hand, message, resume)),
        (Status.QUEUED, (), False, (message, pause)),
        (Status.ESCALATED, (), False, (answer, message, pause)),
        (Status.ESCALATED, ("paused",), True, (answer, hand, message)),
        (Status.DONE, (), False, ()),
        (Status.CANCELLED, ("paused",), True, ()),
    )
    for status, holds, owned, expected in cases:
        assert briefing_actions(status, holds, operator_owned=owned) == expected
