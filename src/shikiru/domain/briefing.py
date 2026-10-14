"""Pure derivations behind the task briefing: rework, verdict, produced work, tests and PR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Hold, Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.reports import TestRun, Verdict

FACTS_COMMITS_HEADING = "### Commits (base..HEAD)"
FACTS_DIFF_STAT_HEADING = "### Diff stat (base...HEAD)"
FACTS_NONE_MARKER = "(none)"
PR_OPEN_STATE = "OPEN"
PR_URL_KEY = "url"
PR_STATE_KEY = "state"
PR_CHECKS_PENDING_KEY = "checks_pending"
PR_CHECKS_FAILING_KEY = "checks_failing"
MAX_BRIEFING_ACTIONS = 3
FIRST_PASS_ITERATION = 1
LAST_WORDS_LIMIT = 160
LAST_WORDS_ELLIPSIS = "…"
_FACTS_SECTION_BREAK = "\n\n"
_FACTS_PARTS = 4
_DIFF_SUMMARY = re.compile(
    r"(?P<files>\d+) files? changed"
    r"(?:, (?P<insertions>\d+) insertions?\(\+\))?"
    r"(?:, (?P<deletions>\d+) deletions?\(-\))?"
)
_PR_NUMBER = re.compile(r"/pull/(?P<number>\d+)/?$")


class BriefingAction(StrEnum):
    """A briefing verb, declared in offer order; each value is the palette action it opens."""

    ANSWER = "resolve"
    TAKE_OVER = "take-over"
    HAND_BACK = "hand-back"
    MESSAGE = "message"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass(frozen=True)
class VerdictCounts:
    """A verdict's decision and its issues counted by severity."""

    decision: str
    blocker: int
    major: int
    minor: int


@dataclass(frozen=True)
class ProducedNumbers:
    """Commits, files and lines recorded in the facts snapshot."""

    commits: int
    files: int
    insertions: int
    deletions: int


@dataclass(frozen=True)
class ReportedTests:
    """Agent-reported test results by outcome."""

    passed: int
    failed: int
    skipped: int


@dataclass(frozen=True)
class PullRequestLine:
    """The recorded pull request; checks are set only when the engine recorded them."""

    state: str
    url: str | None
    number: int | None
    checks_pending: bool | None
    checks_failing: bool | None


def escalation_remaining(
    rejections_on_rung: int, escalate_after: int, *, can_escalate: bool
) -> int | None:
    """Return the rejections left before the Implementer's rung escalates automatically.

    The counter counts rework at one rung; the rejection that makes it reach `escalate_after`
    moves the rung up, so the remainder is only meaningful once a rejection happened and the
    ladder still has a higher rung.
    """
    remaining = escalate_after - rejections_on_rung
    if rejections_on_rung < 1 or not can_escalate or remaining < 1:
        return None
    return remaining


def verdict_counts(verdict: Verdict) -> VerdictCounts:
    """Count `verdict`'s issues by severity beside its decision."""
    severities = [issue.severity for issue in verdict.issues]
    return VerdictCounts(
        verdict.decision,
        severities.count("blocker"),
        severities.count("major"),
        severities.count("minor"),
    )


def reported_tests(tests: Sequence[TestRun]) -> ReportedTests:
    """Count `tests` by their reported result."""
    results = [test.result for test in tests]
    return ReportedTests(results.count("passed"), results.count("failed"), results.count("skipped"))


def parse_produced(facts: str | None) -> ProducedNumbers | None:
    """Read the commit count and diff summary from rendered facts, or None when unreadable.

    An empty diff prints no summary line and renders as `(none)`, which is zero change.
    """
    if facts is None:
        return None
    parts = facts.split(_FACTS_SECTION_BREAK)
    if len(parts) < _FACTS_PARTS:
        return None
    commits_heading, commits_block, diff_heading, diff_block, *_ = parts
    if commits_heading != FACTS_COMMITS_HEADING or diff_heading != FACTS_DIFF_STAT_HEADING:
        return None
    commits = 0 if commits_block == FACTS_NONE_MARKER else len(commits_block.splitlines())
    if diff_block == FACTS_NONE_MARKER:
        return ProducedNumbers(commits, 0, 0, 0)
    summary = _DIFF_SUMMARY.search(diff_block.strip().splitlines()[-1])
    if summary is None:
        return None
    return ProducedNumbers(
        commits,
        int(summary["files"]),
        int(summary["insertions"] or 0),
        int(summary["deletions"] or 0),
    )


def pull_request_line(data: Mapping[str, object]) -> PullRequestLine:
    """Describe the latest recorded pull request event; an open event records no state yet."""
    url = data.get(PR_URL_KEY)
    state = data.get(PR_STATE_KEY)
    link = url if isinstance(url, str) else None
    match = _PR_NUMBER.search(link) if link is not None else None
    pending = data.get(PR_CHECKS_PENDING_KEY)
    failing = data.get(PR_CHECKS_FAILING_KEY)
    return PullRequestLine(
        state=state if isinstance(state, str) else PR_OPEN_STATE,
        url=link,
        number=int(match["number"]) if match is not None else None,
        checks_pending=pending if isinstance(pending, bool) else None,
        checks_failing=failing if isinstance(failing, bool) else None,
    )


def one_line(text: str, limit: int = LAST_WORDS_LIMIT) -> str | None:
    """Collapse `text` to one line of at most `limit` characters, or None when it is blank."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - len(LAST_WORDS_ELLIPSIS)].rstrip() + LAST_WORDS_ELLIPSIS


def briefing_actions(
    status: Status, holds: Sequence[str], *, operator_owned: bool
) -> tuple[BriefingAction, ...]:
    """Return the at most three verbs the briefing offers for a task in this state.

    A decision comes first, then the ownership hand-over, a message, and the pause hold's
    opposite; a finished task offers none. Take over needs a running role to stop, so it is
    only offered while the task runs or integrates. An intervention hold is cleared by handing
    back, so it never offers Resume.
    """
    if status in (Status.DONE, Status.CANCELLED):
        return ()
    verbs: list[BriefingAction] = []
    if status is Status.ESCALATED:
        verbs.append(BriefingAction.ANSWER)
    if operator_owned:
        verbs.append(BriefingAction.HAND_BACK)
    elif status in (Status.RUNNING, Status.INTEGRATING):
        verbs.append(BriefingAction.TAKE_OVER)
    verbs.append(BriefingAction.MESSAGE)
    if Hold.PAUSED.value in holds:
        verbs.append(BriefingAction.RESUME)
    elif not holds:
        verbs.append(BriefingAction.PAUSE)
    return tuple(verbs[:MAX_BRIEFING_ACTIONS])


def rework_progress(iteration: int, max_iterations: int) -> tuple[int, int] | None:
    """Return `(reworks so far, reworks allowed)`, or None until the first rework.

    `iteration` counts implement/verify rounds from 1, so the first pass is not a rework, and
    the round that reaches `max_iterations` is the final one: `max_iterations - 1` reworks fit.
    """
    allowed = max_iterations - FIRST_PASS_ITERATION
    if iteration <= FIRST_PASS_ITERATION or allowed < 1:
        return None
    return iteration - FIRST_PASS_ITERATION, allowed
