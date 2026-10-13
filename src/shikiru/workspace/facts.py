"""Git facts for `facts.md`, outranking the Implementer's claims (03 §2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shikiru.domain.briefing import (
    FACTS_COMMITS_HEADING,
    FACTS_DIFF_STAT_HEADING,
    FACTS_NONE_MARKER,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.domain.tasks import RepoState
    from shikiru.workspace.git import Git


@dataclass(frozen=True, slots=True)
class WorkspaceFacts:
    """The facts computed for a task's worktree."""

    head_branch: str | None = None
    commits: tuple[str, ...] = field(default_factory=tuple)
    diff_stat: str = ""
    status: tuple[str, ...] = field(default_factory=tuple)
    changed_trackers: tuple[str, ...] = field(default_factory=tuple)


DETACHED_HEAD = "a detached HEAD"
OFF_BRANCH_ISSUE = (
    "The workspace is on {actual}, not its task branch {branch}, so its commits "
    "({commits}) would never be integrated. Bring that work onto {branch}, switch back to it "
    "and stay on it."
)


def describe_off_branch(branch: str, head_branch: str | None, commits: Sequence[str]) -> str:
    """Describe a worktree whose `HEAD` is not on `branch`, naming the commits at risk (03 §6)."""
    return OFF_BRANCH_ISSUE.format(
        actual=head_branch or DETACHED_HEAD,
        branch=branch,
        commits="; ".join(commits) or "none ahead of the base",
    )


def render_facts(facts: WorkspaceFacts) -> str:
    """Render `facts` as the markdown text a `verify`/`conflicts`/`handoff` prompt embeds (03 §2).

    These facts outrank the Implementer's claims, so they carry no interpretation, only the
    raw `git` output `GitFacts.collect` gathered.
    """
    lines = [FACTS_COMMITS_HEADING]
    lines.append("\n".join(facts.commits) if facts.commits else FACTS_NONE_MARKER)
    lines.append(FACTS_DIFF_STAT_HEADING)
    lines.append(facts.diff_stat or FACTS_NONE_MARKER)
    lines.append("### git status --porcelain")
    lines.append("\n".join(facts.status) if facts.status else "(clean)")
    lines.append("### Changed trackers")
    lines.append(", ".join(facts.changed_trackers) if facts.changed_trackers else "(none)")
    return "\n\n".join(lines)


class GitFacts:
    """Computes `WorkspaceFacts` for a task's worktree, from real git state."""

    def __init__(self, git: Git) -> None:
        """Read git state through `git`."""
        self._git = git

    async def collect(
        self, task_root: Path, repo: RepoState, trackers: Sequence[str]
    ) -> WorkspaceFacts:
        """Collect facts for the worktree at `task_root`, checked out as `repo`."""
        head_branch = await self._git.head_branch(task_root)
        diff_range = f"{repo.base_ref}...HEAD"
        commits = await self._git.log_oneline(task_root, f"{repo.base_ref}..HEAD")
        diff_stat = await self._git.diff_stat(task_root, diff_range)
        status = await self._git.status_porcelain(task_root)
        changed_paths = set(await self._git.diff_name_only(task_root, diff_range))
        return WorkspaceFacts(
            head_branch=head_branch,
            commits=tuple(commits),
            diff_stat=diff_stat,
            status=tuple(status),
            changed_trackers=tuple(tracker for tracker in trackers if tracker in changed_paths),
        )
