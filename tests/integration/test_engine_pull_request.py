"""`PullRequest` integration against a fake `gh` on PATH and a local bare remote (03 §6-8)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Status, Step
from shikiru.engine.integration import PR_POLL, PullRequest
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.workspace.git import Git
from shikiru.workspace.github import GitHub, PullRequestState
from shikiru.workspace.roots import task_root
from tests.support.clock import FakeClock
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import commit_all
from tests.support.github import add_bare_remote
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub

_WAIT_SECONDS = 60
_RETRY_SLOT = 1
_BASE_CHANGE_FILENAME = "base_moved.txt"
_RUNNER = SubprocessRunner()


@dataclass(frozen=True)
class _Case:
    outcomes: tuple[PullRequestState, ...]
    restart_while_open: bool = False
    base_moves_after_close: bool = False


_CASES = {
    "merged is done": _Case((PullRequestState.MERGED,)),
    "closed escalates and its retry opens a new PR": _Case(
        (PullRequestState.CLOSED, PullRequestState.MERGED)
    ),
    "a retry after the base moved pushes the rebased branch": _Case(
        (PullRequestState.CLOSED, PullRequestState.MERGED), base_moves_after_close=True
    ),
    "a restart while open resumes polling": _Case(
        (PullRequestState.MERGED,), restart_while_open=True
    ),
}


class _Run:
    """One task's flow, restarted the way the `Reconciler` resumes an integrating task."""

    def __init__(self, env: EngineEnv, settings: Settings, task_id: str) -> None:
        self._env = env
        self._settings = settings
        self._task_id = task_id
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
        strategy = PullRequest(
            git=Git(_RUNNER),
            github=GitHub(_RUNNER),
            clock=env.clock,
            tasks=env.tasks,
            events=EventLog(env.connection, env.clock),
            project_root=env.project_root,
            remote=self._settings.repo.remote,
        )
        flow = build_flow(env, self._settings, strategy=strategy).flow
        return asyncio.create_task(flow.run(self._task_id, resumed=resumed))


async def _await_poll(github: FakeGitHub, clock: FakeClock, views: int) -> None:
    await wait_until(lambda: github.views() >= views and clock.has_pending_sleep(), _WAIT_SECONDS)


async def _land(clock: FakeClock, running: asyncio.Task[None]) -> None:
    def landed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(PR_POLL.total_seconds())
        return running.done()

    await wait_until(landed, _WAIT_SECONDS)
    await running


async def _retry_closed(env: EngineEnv, run: _Run, task_id: str, case: _Case) -> None:
    [escalation] = fetch_open_escalations(env.connection)
    assert escalation.reason is EscalationReason.PR_CLOSED
    if case.base_moves_after_close:
        (env.project_root / _BASE_CHANGE_FILENAME).write_text("base moved\n")
        await commit_all(_RUNNER, env.project_root, "move the base")
    escalated = env.tasks.get(task_id)
    assert escalated is not None
    resolved = transition_for_resolution(
        escalated, EscalationReason.PR_CLOSED, step=Step.INTEGRATE, resolution=ResolutionKind.RETRY
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
    assert done.data["commits"] == opened[-1].data["commits"] != []
    branch = task.repo.branch
    assert await _git(remote_dir, "rev-parse", branch) == await _git(
        env.project_root, "rev-parse", branch
    )
    await _git(env.project_root, "merge-base", "--is-ancestor", task.repo.base, branch)


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
    run = _Run(engine_env, settings, task.id)
    for number, outcome in enumerate(case.outcomes, start=1):
        views = fake_github.views() + 1
        await _await_poll(fake_github, clock, views)
        _assert_waiting_on_open_pr(engine_env, task.id)
        assert len(fake_github.pull_requests()) == number
        if case.restart_while_open:
            await run.restart()
            await _await_poll(fake_github, clock, views + 1)
        fake_github.set_state(number, outcome)
        await _land(clock, run.flow)
        if outcome is PullRequestState.CLOSED:
            await _retry_closed(engine_env, run, task.id, case)

    await _assert_landed(engine_env, fake_github, remote_dir, task, len(case.outcomes))
