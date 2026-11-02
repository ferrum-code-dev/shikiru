"""Local squash integration against real Git repositories, SQLite and a bare remote."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.config.settings import RepoConfig
from shikiru.domain.events import EventKind
from shikiru.domain.reports import Commit
from shikiru.domain.results import ResultCommit, derive_task_results
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Status, Step
from shikiru.engine.fault_reporter import FaultReporter
from shikiru.engine.integration import (
    IntegrationBlocked,
    IntegrationMetadata,
    Integrator,
    LocalMerge,
    MergeRequest,
    OffTaskBranch,
    SquashMerge,
    UncommittedWork,
)
from shikiru.engine.push_service import PUSH_FAULT_SCOPE, REMOTE_CHECK_FAULT_SCOPE, PushService
from shikiru.engine.tick_guard import TickGuard
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database, transaction
from shikiru.store.events import EventLog
from shikiru.store.pushes import PushTargets
from shikiru.store.tasks import TaskStore
from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.engine import engine_settings
from tests.support.factories import TASK_CREATED_AT
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all, init_repo, run_git
from tests.support.github import add_bare_remote
from tests.support.store import seed_task
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shikiru.domain.results import TaskResults
    from shikiru.domain.tasks import Task
    from tests.support.store import Connection

_RUNNER = SubprocessRunner()
_LOGGER = logging.getLogger("squash-test")
_REMOTE = "origin"
_RELEASE = "release"
_COMMIT_CAP = 10


@dataclass
class Rig:
    connection: Connection
    clock: FakeClock
    project_root: Path
    remote_dir: Path
    events: EventLog
    tasks: TaskStore
    git: Git
    service: PushService
    integrator: Integrator
    strategy: LocalMerge

    async def start_task(self, name: str, *, base: str = "main") -> tuple[Task, Path]:
        task = seed_task(self.connection, self.clock, title=name, mode=Mode.IMPLEMENT)
        self.tasks.apply_transition(
            task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
        )
        state = await task_root_builder(_RUNNER, self.project_root, RepoConfig(base=base)).build(
            task.id, task.title
        )
        self.tasks.set_repo(task.id, state)
        current = self.tasks.get(task.id)
        assert current is not None
        return current, self.project_root / ".shikiru" / "worktrees" / task.id

    async def commit(self, root: Path, filename: str, content: str) -> None:
        (root / filename).write_text(content)
        await commit_all(_RUNNER, root, f"add {filename}")

    async def integrate(self, task: Task) -> None:
        await self.integrator.run(task, _resolve_nothing, IntegrationMetadata("summary"))

    def done_data(self, task: Task) -> dict[str, object]:
        done = self.events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
        assert len(done) == 1
        return done[0].data

    def landed_lines(self, task: Task) -> list[str]:
        commits = cast("list[str]", self.done_data(task)["commits"])
        return list(commits)

    async def sha(self, ref: str) -> str:
        return await run_git(_RUNNER, self.project_root, "rev-parse", ref)

    async def count(self, commit_range: str) -> int:
        return int(await run_git(_RUNNER, self.project_root, "rev-list", "--count", commit_range))

    async def files(self, ref: str) -> list[str]:
        listing = await run_git(_RUNNER, self.project_root, "ls-tree", "-r", "--name-only", ref)
        return listing.splitlines()


async def _resolve_nothing(
    task: Task, continue_rebase: Callable[[], Awaitable[None]], recovery: object
) -> tuple[Task, IntegrationMetadata]:
    del continue_rebase, recovery
    return task, IntegrationMetadata("resolved")


async def _rig(
    connection: Connection,
    tmp_path: Path,
    *,
    interval: int = 0,
    local: type[LocalMerge] = SquashMerge,
) -> Rig:
    project_root = tmp_path / "project"
    await init_repo(_RUNNER, project_root)
    (project_root / "README.md").write_text("base\n")
    await commit_all(_RUNNER, project_root, "initial")
    await run_git(_RUNNER, project_root, "branch", _RELEASE)
    remote_dir = tmp_path / "origin.git"
    await add_bare_remote(project_root, remote_dir, _REMOTE)
    clock = FakeClock(TASK_CREATED_AT)
    events = EventLog(connection, clock)
    tasks = TaskStore(connection, clock)
    git = Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT)
    orchestration = engine_settings().orchestration
    faults = orchestration.faults
    reporter = FaultReporter(events=events, clock=clock, settings=faults, logger=_LOGGER)
    service = PushService(
        targets=PushTargets(connection),
        git=git,
        guard_for=lambda base: TickGuard(
            reporter=reporter,
            clock=clock,
            settings=faults,
            logger=_LOGGER,
            scope=f"{PUSH_FAULT_SCOPE} {base}",
        ),
        remote_guard=TickGuard(
            reporter=reporter,
            clock=clock,
            settings=faults,
            logger=_LOGGER,
            scope=f"{REMOTE_CHECK_FAULT_SCOPE} {_REMOTE}",
        ),
        clock=clock,
        project_root=project_root,
        remote=_REMOTE,
        interval_seconds=interval,
        remote_recheck_seconds=orchestration.remote_recheck_seconds,
    )
    builder = task_root_builder(_RUNNER, project_root, RepoConfig())

    async def finish_task(task: Task) -> None:
        await builder.cleanup(task.id, task.repo, integrated=task.status is Status.DONE)

    strategy = local(git, clock, events=events, project_root=project_root, integration_wait=0)
    integrator = Integrator(
        git=git,
        strategy=strategy,
        base_refs=LocalBaseRefs(),
        lock=asyncio.Lock(),
        project_root=project_root,
        tasks=tasks,
        events=events,
        finish_task=finish_task,
        publication=service,
        atomically=lambda: transaction(connection),
    )
    return Rig(
        connection,
        clock,
        project_root,
        remote_dir,
        events,
        tasks,
        git,
        service,
        integrator,
        strategy,
    )


def _reopened_integrator(
    rig: Rig, connection: Connection, local: type[LocalMerge]
) -> tuple[EventLog, TaskStore, Integrator]:
    events = EventLog(connection, rig.clock)
    tasks = TaskStore(connection, rig.clock)
    builder = task_root_builder(_RUNNER, rig.project_root, RepoConfig())

    async def finish_task(finished: Task) -> None:
        await builder.cleanup(finished.id, finished.repo, integrated=finished.status is Status.DONE)

    strategy = local(
        rig.git,
        rig.clock,
        events=events,
        project_root=rig.project_root,
        integration_wait=0,
    )
    integrator = Integrator(
        git=rig.git,
        strategy=strategy,
        base_refs=LocalBaseRefs(),
        lock=asyncio.Lock(),
        project_root=rig.project_root,
        tasks=tasks,
        events=events,
        finish_task=finish_task,
        atomically=lambda: transaction(connection),
    )
    return events, tasks, integrator


@pytest.mark.parametrize("base", ["main", _RELEASE], ids=["checked-out", "ref-only"])
async def test_a_multi_commit_task_lands_as_one_commit_with_its_rebased_tree(
    connection: Connection, tmp_path: Path, base: str
) -> None:
    rig = await _rig(connection, tmp_path)
    task, root = await rig.start_task("squash me", base=base)
    await rig.commit(root, "a.txt", "a\none\n")
    await rig.commit(root, "b.txt", "b\n")
    await rig.commit(root, "a.txt", "a\ntwo\n")
    before = await rig.sha(base)

    await rig.integrate(task)

    landed = await rig.sha(base)
    assert await rig.count(f"{before}..{landed}") == 1
    assert await rig.sha(f"{landed}^") == before
    assert {"a.txt", "b.txt"} <= set(await rig.files(landed))
    assert (rig.project_root / "a.txt").exists() is (base == "main")
    commits = rig.landed_lines(task)
    assert len(commits) == 1
    assert commits[0].startswith(landed[:7])
    assert rig.done_data(task)["integration"] == "squash"
    assert not root.exists()


async def test_the_squash_commit_is_parented_by_the_previous_tip_and_keeps_the_complete_diff(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    task, root = await rig.start_task("complete diff")
    await rig.commit(root, "a.txt", "1\n2\n3\n")
    await rig.commit(root, "b.txt", "b\n")
    await rig.commit(root, "a.txt", "1\n2\n")
    await rig.commit(rig.project_root, "moved.txt", "moved\n")
    previous = await rig.sha("main")

    await rig.integrate(task)

    landed = await rig.sha("main")
    assert await rig.sha(f"{landed}^") == previous
    assert await rig.count(f"{previous}..{landed}") == 1
    assert sorted(await rig.files(landed)) == ["README.md", "a.txt", "b.txt", "moved.txt"]
    assert (rig.project_root / "a.txt").read_text() == "1\n2\n"
    assert rig.done_data(task)["changes"] == {"files": 2, "insertions": 3, "deletions": 0}
    message = await run_git(_RUNNER, rig.project_root, "log", "-1", "--format=%B", landed)
    assert message.startswith(f"{task.id}: complete diff\n\nsummary")
    assert f"Shikiru-Task: {task.id}" in message


async def test_completed_results_describe_the_landed_commit_after_cleanup(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    task, root = await rig.start_task("retained")
    await rig.commit(root, "a.txt", "a\n")
    await rig.commit(root, "b.txt", "b\nc\n")
    await rig.integrate(task)
    landed = await rig.sha("main")

    finished = rig.tasks.get(task.id)
    assert finished is not None
    results = derive_task_results(
        finished.status,
        branch=task.repo.branch if task.repo else None,
        base="main",
        done=rig.done_data(task),
        report_commits=[],
        facts="stale facts",
        tests=None,
        pull_request=None,
        scope_events=[],
        titles={},
        verdict_risks=[],
        plan_summary=None,
        commit_cap=_COMMIT_CAP,
    )

    assert [commit.sha for commit in results.commits] == [landed[:7]]
    assert results.commits[0].subject.startswith(f"{task.id}: retained")
    assert results.produced is not None
    assert (results.produced.commits, results.produced.files) == (1, 2)
    assert (results.produced.insertions, results.produced.deletions) == (3, 0)
    assert results.squashed
    assert not root.exists()


async def test_an_unrelated_dirty_checkout_edit_survives_and_an_overlapping_one_blocks(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    (rig.project_root / "keep.txt").write_text("mine\n")
    await run_git(_RUNNER, rig.project_root, "add", "keep.txt")
    await commit_all(_RUNNER, rig.project_root, "tracked keep")
    task, root = await rig.start_task("dirty checkout")
    await rig.commit(root, "a.txt", "a\n")
    (rig.project_root / "keep.txt").write_text("edited\n")
    (rig.project_root / "a.txt").write_text("in the way\n")
    before = await rig.sha("main")

    with pytest.raises(IntegrationBlocked):
        await rig.integrate(task)
    assert await rig.sha("main") == before
    assert (rig.project_root / "a.txt").read_text() == "in the way\n"

    (rig.project_root / "a.txt").unlink()
    await rig.integrate(task)

    assert (rig.project_root / "keep.txt").read_text() == "edited\n"
    assert (rig.project_root / "a.txt").read_text() == "a\n"
    assert await rig.count(f"{before}..main") == 1


async def test_uncommitted_task_work_and_an_off_task_head_block_squash(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    dirty, dirty_root = await rig.start_task("dirty task")
    await rig.commit(dirty_root, "a.txt", "a\n")
    (dirty_root / "README.md").write_text("uncommitted\n")
    before = await rig.sha("main")

    with pytest.raises(UncommittedWork):
        await rig.integrate(dirty)

    off, off_root = await rig.start_task("off task")
    await rig.commit(off_root, "b.txt", "b\n")
    await run_git(_RUNNER, off_root, "checkout", "--detach")

    with pytest.raises(OffTaskBranch):
        await rig.integrate(off)
    assert await rig.sha("main") == before


async def test_a_squash_applied_before_a_crash_is_recognised_without_another_commit(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    task, root = await rig.start_task("crashed")
    await rig.commit(root, "a.txt", "a\n")
    await rig.commit(root, "b.txt", "b\n")
    previous = await rig.sha("main")
    assert task.repo is not None
    await rig.strategy.merge(
        MergeRequest(
            task_id=task.id,
            title=task.title,
            repo=task.repo,
            task_root=root,
            summary="summary",
            held=lambda: False,
        )
    )
    landed = await rig.sha("main")
    assert await rig.count(f"{previous}..{landed}") == 1

    await rig.integrate(task)

    assert await rig.sha("main") == landed
    commits = rig.landed_lines(task)
    assert len(commits) == 1
    assert landed.startswith(commits[0].split()[0])
    assert rig.done_data(task)["changes"] == {"files": 2, "insertions": 2, "deletions": 0}
    final = rig.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert PushTargets(connection).pending()


async def test_a_local_merge_applied_before_sqlite_reopens_keeps_its_landed_evidence(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, local=LocalMerge)
    task, root = await rig.start_task("merge recovery")
    await rig.commit(root, "a.txt", "a\n")
    await rig.commit(root, "b.txt", "b\n")
    previous = await rig.sha("main")
    assert task.repo is not None

    await rig.strategy.merge(
        MergeRequest(
            task_id=task.id,
            title=task.title,
            repo=task.repo,
            task_root=root,
            summary="summary",
            held=lambda: False,
        )
    )
    landed = await rig.sha("main")
    database_path = Path(connection.execute("PRAGMA database_list").fetchone()["file"])
    reopened = Database(database_path).connect()
    try:
        events, tasks, integrator = _reopened_integrator(rig, reopened, LocalMerge)
        recovered = tasks.get(task.id)
        assert recovered is not None

        await integrator.run(recovered, _resolve_nothing, IntegrationMetadata("summary"))

        [done] = events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
        final = tasks.get(task.id)
        assert final is not None
        assert final.status is Status.DONE
        assert done.data["commits"] == await rig.git.log_oneline(
            rig.project_root, f"{previous}..{landed}"
        )
        assert done.data["changes"] == {"files": 2, "insertions": 2, "deletions": 0}
        assert await rig.sha("main") == landed
        assert not root.exists()
    finally:
        reopened.close()


@pytest.mark.parametrize("local", [LocalMerge, SquashMerge], ids=["merge", "squash"])
async def test_a_new_task_commit_after_landing_survives_sqlite_restart(
    connection: Connection, tmp_path: Path, local: type[LocalMerge]
) -> None:
    rig = await _rig(connection, tmp_path, local=local)
    task, root = await rig.start_task("preserve newer work")
    await rig.commit(root, "landed.txt", "landed\n")
    assert task.repo is not None
    await rig.strategy.merge(
        MergeRequest(
            task_id=task.id,
            title=task.title,
            repo=task.repo,
            task_root=root,
            summary="summary",
            held=lambda: False,
        )
    )
    landed = await rig.sha("main")
    await rig.commit(root, "keep-my-work.txt", "keep\n")
    task_tip = await rig.git.rev_parse(root, task.repo.branch)

    database_path = Path(connection.execute("PRAGMA database_list").fetchone()["file"])
    reopened = Database(database_path).connect()
    try:
        events, tasks, integrator = _reopened_integrator(rig, reopened, local)
        recovered = tasks.get(task.id)
        assert recovered is not None

        with pytest.raises(IntegrationBlocked):
            await integrator.run(recovered, _resolve_nothing, IntegrationMetadata("summary"))

        current = tasks.get(task.id)
        assert current is not None
        assert current.status is Status.INTEGRATING
        assert events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id) == []
        assert root.exists()
        assert await rig.git.branch_exists(rig.project_root, task.repo.branch)
        assert await rig.git.rev_parse(rig.project_root, task.repo.branch) == task_tip
        assert (root / "keep-my-work.txt").read_text() == "keep\n"
        assert not (rig.project_root / "keep-my-work.txt").exists()
        assert await rig.sha("main") == landed
    finally:
        reopened.close()


async def test_an_empty_task_lands_no_commit_and_records_no_landed_commits(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    task, _ = await rig.start_task("nothing")
    before = await rig.sha("main")

    await rig.integrate(task)

    assert await rig.sha("main") == before
    data = rig.done_data(task)
    assert data["commits"] == []
    assert data["changes"] == {"files": 0, "insertions": 0, "deletions": 0}


async def test_a_task_whose_commits_cancel_out_lands_no_empty_commit(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path)
    task, root = await rig.start_task("cancels out")
    await rig.commit(root, "a.txt", "a\n")
    await run_git(_RUNNER, root, "rm", "a.txt")
    await commit_all(_RUNNER, root, "remove a")
    before = await rig.sha("main")

    await rig.integrate(task)

    assert await rig.sha("main") == before
    assert rig.done_data(task)["commits"] == []


async def test_a_squash_is_published_in_zero_mode_and_a_failed_push_keeps_it_pending(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, interval=0)
    first, first_root = await rig.start_task("published")
    await rig.commit(first_root, "a.txt", "a\n")
    await rig.integrate(first)
    rig.service.poll(_LOGGER)
    await rig.service.finish_outstanding()
    assert await run_git(_RUNNER, rig.remote_dir, "rev-parse", "refs/heads/main") == await rig.sha(
        "main"
    )

    await run_git(_RUNNER, rig.project_root, "remote", "set-url", _REMOTE, str(tmp_path / "gone"))
    second, second_root = await rig.start_task("unpublished")
    await rig.commit(second_root, "b.txt", "b\n")
    await rig.integrate(second)
    rig.service.poll(_LOGGER)
    await rig.service.finish_outstanding()

    final = rig.tasks.get(second.id)
    assert final is not None
    assert final.status is Status.DONE
    assert PushTargets(connection).pending()


def _results_of(rig: Rig, task: Task, report: list[Commit]) -> TaskResults:
    finished = rig.tasks.get(task.id)
    assert finished is not None
    return derive_task_results(
        finished.status,
        branch=task.repo.branch if task.repo else None,
        base="main",
        done=rig.done_data(task),
        report_commits=report,
        facts="stale facts",
        tests=None,
        pull_request=None,
        scope_events=[],
        titles={},
        verdict_risks=[],
        plan_summary=None,
        commit_cap=_COMMIT_CAP,
    )


async def test_a_merged_task_keeps_its_landed_commits_and_totals_after_cleanup(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, local=LocalMerge)
    task, root = await rig.start_task("plain")
    await rig.commit(root, "a.txt", "a\n")
    await rig.commit(root, "b.txt", "b\nc\n")
    await rig.integrate(task)
    stale = [Commit(sha="f" * 40, subject="stale report commit")]

    results = _results_of(rig, task, stale)

    assert len(results.commits) == 2
    assert ResultCommit("f" * 7, "stale report commit") not in results.commits
    assert results.produced is not None
    assert (results.produced.commits, results.produced.files) == (2, 2)
    assert (results.produced.insertions, results.produced.deletions) == (3, 0)
    assert not results.squashed
    assert not root.exists()


async def test_an_empty_merged_task_stays_empty_despite_older_report_commits(
    connection: Connection, tmp_path: Path
) -> None:
    rig = await _rig(connection, tmp_path, local=LocalMerge)
    task, _ = await rig.start_task("nothing")

    await rig.integrate(task)
    results = _results_of(rig, task, [Commit(sha="e" * 40, subject="old report commit")])

    assert results.commits == ()
    assert results.produced is not None
    assert (results.produced.commits, results.produced.files) == (0, 0)
    assert not results.squashed
