"""`PullRequest` integration against a fake `gh` on PATH and a local bare remote."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status, Step
from shikiru.engine.integration import PullRequest
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.workspace.base_refs import BaseFetchFailed, FetchedBaseRefs, LocalBaseSync
from shikiru.workspace.git import Git
from shikiru.workspace.github import GitHub, PullRequestState
from shikiru.workspace.roots import task_root
from tests.support import fake_gh
from tests.support.clock import FakeClock
from tests.support.engine import build_fault_reporter, pr_poll_seconds, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import GIT_COMMAND_TIMEOUT, commit_all
from tests.support.github import MergeStyle, add_bare_remote, merge_on_remote
from tests.support.waiting import wait_until, wait_until_advancing
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_WAIT_SECONDS = 60
_RETRY_SLOT = 1
_BASE_CHANGE_FILENAME = "base_moved.txt"
_LOCAL_ONLY = "local_only.txt"
_LOCAL_EDIT = "my notes\n"
_RUNNER = SubprocessRunner()


def _fetched(settings: Settings, project_root: Path) -> FetchedBaseRefs:
    return FetchedBaseRefs(
        Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
        project_root,
        settings.repo.remote,
        fetch_timeout=settings.orchestration.network.fetch_timeout_seconds,
    )


def _local_base(project_root: Path) -> LocalBaseSync:
    return LocalBaseSync(Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT), project_root)


@dataclass(frozen=True)
class _Case:
    outcomes: tuple[PullRequestState, ...]
    restart_while_open: bool = False
    base_moves_after_close: bool = False
    crash_after_create: bool = False


_CASES = {
    "merged is done": _Case((PullRequestState.MERGED,)),
    "a closed PR escalates and its retry opens a new one after the base moved": _Case(
        (PullRequestState.CLOSED, PullRequestState.MERGED), base_moves_after_close=True
    ),
    "a restart while open resumes polling": _Case(
        (PullRequestState.MERGED,), restart_while_open=True
    ),
    "a restart between opening and recording adopts the open PR": _Case(
        (PullRequestState.MERGED,), crash_after_create=True
    ),
}


class _Run:
    """One task's flow, restarted the way the `Reconciler` resumes an integrating task."""

    def __init__(self, env: EngineEnv, settings: Settings, task_id: str) -> None:
        self._env = env
        self._settings = settings
        self._task_id = task_id
        self.clock = FakeClock(env.clock.now())
        self.flow = self._start(resumed=False)

    def start(self) -> None:
        self.flow = self._start(resumed=False)

    async def restart(self) -> None:
        self.flow.cancel()
        with suppress(asyncio.CancelledError):
            await self.flow
        self.flow = self._start(resumed=True)

    def _start(self, *, resumed: bool) -> asyncio.Task[None]:
        env = self._env
        events = EventLog(env.connection, env.clock)
        strategy = PullRequest(
            git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            github=GitHub(_RUNNER),
            clock=self.clock,
            tasks=env.tasks,
            events=events,
            project_root=env.project_root,
            remote=self._settings.repo.remote,
            base_refs=_fetched(self._settings, env.project_root),
            local_base=_local_base(env.project_root),
            faults=build_fault_reporter(events, env.clock, self._settings),
            settings=self._settings.orchestration,
        )
        flow = build_flow(
            env,
            self._settings,
            turn_clock=FakeClock(env.clock.now()),
            strategy=strategy,
            base_refs=_fetched(self._settings, env.project_root),
        ).flow
        return asyncio.create_task(flow.run(self._task_id, resumed=resumed))


async def _die_between_opening_and_recording(github: FakeGitHub, run: _Run) -> None:
    await wait_until(lambda: len(github.pull_requests()) == 1, _WAIT_SECONDS)
    await run.restart()
    github.release("create")


async def _await_poll(github: FakeGitHub, clock: FakeClock, views: int) -> None:
    await wait_until(lambda: github.views() >= views and clock.has_pending_sleep(), _WAIT_SECONDS)


