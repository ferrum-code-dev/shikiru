"""Integration coverage for the task Git diff popup with real git and the dashboard."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from rich.text import Text
from textual.containers import ScrollableContainer
from textual.widgets import Button, Select, Static

from shikiru.dashboard.actions_list import ActionsListScreen
from shikiru.dashboard.contract import DIFF_ALL_COMMITS, PaletteRequest
from shikiru.dashboard.detail import (
    TASK_DETAIL_BRIEFING,
    TASK_DETAIL_IDENTITY,
    TASK_VIEW_REQUEST,
    TASK_VIEW_RESULTS,
    TaskDetailPanel,
)
from shikiru.dashboard.detail_fold import DetailFold
from shikiru.dashboard.diff import (
    DIFF_COMMIT_SELECT,
    DIFF_FILE_SELECT,
    DIFF_FULLSCREEN,
    DIFF_SCOPE,
    DIFF_TEXT,
    DiffScreen,
    scope_text,
)
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import RepoState, Status
from shikiru.paths import task_root
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.store.reader import StateReader
from shikiru.store.tasks import TaskStore
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.tokens import DUSTY_ROSE, PRIMARY_TEXT_COLOR, SAGE, STEEL_BLUE
from shikiru.workspace.git import Git
from tests.support.actions import ScreenHost, is_composed, open_row, structure
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import ManualFocusHost, widget_context
from tests.support.diff import task_diff_service
from tests.support.escalations import operator_services
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo, run_git
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.app import App

    from tests.support.store import Connection, Database

START = datetime(2026, 1, 1, tzinfo=UTC)
APP_SIZE = (100, 40)
BASE_BRANCH = "main"
TASK_BRANCH = "shikiru/diff-probe"


@pytest.fixture
def runner() -> SubprocessRunner:
    return SubprocessRunner()


async def _live_task(
    connection: Connection,
    clock: FakeClock,
    project_root: Path,
    runner: SubprocessRunner,
) -> tuple[str, Path]:
    task = seed_task(connection, clock, title="Diff probe")
    worktree = task_root(project_root, task.id)
    await init_repo(runner, worktree)
    (worktree / "app.py").write_text("one\n")
    await commit_all(runner, worktree, "base")
    base_sha = await run_git(runner, worktree, "rev-parse", "HEAD")
    await run_git(runner, worktree, "checkout", "-b", TASK_BRANCH)
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    return task.id, worktree


async def test_live_all_changes_include_uncommitted_while_a_commit_excludes_them(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "app.py").write_text("one\ntwo\n")
    await commit_all(runner, worktree, "add two")
    (worktree / "app.py").write_text("one\ntwo\nthree\n")
    service = task_diff_service(reader, project_root, clock)

    all_view = await service.load(task_id, DIFF_ALL_COMMITS, None)
    assert all_view.live is True
    assert all_view.includes_uncommitted is True
    assert all_view.files == ("app.py",)
    assert "+three" in all_view.patch
    assert "+two" in all_view.patch
    assert scope_text(all_view) == "Changes so far · includes uncommitted changes"

    short = all_view.commits[0].sha
    single = await service.load(task_id, short, None)
    assert single.live is True
    assert single.includes_uncommitted is False
    assert "+three" not in single.patch
    assert "+two" in single.patch
    assert (
        scope_text(single) == "Changes so far · committed only (uncommitted changes not included)"
    )


async def test_changed_unicode_path_is_preserved_for_file_selection(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    path = "café notes [draft].py"
    (worktree / path).write_text("special path change\n")
    await commit_all(runner, worktree, "add special path")
    service = task_diff_service(reader, project_root, clock)

    all_changes = await service.load(task_id, DIFF_ALL_COMMITS, None)
    selected_file = await service.load(task_id, DIFF_ALL_COMMITS, path)

    assert all_changes.files == (path,)
    assert selected_file.files == (path,)
    assert "+special path change" in selected_file.patch


async def test_diff_file_selection_treats_pathspec_metacharacters_literally(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "a.txt").write_text("pathspec match\n")
    path = "[ab].txt"
    (worktree / path).write_text("literal target\n")
    await commit_all(runner, worktree, "add bracketed and matching paths")

    view = await task_diff_service(reader, project_root, clock).load(
        task_id, DIFF_ALL_COMMITS, path
    )

    assert view.error is None
    assert "a.txt" in view.files
    assert path in view.files
    assert "+literal target" in view.patch
    assert "+pathspec match" not in view.patch


@pytest.mark.parametrize(
    "case",
    [("one\n", ()), ("one\ntwo\n", ("app.py",))],
    ids=["identical", "changed"],
)
async def test_live_staged_deletion_replacement_compares_against_base(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
    case: tuple[str, tuple[str, ...]],
) -> None:
    replacement, expected_files = case
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    await run_git(runner, worktree, "rm", "app.py")
    (worktree / "app.py").write_text(replacement)

    view = await task_diff_service(reader, project_root, clock).load(
        task_id, DIFF_ALL_COMMITS, None
    )

    assert view.error is None
    assert view.files == expected_files
    assert "-one" not in view.patch.splitlines()


@pytest.mark.parametrize(
    "case",
    [("caf\xe8\n".encode("latin-1"), 0o644), (b"changed\n", 0o000)],
    ids=["invalid-utf8", "unreadable"],
)
async def test_a_replacement_that_cannot_be_shown_does_not_block_other_files(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
    case: tuple[bytes, int],
) -> None:
    content, mode = case
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    for name in ("broken.txt", "same.txt"):
        (worktree / name).write_bytes("caf\xe9\n".encode("latin-1"))
    await commit_all(runner, worktree, "add text")
    await run_git(runner, worktree, "checkout", "-B", BASE_BRANCH)
    await run_git(runner, worktree, "checkout", "-B", TASK_BRANCH)
    await run_git(runner, worktree, "rm", "--cached", "broken.txt", "same.txt")
    (worktree / "broken.txt").write_bytes(content)
    (worktree / "broken.txt").chmod(mode)
    (worktree / "valid.txt").write_text("valid\n")
    service = task_diff_service(reader, project_root, clock)

    first = await service.load(task_id, DIFF_ALL_COMMITS, None)
    valid = await service.load(task_id, DIFF_ALL_COMMITS, "valid.txt")

    assert first.files == ("broken.txt", "valid.txt")
    assert first.error is not None
    assert first.patch == ""
    assert valid.files == first.files
    assert valid.error is None
    assert "+valid" in valid.patch.splitlines()


async def test_live_untracked_and_binary_files_appear_in_all_changes(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "notes.txt").write_text("hello\n")
    (worktree / "asset.bin").write_bytes(b"\x00\x01\x02")
    outside = tmp_path / "outside.txt"
    outside.write_text("private content\n")
    (worktree / "link").symlink_to(outside)
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task_id, DIFF_ALL_COMMITS, "asset.bin")
    assert view.files == ("asset.bin", "link", "notes.txt")
    assert "Binary files /dev/null and b/asset.bin differ" in view.patch
    notes = await service.load(task_id, DIFF_ALL_COMMITS, "notes.txt")
    assert "+hello" in notes.patch.splitlines()
    link = await service.load(task_id, DIFF_ALL_COMMITS, "link")
    assert f"+{outside}" in link.patch.splitlines()
    assert "private content" not in link.patch


async def test_changed_binary_file_shows_a_notice_instead_of_encoded_content(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "asset.bin").write_bytes(b"\x00\x01")
    await commit_all(runner, worktree, "add asset")
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task_id, DIFF_ALL_COMMITS, None)

    assert "Binary files /dev/null and b/asset.bin differ" in view.patch
    assert "GIT binary patch" not in view.patch


async def test_renamed_file_lists_both_the_removed_and_the_added_path(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    await run_git(runner, worktree, "mv", "app.py", "renamed.py")
    await commit_all(runner, worktree, "rename")
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task_id, DIFF_ALL_COMMITS, "app.py")

    assert view.files == ("app.py", "renamed.py")
    assert "-one" in view.patch.splitlines()


async def test_text_that_is_not_utf8_is_a_visible_error_and_other_files_stay_selectable(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "latin.txt").write_bytes("caf\xe9\n".encode("latin-1"))
    (worktree / "plain.txt").write_text("plain\n")
    service = task_diff_service(reader, project_root, clock)

    broken = await service.load(task_id, DIFF_ALL_COMMITS, "latin.txt")
    assert broken.error is not None
    assert "not valid UTF-8" in broken.error
    assert broken.files == ("latin.txt", "plain.txt")
    plain = await service.load(task_id, DIFF_ALL_COMMITS, "plain.txt")
    assert plain.error is None
    assert "+plain" in plain.patch.splitlines()


async def test_worktree_that_lost_its_git_link_never_shows_the_project_changes(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    await init_repo(runner, project_root)
    (project_root / "unrelated.py").write_text("base\n")
    await commit_all(runner, project_root, "base")
    task = seed_task(connection, clock, title="Leftover directory")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha="0" * 40, branch=TASK_BRANCH),
    )
    task_root(project_root, task.id).mkdir(parents=True)
    (project_root / "unrelated.py").write_text("changed\n")

    view = await task_diff_service(reader, project_root, clock).load(
        task.id, DIFF_ALL_COMMITS, None
    )

    assert view.error is not None
    assert "task worktree is missing" in view.error
    assert view.files == ()


async def test_live_empty_worktree_shows_no_changes(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = await _live_task(connection, clock, project_root, runner)
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task_id, DIFF_ALL_COMMITS, None)
    assert view.error is None
    assert view.commits == ()
    assert view.files == ()
    assert view.patch == ""


def _record_landed(
    connection: Connection,
    clock: FakeClock,
    task_id: str,
    lines: list[str],
    *,
    integration: str,
    base_sha: str,
    branch_sha: str | None = None,
    landing_sha: str | None = None,
) -> None:
    if integration in ("merge", "squash"):
        EventLog(connection, clock).append(
            EventKind.INTEGRATION_APPLYING,
            task_id=task_id,
            message="integration applying",
            data={
                "base_sha": base_sha,
                "commits": lines,
                "integration": integration,
                "landing_sha": landing_sha or (lines[0].partition(" ")[0] if lines else ""),
            },
        )
    elif branch_sha is not None:
        EventLog(connection, clock).append(
            EventKind.PR_OPENED,
            task_id=task_id,
            message="pull request opened",
            data={"base_sha": base_sha, "branch_sha": branch_sha},
        )
    current = TaskStore(connection, clock).get(task_id)
    assert current is not None
    TaskStore(connection, clock).finish(
        task_id,
        Transition.stay(current, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={
            "commits": lines,
            "integration": integration,
            "changes": {"files": 1, "insertions": 2, "deletions": 0},
            "summary": "done",
        },
    )


async def _landed_task(
    connection: Connection,
    clock: FakeClock,
    project_root: Path,
    runner: SubprocessRunner,
    *,
    integration: str,
) -> tuple[str, list[str]]:
    if not (project_root / ".git").exists():
        await init_repo(runner, project_root)
        (project_root / ".gitignore").write_text(".shikiru/\nstate.db*\n")
        (project_root / "module.py").write_text("one\n")
        await commit_all(runner, project_root, "base module")
    base_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    task = seed_task(connection, clock, title=f"Landed {integration}")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    lines: list[str] = []
    if integration == "squash":
        (project_root / "module.py").write_text("one\ntwo\nthree\n")
        await commit_all(runner, project_root, "squash work branch tip")
        tip_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
        await run_git(runner, project_root, "checkout", "-B", BASE_BRANCH, base_sha)
        tree = await run_git(runner, project_root, "rev-parse", f"{tip_sha}^{{tree}}")
        squash_sha = await run_git(
            runner, project_root, "commit-tree", tree, "-p", base_sha, "-m", "Squashed (T-1)"
        )
        await run_git(runner, project_root, "update-ref", f"refs/heads/{BASE_BRANCH}", squash_sha)
        lines = await git.log_oneline(project_root, f"{base_sha}..HEAD")
    else:
        (project_root / "module.py").write_text("one\ntwo\n")
        await commit_all(runner, project_root, "landed first")
        (project_root / "module.py").write_text("one\ntwo\nthree\n")
        await commit_all(runner, project_root, "landed second")
        lines = await git.log_oneline(project_root, f"{base_sha}..HEAD")
    branch_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    _record_landed(
        connection,
        clock,
        task.id,
        lines,
        integration=integration,
        base_sha=base_sha,
        branch_sha=branch_sha,
        landing_sha=branch_sha,
    )
    return task.id, lines


@pytest.mark.parametrize("integration", ["merge", "squash", "pr"])
async def test_landed_tasks_show_their_own_commits_after_cleanup(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
    integration: str,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, lines = await _landed_task(
        connection, clock, project_root, runner, integration=integration
    )
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task_id, DIFF_ALL_COMMITS, None)
    assert view.live is False
    assert view.integration == integration
    assert [commit.subject for commit in view.commits] == [line.partition(" ")[2] for line in lines]
    assert view.files == ("module.py",)
    assert "+two" in view.patch
    assert "+three" in view.patch
    label = {
        "merge": "Landed as merge",
        "squash": "Landed as squash",
        "pr": "Landed as pull request",
    }[integration]
    assert scope_text(view) == label

    newest = view.commits[0].sha
    single = await service.load(task_id, newest, None)
    assert single.files == ("module.py",)
    if integration == "squash":
        assert "+two" in single.patch
    else:
        assert "+two" not in single.patch
        assert "+three" in single.patch


async def test_done_without_landed_evidence_is_unavailable_not_substituted(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Old record")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha="0" * 40, branch=TASK_BRANCH),
    )
    current = TaskStore(connection, clock).get(task.id)
    assert current is not None
    TaskStore(connection, clock).finish(
        task.id,
        Transition.stay(current, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={"commits": ["abc1234 old work"], "summary": "done"},
    )
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task.id, DIFF_ALL_COMMITS, None)
    assert view.error is not None
    assert "no landed changes recorded" in view.error
    assert view.files == ()
    assert view.patch == ""


async def test_missing_git_object_is_visible(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    if not (project_root / ".git").exists():
        await init_repo(runner, project_root)
        (project_root / "a.txt").write_text("x\n")
        await commit_all(runner, project_root, "base")
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Missing object")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha="0" * 40, branch=TASK_BRANCH),
    )
    current = TaskStore(connection, clock).get(task.id)
    assert current is not None
    TaskStore(connection, clock).finish(
        task.id,
        Transition.stay(current, step=None, status=Status.DONE),
        EventKind.TASK_DONE,
        message="done",
        data={
            "commits": ["deadbeef missing object"],
            "integration": "merge",
            "changes": {"files": 1, "insertions": 1, "deletions": 0},
        },
    )
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task.id, DIFF_ALL_COMMITS, None)
    assert view.error is not None
    assert "Git evidence unavailable" in view.error


async def test_missing_worktree_and_invalid_commit_are_visible(
    connection: Connection, reader: StateReader, tmp_path: Path, runner: SubprocessRunner
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Missing worktree")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha="0" * 40, branch=TASK_BRANCH),
    )
    service = task_diff_service(reader, project_root, clock)

    missing = await service.load(task.id, DIFF_ALL_COMMITS, None)
    assert missing.error is not None
    assert "task worktree is missing" in missing.error

    live_id, _ = await _live_task(connection, clock, project_root, runner)
    invalid = await service.load(live_id, "1234567", None)
    assert invalid.error is not None
    assert "is not part of this task" in invalid.error


async def test_pr_merge_shows_only_the_task_changes_when_the_target_advanced(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    await init_repo(runner, project_root)
    (project_root / ".gitignore").write_text(".shikiru/\nstate.db*\n")
    (project_root / "module.py").write_text("one\n")
    await commit_all(runner, project_root, "base module")
    base_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "checkout", "-b", TASK_BRANCH)
    (project_root / "task.py").write_text("task\n")
    await commit_all(runner, project_root, "task work")
    await run_git(runner, project_root, "checkout", BASE_BRANCH)
    (project_root / "target.py").write_text("target\n")
    await commit_all(runner, project_root, "target advanced")
    before = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "merge", "--no-ff", "-m", "Merge pull request", TASK_BRANCH)
    merge_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    lines = await git.log_oneline(project_root, f"{before}..{merge_sha}")
    task = seed_task(connection, clock, title="Divergent pull request")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    _record_landed(connection, clock, task.id, lines, integration="pr", base_sha=base_sha)
    service = task_diff_service(reader, project_root, clock)

    view = await service.load(task.id, DIFF_ALL_COMMITS, None)
    assert view.files == ("task.py",)
    assert "+task" in view.patch
    assert "target" not in view.patch
    assert [commit.subject for commit in view.commits] == ["task work"]

    single = await service.load(task.id, view.commits[0].sha, None)
    assert single.files == ("task.py",)
    assert "+task" in single.patch


async def test_local_merge_uses_recorded_base_for_non_linear_task_history(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    await init_repo(runner, project_root)
    (project_root / ".gitignore").write_text(".shikiru/\nstate.db*\n")
    (project_root / "base.py").write_text("base\n")
    await commit_all(runner, project_root, "base")
    base_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "checkout", "-b", TASK_BRANCH)
    (project_root / "task.py").write_text("task change\n")
    await commit_all(runner, project_root, "task work")
    await run_git(runner, project_root, "checkout", "-b", "side-work", base_sha)
    (project_root / "side.py").write_text("side change\n")
    await commit_all(runner, project_root, "side work")
    await run_git(runner, project_root, "checkout", TASK_BRANCH)
    await run_git(runner, project_root, "merge", "--no-ff", "-m", "merge side work", "side-work")
    lines = await Git(runner, command_timeout=GIT_COMMAND_TIMEOUT).log_oneline(
        project_root, f"{base_sha}..HEAD"
    )
    task = seed_task(connection, clock, title="Non-linear local landing")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    _record_landed(
        connection,
        clock,
        task.id,
        lines,
        integration="merge",
        base_sha=base_sha,
        landing_sha=await run_git(runner, project_root, "rev-parse", "HEAD"),
    )
    service = task_diff_service(reader, project_root, clock)
    view = await service.load(task.id, DIFF_ALL_COMMITS, None)

    assert view.error is None
    assert view.files == ("side.py", "task.py")
    assert "+side change" in view.patch
    task_file = await service.load(task.id, DIFF_ALL_COMMITS, "task.py")
    assert "+task change" in task_file.patch


async def test_pr_task_merge_commit_matches_full_recorded_branch_sha(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    await init_repo(runner, project_root)
    (project_root / ".gitignore").write_text(".shikiru/\nstate.db*\n")
    (project_root / "base.py").write_text("base\n")
    await commit_all(runner, project_root, "base")
    base_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "checkout", "-b", TASK_BRANCH)
    (project_root / "task.py").write_text("task change\n")
    await commit_all(runner, project_root, "task work")
    await run_git(runner, project_root, "checkout", "-b", "side-work", base_sha)
    (project_root / "side.py").write_text("side change\n")
    await commit_all(runner, project_root, "side work")
    await run_git(runner, project_root, "checkout", TASK_BRANCH)
    await run_git(runner, project_root, "merge", "--no-ff", "-m", "merge side work", "side-work")
    branch_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    lines = await Git(runner, command_timeout=GIT_COMMAND_TIMEOUT).log_oneline(
        project_root, f"{base_sha}..{branch_sha}"
    )
    task = seed_task(connection, clock, title="PR task merge commit")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    _record_landed(
        connection,
        clock,
        task.id,
        lines,
        integration="pr",
        base_sha=base_sha,
        branch_sha=branch_sha,
    )
    view = await task_diff_service(reader, project_root, clock).load(
        task.id, DIFF_ALL_COMMITS, None
    )

    assert view.error is None
    assert view.files == ("side.py", "task.py")
    assert "+side change" in view.patch
    task_file = await task_diff_service(reader, project_root, clock).load(
        task.id, DIFF_ALL_COMMITS, "task.py"
    )
    assert "+task change" in task_file.patch
    assert view.commits[0].subject == "merge side work"


async def test_squash_merged_pr_excludes_target_changes_made_after_branching(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    runner: SubprocessRunner,
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    await init_repo(runner, project_root)
    (project_root / ".gitignore").write_text(".shikiru/\nstate.db*\n")
    (project_root / "base.py").write_text("base\n")
    await commit_all(runner, project_root, "base")
    base_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "checkout", "-b", TASK_BRANCH)
    (project_root / "task.py").write_text("task change\n")
    await commit_all(runner, project_root, "task work")
    branch_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "checkout", BASE_BRANCH)
    (project_root / "target.py").write_text("target change\n")
    await commit_all(runner, project_root, "target advanced")
    target_sha = await run_git(runner, project_root, "rev-parse", "HEAD")
    await run_git(runner, project_root, "merge", "--squash", TASK_BRANCH)
    await commit_all(runner, project_root, "squash pull request")
    lines = await Git(runner, command_timeout=GIT_COMMAND_TIMEOUT).log_oneline(
        project_root, f"{target_sha}..HEAD"
    )
    task = seed_task(connection, clock, title="Squash pull request")
    TaskStore(connection, clock).set_repo(
        task.id,
        RepoState(base=BASE_BRANCH, base_ref=BASE_BRANCH, base_sha=base_sha, branch=TASK_BRANCH),
    )
    _record_landed(
        connection,
        clock,
        task.id,
        lines,
        integration="pr",
        base_sha=base_sha,
        branch_sha=branch_sha,
    )
    view = await task_diff_service(reader, project_root, clock).load(
        task.id, DIFF_ALL_COMMITS, None
    )

    assert view.error is None
    assert view.files == ("task.py",)
    assert "+task change" in view.patch
    assert "target change" not in view.patch


def _diff_screen(project_root: Path, reader: StateReader, task_id: str) -> DiffScreen:
    service = task_diff_service(reader, project_root, FakeClock(START))
    return DiffScreen(service, task_id)


def _select(screen: DiffScreen, widget_id: str) -> Select[str]:
    return cast("Select[str]", screen.query_one(f"#{widget_id}", Select))


def _text(screen: DiffScreen, widget_id: str) -> str:
    return str(screen.query_one(f"#{widget_id}", Static).content)


def _rendered(screen: DiffScreen) -> Text:
    content = screen.query_one(f"#{DIFF_TEXT}", Static).content
    assert isinstance(content, Text)
    return content


def _line_styles(rendered: Text) -> dict[str, object]:
    return {rendered.plain[span.start : span.end]: span.style for span in rendered.spans}


def _screen_text(app: App[None]) -> str:
    drawn = re.findall(r"<text\b[^>]*>(.*?)</text>", app.export_screenshot(), re.DOTALL)
    return html.unescape("".join(drawn)).replace("\xa0", " ")


def _button_label(screen: DiffScreen) -> str:
    return str(screen.query_one(f"#{DIFF_FULLSCREEN}", Button).label)


async def _wait_for_scope(screen: DiffScreen, fragment: str) -> None:
    await wait_until(
        lambda: fragment in _text(screen, DIFF_SCOPE),
        timeout=UI_WAIT_SECONDS,
    )


async def test_popup_defaults_to_all_changes_and_the_first_file(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, worktree = await _live_task(connection, clock, project_root, SubprocessRunner())
    (worktree / "b.py").write_text("b\n")
    (worktree / "a.py").write_text("a\n")
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE) as pilot:
        await _wait_for_scope(screen, "Changes so far")
        commit_select = _select(screen, DIFF_COMMIT_SELECT)
        file_select = _select(screen, DIFF_FILE_SELECT)
        assert commit_select.value == DIFF_ALL_COMMITS
        assert file_select.value == "a.py"
        assert "Changes so far · includes uncommitted changes" in _text(screen, DIFF_SCOPE)

        await pilot.press("tab")
        await wait_until(lambda: pilot.app.focused is file_select, timeout=UI_WAIT_SECONDS)
        await pilot.press("enter")
        await wait_until(lambda: file_select.expanded, timeout=UI_WAIT_SECONDS)
        await pilot.press("down", "enter")
        await wait_until(lambda: file_select.value == "b.py", timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: "+b" in _text(screen, DIFF_TEXT),
            timeout=UI_WAIT_SECONDS,
        )


async def test_commit_change_makes_files_follow_and_keeps_first_file_preselected(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "first.py").write_text("first\n")
    await commit_all(runner, worktree, "first only")
    (worktree / "second.py").write_text("second\n")
    await commit_all(runner, worktree, "second only")
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE):
        await _wait_for_scope(screen, "Changes so far")
        service = task_diff_service(reader, project_root, clock)
        direct = await service.load(task_id, DIFF_ALL_COMMITS, None)
        oldest_sha = direct.commits[-1].sha
        commit_select = _select(screen, DIFF_COMMIT_SELECT)
        commit_select.value = oldest_sha
        await wait_until(
            lambda: _select(screen, DIFF_FILE_SELECT).value == "first.py",
            timeout=UI_WAIT_SECONDS,
        )
        diff_text = _text(screen, DIFF_TEXT)
        assert "+first" in diff_text
        assert "+second" not in diff_text


async def test_replacement_renders_headers_hunk_removed_and_added_on_separate_lines(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "app.py").write_text("one\ntwo\nkept\n")
    await commit_all(runner, worktree, "extend")
    await run_git(runner, worktree, "checkout", "-B", BASE_BRANCH)
    await run_git(runner, worktree, "checkout", "-B", TASK_BRANCH)
    await run_git(runner, worktree, "rm", "app.py")
    (worktree / "app.py").write_text("one\n-- three \x1b[31m\x0cend\nkept\n")
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE):
        await _wait_for_scope(screen, "Changes so far")
        rendered = _rendered(screen)

    lines = rendered.plain.split("\n")
    assert lines[0] == "diff --git a/app.py b/app.py"
    assert lines[2:] == [
        "--- a/app.py",
        "+++ b/app.py",
        "@@ -1,3 +1,3 @@",
        " one",
        "-two",
        "+-- three \N{SYMBOL FOR ESCAPE}[31mend",
        " kept",
    ]
    styles = _line_styles(rendered)
    assert styles["--- a/app.py"] == PRIMARY_TEXT_COLOR
    assert styles["+++ b/app.py"] == PRIMARY_TEXT_COLOR
    assert styles["@@ -1,3 +1,3 @@"] == f"bold {STEEL_BLUE}"
    assert styles[" one"] == PRIMARY_TEXT_COLOR
    assert styles["-two"] == DUSTY_ROSE
    assert styles[lines[-2]] == SAGE


async def test_file_and_commit_names_with_markup_characters_are_shown_literally(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "[bold].txt").write_text("bracketed\n")
    await commit_all(runner, worktree, "add [/red] file")
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE) as pilot:
        await _wait_for_scope(screen, "Changes so far")
        shown = _screen_text(pilot.app)
        assert "[bold].txt" in shown
        commit_select = _select(screen, DIFF_COMMIT_SELECT)
        await pilot.press("enter")
        await wait_until(lambda: commit_select.expanded, timeout=UI_WAIT_SECONDS)
        await pilot.pause()
        assert "add [/red] file" in _screen_text(pilot.app)


async def test_selected_commit_that_disappears_stays_selected_with_a_visible_error(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "a.py").write_text("a\n")
    (worktree / "b.py").write_text("b\n")
    await commit_all(runner, worktree, "two files")
    sha = (await run_git(runner, worktree, "log", "--format=%h", "-1")).strip()
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE):
        await _wait_for_scope(screen, "Changes so far")
        commit_select = _select(screen, DIFF_COMMIT_SELECT)
        commit_select.value = sha
        await _wait_for_scope(screen, "committed only")
        await run_git(runner, worktree, "commit", "--amend", "-m", "rewritten")
        _select(screen, DIFF_FILE_SELECT).value = "b.py"
        await wait_until(
            lambda: "is not part of this task" in _text(screen, DIFF_TEXT),
            timeout=UI_WAIT_SECONDS,
        )
        assert commit_select.value == sha
        assert _select(screen, DIFF_FILE_SELECT).disabled is True


async def test_fullscreen_preserves_selections_and_scroll_and_restores(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "app.py").write_text("one\n" + ("wide " * 40 + "\n") * 80)
    await commit_all(runner, worktree, "long file")
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE) as pilot:
        await _wait_for_scope(screen, "Changes so far")
        scroll = screen.query_one("#diff-scroll", ScrollableContainer)
        scroll.focus()
        await pilot.press("down", "down", "down", "right", "right")
        await wait_until(
            lambda: scroll.scroll_y == 3 and scroll.scroll_x == 2, timeout=UI_WAIT_SECONDS
        )
        commit_value = _select(screen, DIFF_COMMIT_SELECT).value
        file_value = _select(screen, DIFF_FILE_SELECT).value
        frame = screen.query_one("#diff-frame")
        restored = frame.region

        await pilot.press("f")
        await pilot.pause()
        assert frame.region.size == APP_SIZE
        assert _button_label(screen) == "f Restore"
        assert _select(screen, DIFF_COMMIT_SELECT).value == commit_value
        assert _select(screen, DIFF_FILE_SELECT).value == file_value
        assert (scroll.scroll_x, scroll.scroll_y) == (2, 3)
        await pilot.resize_terminal(120, 50)
        await wait_until(lambda: frame.region.size == (120, 50), timeout=UI_WAIT_SECONDS)
        await pilot.resize_terminal(*APP_SIZE)
        await wait_until(lambda: frame.region.size == APP_SIZE, timeout=UI_WAIT_SECONDS)

        await pilot.press("f")
        await pilot.pause()
        assert _button_label(screen) == "f Fullscreen"
        assert frame.region == restored
        assert restored.width < APP_SIZE[0]
        assert restored.bottom < APP_SIZE[1]
        assert (scroll.scroll_x, scroll.scroll_y) == (2, 3)


async def test_expanded_dropdown_does_not_trigger_fullscreen_and_escape_closes(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "app.py").write_text("one\ntwo\n")
    await commit_all(runner, worktree, "change")
    screen = _diff_screen(project_root, reader, task_id)

    host = ScreenHost(screen)
    async with host.run_test(size=APP_SIZE) as pilot:
        await _wait_for_scope(screen, "Changes so far")
        commit_select = _select(screen, DIFF_COMMIT_SELECT)
        assert pilot.app.focused is commit_select
        await pilot.press("enter")
        await wait_until(lambda: commit_select.expanded, timeout=UI_WAIT_SECONDS)
        await pilot.press("f")
        await pilot.pause()
        frame = screen.query_one("#diff-frame")
        assert frame.region.width < APP_SIZE[0]
        assert commit_select.expanded is True
        await pilot.press("escape")
        await wait_until(lambda: commit_select.expanded is False, timeout=UI_WAIT_SECONDS)
        await pilot.press("escape")
        await wait_until(lambda: not host.is_running, timeout=UI_WAIT_SECONDS)


async def test_empty_diff_is_visible_in_the_popup(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = await _live_task(connection, clock, project_root, SubprocessRunner())
    screen = _diff_screen(project_root, reader, task_id)

    async with ScreenHost(screen).run_test(size=APP_SIZE) as pilot:
        await _wait_for_scope(screen, "Changes so far")
        assert "No changes in this view." in _text(screen, DIFF_TEXT)
        await pilot.pause()
        assert "No files" in _screen_text(pilot.app)
        assert _select(screen, DIFF_FILE_SELECT).disabled is True


def _focused_fold(app: App[None]) -> str | None:
    focused = app.focused
    if focused is None:
        return None
    fold = next((item for item in focused.ancestors if isinstance(item, DetailFold)), None)
    return fold.view_id if fold is not None else None


async def test_d_opens_the_popup_only_from_the_results_fold(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = await _live_task(connection, clock, project_root, SubprocessRunner())
    reader = StateReader(database, clock)
    opened: list[str] = []

    async def open_diff(task_id: str) -> None:
        opened.append(task_id)

    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock, open_diff=open_diff), task_id, fixed=True
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 60)) as pilot:
            await wait_until(
                lambda: (
                    task_id
                    in plain_text(panel.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).content)
                ),
                timeout=UI_WAIT_SECONDS,
            )
            panel.focus_folds()
            await wait_until(
                lambda: _focused_fold(pilot.app) == TASK_VIEW_REQUEST,
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("d")
            await pilot.pause()
            assert opened == []
            await pilot.press("down")
            await wait_until(
                lambda: _focused_fold(pilot.app) == TASK_VIEW_RESULTS,
                timeout=UI_WAIT_SECONDS,
            )
            await pilot.press("d")
            await wait_until(lambda: opened == [task_id], timeout=UI_WAIT_SECONDS)
    finally:
        reader.close()


async def test_view_diff_in_more_opens_the_popup_from_the_actions_list(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    runner = SubprocessRunner()
    task_id, worktree = await _live_task(connection, clock, project_root, runner)
    (worktree / "app.py").write_text("one\ntwo\n")
    await commit_all(runner, worktree, "change")
    _, queries, _ = operator_services(connection, reader, clock, tmp_path)
    service = task_diff_service(reader, project_root, clock)
    screen = ActionsListScreen(
        queries,
        lambda request: (_ for _ in ()).throw(AssertionError(request)),
        PaletteRequest(task_id=task_id),
        on_change=reader.watch,
        action_confirmation_seconds=5,
        diff_screen=lambda tid: DiffScreen(service, tid),
    )
    host = ScreenHost(screen)

    async with host.run_test(size=APP_SIZE) as pilot:
        await wait_until(
            lambda: "More\N{HORIZONTAL ELLIPSIS}" in structure(screen),
            timeout=UI_WAIT_SECONDS,
        )
        await open_row(pilot, screen, "More\N{HORIZONTAL ELLIPSIS}")
        await wait_until(lambda: "View diff" in structure(screen), timeout=UI_WAIT_SECONDS)
        await open_row(pilot, screen, "View diff")
        await wait_until(
            lambda: isinstance(pilot.app.screen, DiffScreen) and is_composed(pilot.app.screen),
            timeout=UI_WAIT_SECONDS,
        )
        diff = cast("DiffScreen", pilot.app.screen)
        await _wait_for_scope(diff, "Changes so far")
        await pilot.press("escape")
        await wait_until(lambda: pilot.app.screen is screen, timeout=UI_WAIT_SECONDS)


async def test_results_fold_header_shows_the_diff_hint(
    connection: Connection, database: Database, tmp_path: Path
) -> None:
    project_root = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id, _ = await _live_task(connection, clock, project_root, SubprocessRunner())
    reader = StateReader(database, clock)

    async def open_diff(task_id: str) -> None:
        del task_id

    panel = TaskDetailPanel(
        widget_context(reader, project_root, clock, open_diff=open_diff), task_id, fixed=True
    )
    try:
        async with ManualFocusHost(panel).run_test(size=(100, 60)) as pilot:
            await wait_until(
                lambda: bool(panel.query_one(f"#{TASK_DETAIL_BRIEFING}", GatedStatic).content),
                timeout=UI_WAIT_SECONDS,
            )
            panel.focus_folds()
            await pilot.press("down")
            await wait_until(
                lambda: _focused_fold(pilot.app) == TASK_VIEW_RESULTS,
                timeout=UI_WAIT_SECONDS,
            )
            bar = panel.query_one("#task-detail-hint", Static)
            assert "view diff" in plain_text(bar.content)
    finally:
        reader.close()
