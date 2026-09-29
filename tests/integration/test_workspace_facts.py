"""Integration tests for `GitFacts` against real git repos and diffs (03 §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import RepoState
from shikiru.process import SubprocessRunner
from shikiru.workspace.facts import GitFacts
from shikiru.workspace.git import Git
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from pathlib import Path

_TRACKERS = ["tasks/plan.md", "tasks/todo.md"]


async def _repo_at_base(tmp_path: Path) -> tuple[Path, str]:
    repo_dir = tmp_path / "repo"
    runner = SubprocessRunner()
    await init_repo(runner, repo_dir)
    (repo_dir / "tasks").mkdir()
    (repo_dir / "tasks" / "plan.md").write_text("- [ ] task\n")
    (repo_dir / "tasks" / "todo.md").write_text("nothing yet\n")
    await commit_all(runner, repo_dir, "initial commit")
    base_sha = await Git(runner).rev_parse(repo_dir, "HEAD")
    return repo_dir, base_sha


async def test_collect_reports_commits_diff_status_and_changed_trackers(tmp_path: Path) -> None:
    repo_dir, base_sha = await _repo_at_base(tmp_path)
    runner = SubprocessRunner()
    (repo_dir / "tasks" / "plan.md").write_text("- [x] task\n")
    (repo_dir / "src.py").write_text("print('hi')\n")
    await commit_all(runner, repo_dir, "do the task")
    (repo_dir / "untracked.txt").write_text("scratch\n")
    repo = RepoState(base=base_sha, base_ref=base_sha, base_sha=base_sha, branch="main")

    facts = await GitFacts(Git(runner)).collect(repo_dir, repo, _TRACKERS)

    assert len(facts.commits) == 1
    assert "src.py" in facts.diff_stat
    assert any(line.endswith("untracked.txt") for line in facts.status)
    assert facts.changed_trackers == ("tasks/plan.md",)


async def test_collect_reports_nothing_when_not_ahead_of_base(tmp_path: Path) -> None:
    repo_dir, base_sha = await _repo_at_base(tmp_path)
    runner = SubprocessRunner()
    repo = RepoState(base=base_sha, base_ref=base_sha, base_sha=base_sha, branch="main")

    facts = await GitFacts(Git(runner)).collect(repo_dir, repo, _TRACKERS)

    assert facts.commits == ()
    assert facts.changed_trackers == ()


async def test_collect_detects_a_tracker_at_a_path_diff_stat_would_elide(
    tmp_path: Path,
) -> None:
    tracker = "a/very/deeply/nested/directory/structure/that/keeps/going/for/a/while/tasks/plan.md"
    repo_dir = tmp_path / "repo"
    runner = SubprocessRunner()
    await init_repo(runner, repo_dir)
    tracker_path = repo_dir / tracker
    tracker_path.parent.mkdir(parents=True)
    tracker_path.write_text("- [ ] task\n")
    await commit_all(runner, repo_dir, "initial commit")
    base_sha = await Git(runner).rev_parse(repo_dir, "HEAD")
    tracker_path.write_text("- [x] task\n")
    await commit_all(runner, repo_dir, "update plan")
    repo = RepoState(base=base_sha, base_ref=base_sha, base_sha=base_sha, branch="main")

    diff_stat = await Git(runner).diff_stat(repo_dir, f"{base_sha}...HEAD")
    assert tracker not in diff_stat, "test setup no longer reproduces --stat's elision"

    facts = await GitFacts(Git(runner)).collect(repo_dir, repo, [tracker])

    assert facts.changed_trackers == (tracker,)