async def _poll_again(github: FakeGitHub, clock: FakeClock, views: int) -> None:
    def polled() -> bool:
        if github.views() < views and clock.has_pending_sleep():
            clock.advance(pr_poll_seconds())
        return github.views() >= views and clock.has_pending_sleep()

    await wait_until(polled, _WAIT_SECONDS)


async def _land(clock: FakeClock, running: asyncio.Task[None]) -> None:
    await wait_until_advancing(clock, running.done, _WAIT_SECONDS, step_seconds=pr_poll_seconds())
    await running


async def _retry_closed(
    env: EngineEnv, run: _Run, task_id: str, case: _Case, remote_dir: Path
) -> None:
    if case.base_moves_after_close:
        (env.project_root / _BASE_CHANGE_FILENAME).write_text("base moved\n")
        await commit_all(_RUNNER, env.project_root, "move the base")
        await _git(env.project_root, "push", str(remote_dir), "main")
    _retry(env, run, task_id, EscalationReason.PR_CLOSED)


def _retry(env: EngineEnv, run: _Run, task_id: str, reason: EscalationReason) -> None:
    [escalation] = fetch_open_escalations(env.connection)
    assert escalation.reason is reason
    escalated = env.tasks.get(task_id)
    assert escalated is not None
    resolved = transition_for_resolution(
        escalated, reason, step=Step.INTEGRATE, resolution=ResolutionKind.RETRY
    )
    env.tasks.apply_transition(task_id, resolved)
    queued = env.tasks.get(task_id)
    assert queued is not None
    env.tasks.apply_transition(
        task_id, Transition.stay(queued, step=queued.step, status=Status.INTEGRATING)
    )
    env.tasks.set_slot(task_id, _RETRY_SLOT)
    run.start()


def _assert_waiting_on_open_pr(env: EngineEnv, task_id: str) -> None:
    waiting = env.tasks.get(task_id)
    assert waiting is not None
    assert waiting.status is Status.INTEGRATING
    assert waiting.slot is None
    assert waiting.wait_reason == WaitReason.PR_OPEN.value


async def _git(repo_dir: Path, *args: str) -> str:
    result = await _RUNNER.run(["git", *args], timeout=10, cwd=repo_dir, check=True)
    return result.stdout.strip()


async def _local_branch_exists(project_root: Path, branch: str) -> bool:
    result = await _RUNNER.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        timeout=10,
        cwd=project_root,
    )
    return result.returncode == 0


async def _changed_paths(remote_dir: Path, base_sha: str, commit: str) -> list[str]:
    return (await _git(remote_dir, "diff", "--name-only", f"{base_sha}..{commit}")).splitlines()


async def _merge_remotely(
    github: FakeGitHub, remote_dir: Path, number: int, style: MergeStyle
) -> None:
    pull = github.pull_requests()[number - 1]
    github.merge(number, await merge_on_remote(remote_dir, pull["base"], pull["head"], style))


async def _assert_landed(
    env: EngineEnv, github: FakeGitHub, remote_dir: Path, task: Task, pr_count: int
) -> None:
    assert task.repo is not None
    final = env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.DONE
    assert final.wait_reason is None
    assert not task_root(env.project_root, task.id).exists()
    assert task.window_id is not None
    assert not await env.tmux.target_exists(task.window_id)
    pulls = github.pull_requests()
    assert len(pulls) == pr_count
    for pull in pulls:
        assert pull["head"] == task.repo.branch
        assert pull["base"] == task.repo.base
        assert pull["title"] == task.title
        assert pull["body"] == final.summary
    events = EventLog(env.connection, env.clock)
    opened = events.since(0, kinds=[EventKind.PR_OPENED], task_id=task.id)
    [done] = events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    assert len(opened) == pr_count
    merge_commit = pulls[-1]["mergeCommit"]["oid"]
    parents = await _git(remote_dir, "rev-list", "--parents", "-n", "1", merge_commit)
    before = parents.split()[1]
    landed_commits = await _git(remote_dir, "log", "--oneline", f"{before}..{merge_commit}")
    assert done.data["commits"] == landed_commits.splitlines() != []
    stats = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).committed_line_changes(
        remote_dir, before, merge_commit
    )
    assert done.data["integration"] == "pr"
    assert done.data["changes"] == {
        "files": stats.changed_paths,
        "insertions": stats.added_lines,
        "deletions": stats.deleted_lines,
    }
    assert done.data["changes"]["files"] > 0
    assert await _git(remote_dir, "rev-parse", task.repo.branch)
    assert not await _local_branch_exists(env.project_root, task.repo.branch)


