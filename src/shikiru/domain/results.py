"""Pure derivation of a task's Results from recorded data: no git call, nothing guessed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from shikiru.domain.briefing import facts_commit_lines, parse_produced
from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.domain.briefing import ProducedNumbers, PullRequestLine, ReportedTests
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
class PlanChange:
    """One recorded scope change: what changed and the tasks it created as (id, title or None)."""

    scope_changes: tuple[str, ...]
    new_tasks: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class TaskResults:
    """What a task produced; `more_commits` counts the commits beyond the capped list."""

    outcome: ResultOutcome
    branch: str | None
    base: str | None
    commits: tuple[ResultCommit, ...]
    more_commits: int
    produced: ProducedNumbers | None
    tests: ReportedTests | None
    pull_request: PullRequestLine | None
    plan_changes: tuple[PlanChange, ...]
    residual_risks: tuple[str, ...]


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


def new_task_ids(scope_events: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Return the distinct ids of the tasks the recorded scope changes created, sorted."""
    return tuple(
        sorted(
            {task_id for data in scope_events for task_id in _strings(data.get(NEW_TASK_IDS_KEY))}
        )
    )


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
    commit_cap: int,
) -> TaskResults:
    """Derive the Results of a task from its recorded events, reports and facts snapshot.

    Commits come from `TASK_DONE` for a Done task, else the latest report, else the facts; the
    first source with commits wins. Residual risks come from `TASK_DONE` when it recorded them,
    else from the latest verdict. A new task missing from `titles` keeps its id with no title.
    """
    done_data = done or {}
    candidates = (
        tuple(_from_oneline(line) for line in _strings(done_data.get(DONE_COMMITS_KEY))),
        tuple(
            ResultCommit(commit.sha[:SHORT_SHA_LENGTH], commit.subject) for commit in report_commits
        ),
        tuple(_from_oneline(line) for line in facts_commit_lines(facts)),
    )
    commits = next((found for found in candidates if found), ())
    return TaskResults(
        outcome=_outcome(status),
        branch=branch,
        base=base,
        commits=commits[:commit_cap],
        more_commits=max(len(commits) - commit_cap, 0),
        produced=parse_produced(facts),
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
        residual_risks=_strings(done_data.get(DONE_RISKS_KEY)) or tuple(verdict_risks),
    )
