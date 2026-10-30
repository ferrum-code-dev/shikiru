"""Pure derivation of a task's Results from recorded data: no git call, nothing guessed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from shikiru.domain.briefing import ProducedNumbers, facts_commit_lines, parse_produced
from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.briefing import PullRequestLine, ReportedTests
    from shikiru.domain.reports import Commit

SHORT_SHA_LENGTH = 7
DONE_COMMITS_KEY = "commits"
DONE_RISKS_KEY = "residual_risks"
SCOPE_CHANGES_KEY = "scope_changes"
NEW_TASK_IDS_KEY = "new_task_ids"


class ResultOutcome(StrEnum):
    """Whether the task's work reached its base branch."""

    MERGED = "merged"
    NOT_MERGED = "not merged"
    SO_FAR = "so far"


@dataclass(frozen=True)
class ResultCommit:
    """A commit as a short hash and its subject."""

    sha: str
    subject: str


@dataclass(frozen=True)
class LiveWork:
    """A task's work as its worktree holds it now: commits past its base and the changes."""

    commits: tuple[ResultCommit, ...]
    produced: ProducedNumbers

    @classmethod
    def from_oneline(
        cls, commit_lines: Sequence[str], *, files: int, insertions: int, deletions: int
    ) -> LiveWork:
        """Build the observation from `git log --oneline` lines and the change totals."""
        commits = tuple(_from_oneline(line) for line in commit_lines)
        return cls(commits, ProducedNumbers(len(commits), files, insertions, deletions))


@dataclass(frozen=True)
class PlanChange:
    """One recorded scope change: what changed and the tasks it created as (id, title or None)."""

    scope_changes: tuple[str, ...]
    new_tasks: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class TaskResults:
    """What a task produced; `more_commits` counts the commits beyond the capped list.

    `live_error` is set when the task's current Git state could not be read, so the commits and
    numbers shown are the recorded ones and must be labelled as such.
    """

    outcome: ResultOutcome
    branch: str | None
    base: str | None
    commits: tuple[ResultCommit, ...]
    more_commits: int
    produced: ProducedNumbers | None
    tests: ReportedTests | None
    pull_request: PullRequestLine | None
    plan_changes: tuple[PlanChange, ...]
    plan_summary: str | None
    residual_risks: tuple[str, ...]
    live_error: str | None


def _strings(value: object) -> tuple[str, ...]:
    items = cast("list[object]", value) if isinstance(value, list) else []
    return tuple(item for item in items if isinstance(item, str))


def _from_oneline(line: str) -> ResultCommit:
    sha, _, subject = line.partition(" ")
    return ResultCommit(sha[:SHORT_SHA_LENGTH], subject.strip())


def _outcome(status: Status) -> ResultOutcome:
    if status is Status.DONE:
        return ResultOutcome.MERGED
    return ResultOutcome.NOT_MERGED if status is Status.CANCELLED else ResultOutcome.SO_FAR


def tracks_live_work(status: Status) -> bool:
    """Whether a task still shows live work; a finished one keeps its recorded Results."""
    return status not in (Status.DONE, Status.CANCELLED)


def new_task_ids(scope_events: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Return the distinct ids of the tasks the recorded scope changes created, sorted."""
    return tuple(
        sorted(
            {task_id for data in scope_events for task_id in _strings(data.get(NEW_TASK_IDS_KEY))}
        )
    )


def _residual_risks(done: Mapping[str, object], verdict_risks: Sequence[str]) -> tuple[str, ...]:
    if DONE_RISKS_KEY in done:
        return _strings(done[DONE_RISKS_KEY])
    return tuple(verdict_risks)


def derive_task_results(
    status: Status,
    *,
    branch: str | None,
    base: str | None,
    done: Mapping[str, object] | None,
    report_commits: Sequence[Commit],
    facts: str | None,
    tests: ReportedTests | None,
    pull_request: PullRequestLine | None,
    scope_events: Sequence[Mapping[str, object]],
    titles: Mapping[str, str],
    verdict_risks: Sequence[str],
    plan_summary: str | None,
    commit_cap: int,
    live: LiveWork | None = None,
    live_error: str | None = None,
) -> TaskResults:
    """Derive the Results of a task from its recorded events, reports and facts snapshot.

    A live observation of the task's worktree (`live`) is authoritative for its commits and
    produced numbers, even when it finds none, so a report or facts snapshot that predates a
    commit or revert never shows. Without one, commits come from `TASK_DONE` for a Done task,
    else the latest report, else the facts; the first source with commits wins, and the numbers
    come from the facts. Residual risks come from `TASK_DONE` when it recorded a risk list, even
    an empty one, else from `verdict_risks`: the verdict of the task's current iteration. A new
    task missing from `titles` keeps its id with no title.
    """
    done_data = done or {}
    if live is not None:
        commits, produced = live.commits, live.produced
    else:
        candidates = (
            tuple(_from_oneline(line) for line in _strings(done_data.get(DONE_COMMITS_KEY))),
            tuple(
                ResultCommit(commit.sha[:SHORT_SHA_LENGTH], commit.subject)
                for commit in report_commits
            ),
            tuple(_from_oneline(line) for line in facts_commit_lines(facts)),
        )
        commits = next((found for found in candidates if found), ())
        produced = parse_produced(facts)
    return TaskResults(
        outcome=_outcome(status),
        branch=branch,
        base=base,
        commits=commits[:commit_cap],
        more_commits=max(len(commits) - commit_cap, 0),
        produced=produced,
        tests=tests,
        pull_request=pull_request,
        plan_changes=tuple(
            PlanChange(
                _strings(data.get(SCOPE_CHANGES_KEY)),
                tuple(
                    (task_id, titles.get(task_id))
                    for task_id in _strings(data.get(NEW_TASK_IDS_KEY))
                ),
            )
            for data in scope_events
        ),
        plan_summary=plan_summary,
        residual_risks=_residual_risks(done_data, verdict_risks),
        live_error=live_error,
    )