@pytest.mark.parametrize(
    "style",
    [MergeStyle.MERGE_COMMIT, MergeStyle.SQUASH, MergeStyle.FAST_FORWARD],
    ids=["merge-commit", "squash", "fast-forward"],
)
async def test_pull_request_records_the_verified_landing_after_update_and_restart(
    style: MergeStyle,
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)
    events = EventLog(engine_env.connection, clock)
    [opened] = events.since(0, kinds=[EventKind.PR_OPENED], task_id=task.id)

    task_root_dir = task_root(engine_env.project_root, task.id)
    (task_root_dir / "during_review.txt").write_text("review update\n")
    await commit_all(_RUNNER, task_root_dir, "review update")
    await _RUNNER.run(
        ["git", "push", settings.repo.remote, task.repo.branch],
        timeout=GIT_COMMAND_TIMEOUT,
        cwd=task_root_dir,
        check=True,
    )
    previous_views = fake_github.views()
    await run.restart()
    await _await_poll(fake_github, run.clock, previous_views + 1)

    landed = await merge_on_remote(remote_dir, task.repo.base, task.repo.branch, style)
    fake_github.merge(1, landed)
    await _land(run.clock, run.flow)

    [done] = events.since(0, kinds=[EventKind.TASK_DONE], task_id=task.id)
    parents = await _git(remote_dir, "rev-list", "--parents", "-n", "1", landed)
    parent_list = parents.split()[1:]
    before = opened.data["base_sha"] if style is MergeStyle.FAST_FORWARD else parent_list[0]
    landed_commits = await _git(remote_dir, "log", "--oneline", f"{before}..{landed}")
    stats = await Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT).committed_line_changes(
        remote_dir, before, landed
    )
    assert done.data["commits"] == landed_commits.splitlines()
    assert any(line.startswith(landed[:7]) for line in done.data["commits"])
    assert done.data["changes"] == {
        "files": stats.changed_paths,
        "insertions": stats.added_lines,
        "deletions": stats.deleted_lines,
    }
    assert done.data["changes"]["files"] == 2
    assert done.data["changes"]["insertions"] >= 2
    assert done.data["commits"] != opened.data["commits"]


@pytest.mark.parametrize("case", _CASES.values(), ids=_CASES.keys())
async def test_pull_request_lands_through_the_done_path(
    case: _Case,
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    if case.crash_after_create:
        fake_github.hold("create")
    run = _Run(engine_env, settings, task.id)
    if case.crash_after_create:
        await _die_between_opening_and_recording(fake_github, run)
    for number, outcome in enumerate(case.outcomes, start=1):
        views = fake_github.views() + 1
        await _await_poll(fake_github, run.clock, views)
        _assert_waiting_on_open_pr(engine_env, task.id)
        assert len(fake_github.pull_requests()) == number
        if case.restart_while_open:
            await run.restart()
            await _await_poll(fake_github, run.clock, views + 1)
        if outcome is PullRequestState.MERGED:
            await _merge_remotely(fake_github, remote_dir, number, MergeStyle.MERGE_COMMIT)
        else:
            fake_github.set_state(number, outcome)
        await _land(run.clock, run.flow)
        if outcome is PullRequestState.CLOSED:
            await _retry_closed(engine_env, run, task.id, case, remote_dir)

    await _assert_landed(engine_env, fake_github, remote_dir, task, len(case.outcomes))


async def test_an_agent_that_left_its_task_branch_opens_no_pull_request(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "leave_branch": "branch"}, {"kind": "accept"}, {"kind": "accept"}],
    )
    run = _Run(engine_env, settings, task.id)

    await asyncio.wait_for(run.flow, timeout=_WAIT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    assert fake_github.pull_requests() == []
    assert task_root(engine_env.project_root, task.id).is_dir()
    assert await _git(task_root(engine_env.project_root, task.id), "log", "main..HEAD")


@pytest.mark.parametrize("style", list(MergeStyle))
async def test_dependents_start_from_the_merged_remote_base(
    style: MergeStyle,
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)
    await _merge_remotely(fake_github, remote_dir, 1, style)
    await _land(run.clock, run.flow)
    delivered = await _git(
        remote_dir, "diff", "--name-only", f"{task.repo.base_sha}..{task.repo.base}"
    )

    dependent = await task_root_builder(
        _RUNNER,
        project_root,
        settings.repo,
        _fetched(settings, project_root),
    ).build("T-2", "Use the retries")

    assert delivered
    assert not await _local_branch_exists(project_root, task.repo.branch)
    for path in delivered.splitlines():
        assert (task_root(project_root, "T-2") / path).exists()
    assert dependent.base == task.repo.base
    remote_tip = await _git(remote_dir, "rev-parse", task.repo.base)
    assert await _git(project_root, "rev-parse", task.repo.base) == remote_tip
    assert await _git(project_root, "rev-parse", "HEAD") == remote_tip
    tracking = f"refs/remotes/{settings.repo.remote}/{task.repo.base}"
    assert await _git(project_root, "rev-parse", tracking) == remote_tip


async def test_a_merged_pull_request_missing_from_the_remote_base_blocks_done(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)

    fake_github.merge(1, await _git(remote_dir, "rev-parse", task.repo.branch))
    await _land(run.clock, run.flow)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_BLOCKED
    blocked = engine_env.tasks.get(task.id)
    assert blocked is not None
    assert blocked.status is Status.ESCALATED


async def test_uncommitted_work_that_blocks_the_base_sync_escalates_and_completes_once_moved(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    merge_commit = fake_github.pull_requests()[0]["mergeCommit"]["oid"]
    delivered = (await _changed_paths(remote_dir, task.repo.base_sha, merge_commit))[0]
    (project_root / delivered).write_text(_LOCAL_EDIT)
    base_before = await _git(project_root, "rev-parse", task.repo.base)
    await _land(run.clock, run.flow)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_BLOCKED
    blocked = engine_env.tasks.get(task.id)
    assert blocked is not None
    assert blocked.status is Status.ESCALATED
    assert await _git(project_root, "rev-parse", task.repo.base) == base_before
    assert (project_root / delivered).read_text() == _LOCAL_EDIT

    moved_aside = project_root / f"{delivered}.mine"
    (project_root / delivered).rename(moved_aside)
    _retry(engine_env, run, task.id, EscalationReason.INTEGRATION_BLOCKED)
    await _land(run.clock, run.flow)

    await _assert_landed(engine_env, fake_github, remote_dir, task, 1)
    remote_tip = await _git(remote_dir, "rev-parse", task.repo.base)
    assert await _git(project_root, "rev-parse", task.repo.base) == remote_tip
    assert moved_aside.read_text() == _LOCAL_EDIT


async def test_an_unpushed_local_commit_blocks_done_and_is_kept(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)
    (project_root / _LOCAL_ONLY).write_text(_LOCAL_EDIT)
    await commit_all(_RUNNER, project_root, "local work")
    local_base = await _git(project_root, "rev-parse", task.repo.base)
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    await _land(run.clock, run.flow)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_BLOCKED
    blocked = engine_env.tasks.get(task.id)
    assert blocked is not None
    assert blocked.status is Status.ESCALATED
    assert await _git(project_root, "rev-parse", task.repo.base) == local_base
    assert (project_root / _LOCAL_ONLY).read_text() == _LOCAL_EDIT


async def test_a_paused_pull_request_stops_polling_and_never_escalates_until_resumed(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)

    engine_env.tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
    await _land(run.clock, run.flow)
    run.clock.advance(settings.orchestration.timeouts.pr_wait)

    assert fake_github.views() == 1
    assert fetch_open_escalations(engine_env.connection) == []
    assert engine_env.tasks.require(task.id).status is Status.INTEGRATING

    engine_env.tasks.set_holds(task.id, frozenset())
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    run.start()
    await _land(run.clock, run.flow)

    assert engine_env.tasks.require(task.id).status is Status.DONE


_FAILING_CHECK = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}
_PASSING_CHECK = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
_RUNNING_CHECK = {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}
_PENDING_STATUS = {"__typename": "StatusContext", "state": "PENDING"}


@dataclass(frozen=True)
class _Stall:
    fields: dict[str, object]
    reason: EscalationReason


_STALLS = {
    "conflict": _Stall({"mergeable": "CONFLICTING"}, EscalationReason.INTEGRATION_CONFLICT),
    "failing checks": _Stall(
        {"statusCheckRollup": [_PASSING_CHECK, _FAILING_CHECK]},
        EscalationReason.INTEGRATION_BLOCKED,
    ),
    "over budget": _Stall({}, EscalationReason.INTEGRATION_BLOCKED),
}


@pytest.mark.parametrize("stall", _STALLS.values(), ids=_STALLS.keys())
async def test_an_open_pull_request_that_cannot_land_escalates_once_with_its_url_and_state(
    stall: _Stall,
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)

    fake_github.set_fields(1, **stall.fields)
    await _land(run.clock, run.flow)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is stall.reason
    packet = await asyncio.to_thread(Path(escalation.packet_path).read_text)
    assert f"{fake_gh.URL_PREFIX}1" in packet
    blocked = engine_env.tasks.get(task.id)
    assert blocked is not None
    assert blocked.status is Status.ESCALATED
    assert blocked.wait_reason is None


@pytest.mark.parametrize("exhausted", [False, True], ids=["within the budget", "past the budget"])
async def test_failed_polls_are_retried_on_a_backoff_and_escalate_only_past_the_attempt_budget(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    exhausted: bool,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    attempts = settings.orchestration.network.poll_attempts
    failures = attempts if exhausted else attempts - 1
    fake_github.fail_next("view", failures)
    run = _Run(engine_env, settings, task.id)
    await wait_until(lambda: len(fake_github.pull_requests()) == 1, _WAIT_SECONDS)

    if not exhausted:
        await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    await _land(run.clock, run.flow)

    assert fake_github.views() == (failures if exhausted else failures + 1)
    final = engine_env.tasks.require(task.id)
    escalations = fetch_open_escalations(engine_env.connection)
    if exhausted:
        assert [e.reason for e in escalations] == [EscalationReason.ENGINE_ERROR]
    else:
        assert final.status is Status.DONE
        assert escalations == []


@pytest.mark.parametrize(
    "unsettled",
    [[_FAILING_CHECK, _RUNNING_CHECK], [_FAILING_CHECK, _PENDING_STATUS]],
    ids=["a check still running", "a status still pending"],
)
async def test_a_failing_check_does_not_escalate_until_the_checks_have_settled(
    unsettled: list[dict[str, object]],
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)

    fake_github.set_fields(1, statusCheckRollup=unsettled)
    await _poll_again(fake_github, run.clock, 2)

    assert fetch_open_escalations(engine_env.connection) == []
    assert not run.flow.done()

    fake_github.set_fields(1, statusCheckRollup=[_PASSING_CHECK])
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    await _land(run.clock, run.flow)

    done = engine_env.tasks.get(task.id)
    assert done is not None
    assert done.status is Status.DONE


async def test_the_branch_of_a_merged_pull_request_stays_with_a_worktree_kept_for_uncommitted_work(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    await init_project_repo(engine_env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(engine_env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)

    (task_root(engine_env.project_root, task.id) / "scratch.txt").write_text("unsaved work\n")
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.SQUASH)
    await _land(run.clock, run.flow)

    done = engine_env.tasks.get(task.id)
    assert done is not None
    assert done.status is Status.DONE
    assert (task_root(engine_env.project_root, task.id) / "scratch.txt").exists()
    assert await _local_branch_exists(engine_env.project_root, task.repo.branch)


async def _push_unrelated_change(remote_dir: Path, clone_dir: Path, filename: str) -> str:
    await _git(clone_dir.parent, "clone", "--branch", "main", str(remote_dir), str(clone_dir))
    (clone_dir / filename).write_text("unrelated\n")
    await commit_all(_RUNNER, clone_dir, "unrelated change")
    await _git(clone_dir, "push", "origin", "main")
    return await _git(clone_dir, "rev-parse", "HEAD")


async def _contains(remote_dir: Path, branch: str, commit: str) -> bool:
    result = await _RUNNER.run(
        ["git", "merge-base", "--is-ancestor", commit, branch], timeout=10, cwd=remote_dir
    )
    return result.returncode == 0


async def test_a_task_root_starts_from_the_remote_base_as_it_is_now(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    del fake_github
    settings = flow_settings()
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    builder = task_root_builder(
        _RUNNER,
        project_root,
        settings.repo,
        _fetched(settings, project_root),
    )
    await builder.build("T-1", "Before the push")

    await _push_unrelated_change(remote_dir, tmp_path_factory.mktemp("other") / "clone", "new.txt")
    later = await builder.build("T-2", "After the push")

    assert (task_root(project_root, "T-2") / "new.txt").exists()
    assert later.base_sha == await _git(remote_dir, "rev-parse", "main")


async def test_a_task_root_is_not_built_from_a_stale_base_when_the_remote_cannot_be_fetched(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    del fake_github
    settings = flow_settings()
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    await _git(project_root, "remote", "set-url", settings.repo.remote, str(remote_dir / "gone"))
    builder = task_root_builder(
        _RUNNER,
        project_root,
        settings.repo,
        _fetched(settings, project_root),
    )

    with pytest.raises(BaseFetchFailed):
        await builder.build("T-1", "Offline")

    assert not task_root(project_root, "T-1").exists()


async def test_a_retry_after_a_conflicting_pull_request_rebases_and_pushes_again(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    settings = flow_settings()
    clock = engine_env.clock
    assert isinstance(clock, FakeClock)
    project_root = engine_env.project_root
    await init_project_repo(project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(
        engine_env, mode=Mode.IMPLEMENT, base_refs=_fetched(settings, project_root)
    )
    assert task.repo is not None
    run = _Run(engine_env, settings, task.id)
    await _await_poll(fake_github, run.clock, 1)
    fake_github.set_fields(1, mergeable="CONFLICTING")
    await _land(run.clock, run.flow)
    moved = await _push_unrelated_change(
        remote_dir, tmp_path_factory.mktemp("other") / "clone", "elsewhere.txt"
    )
    assert not await _contains(remote_dir, task.repo.branch, moved)

    views = fake_github.views() + 1
    fake_github.set_fields(1, mergeable="MERGEABLE")
    _retry(engine_env, run, task.id, EscalationReason.INTEGRATION_CONFLICT)
    await _await_poll(fake_github, run.clock, views)

    assert await _contains(remote_dir, task.repo.branch, moved)
    assert len(fake_github.pull_requests()) == 1
    await _merge_remotely(fake_github, remote_dir, 1, MergeStyle.MERGE_COMMIT)
    await _land(run.clock, run.flow)
    done = engine_env.tasks.get(task.id)
    assert done is not None
    assert done.status is Status.DONE
