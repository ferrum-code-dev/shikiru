"""PR feedback rounds end to end: fake `gh`, a bare remote and fake agents."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from shikiru.engine.feedback_intake import FeedbackIntake
from shikiru.engine.integration import PullRequest
from shikiru.paths import task_root
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.feedback import FeedbackStore
from shikiru.workspace.base_refs import FetchedBaseRefs, LocalBaseSync
from shikiru.workspace.git import Git
from shikiru.workspace.github import GitHub
from tests.support.clock import FakeClock
from tests.support.engine import build_fault_reporter, pr_poll_seconds, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.git_repo import COMMIT_IDENTITY_ARGS, GIT_COMMAND_TIMEOUT
from tests.support.github import (
    MergeStyle,
    add_bare_remote,
    comment_node,
    merge_on_remote,
    review_node,
    thread_node,
)
from tests.support.waiting import stop_engine, wait_until, wait_until_advancing

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    import pytest

    from shikiru.config.settings import PullRequestFeedbackScope, Settings
    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

WAIT_SECONDS = 60
_SLOT = 1
RUNNER = SubprocessRunner()
REVIEW = "R1"
THREAD = "T1"
_WORK_ID = "w1"
_REMOTE_NAME = "origin"
OTHER_COMMIT = "someone else"
_TASK_FILE = "task.txt"
BASE_FILE = "base.txt"
_ADDRESS = "address"
_ACCEPT = "accept"
_CHANGES_REQUESTED = "changes_requested"
_REPLY_MARKER = "shikiru-feedback"
_COMMENT_ID = 11
_GIT = "git"
_GIT_TIMEOUT = 10
_REV_PARSE = "rev-parse"
_VERIFY = "--verify"
_QUIET = "--quiet"
_MERGE_BASE = "merge-base"
_IS_ANCESTOR = "--is-ancestor"
_MAIN = "main"


def decision(source_id: str, action: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "action": action,
        "points": ["the point"],
        "detail": "the detail",
        "reply": f"reply to {source_id}",
    }
    if action == _ADDRESS:
        fields["work_id"] = _WORK_ID
    fields.update(overrides)
    return fields


def feedback_analysis(*decisions: dict[str, Any]) -> dict[str, Any]:
    has_work = any(decision["action"] == _ADDRESS for decision in decisions)
    return {
        "kind": "report",
        "report": {
            "summary": "analysed",
            "work": [{"id": _WORK_ID, "instruction": "make the change"}] if has_work else [],
            "decisions": list(decisions),
        },
    }


def verdict(decision: str, *reply_ids: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "decision": decision,
        "summary": "verified",
        "replies": [{"source_id": source_id, "reply": "final"} for source_id in reply_ids],
    }
    if decision == _ACCEPT:
        report["commit_subject"] = "feat: verify feedback"
    if decision == _CHANGES_REQUESTED:
        report["issues"] = [{"severity": "major", "description": "still wrong"}]
    return {"kind": "report", "report": report}


class _Pull:
    """One task's flow on a fake pull request with a bare remote, restarted like the engine does."""

    def __init__(
        self, env: EngineEnv, settings: Settings, github: FakeGitHub, remote_dir: Path, task: Task
    ) -> None:
        self.env = env
        self.settings = settings
        self.github = github
        self.remote_dir = remote_dir
        self.task = task
        self.clock = FakeClock(env.clock.now())
        self.cleanup: Callable[[Task], Awaitable[None]]
        self.store = FeedbackStore(env.connection)
        self.events = EventLog(env.connection, env.clock)
        self.flow = self._start()

    def _start(self) -> asyncio.Task[None]:
        env = self.env
        fetched = FetchedBaseRefs(
            Git(RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            env.project_root,
            self.settings.repo.remote,
            fetch_timeout=self.settings.orchestration.network.fetch_timeout_seconds,
        )
        github = GitHub(RUNNER)
        faults = build_fault_reporter(self.events, env.clock, self.settings)
        strategy = PullRequest(
            git=Git(RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            github=github,
            clock=self.clock,
            tasks=env.tasks,
            events=self.events,
            project_root=env.project_root,
            remote=self.settings.repo.remote,
            base_refs=fetched,
            local_base=LocalBaseSync(
                Git(RUNNER, command_timeout=GIT_COMMAND_TIMEOUT), env.project_root
            ),
            faults=faults,
            settings=self.settings.orchestration,
            feedback=FeedbackIntake(
                github=github,
                store=self.store,
                events=self.events,
                faults=faults,
                project_root=env.project_root,
                scope=self.settings.orchestration.pr_feedback,
            ),
        )
        built = build_flow(
            env,
            self.settings,
            turn_clock=FakeClock(self.clock.now()),
            strategy=strategy,
            base_refs=fetched,
            feedback_store=self.store,
        )
        assert built.cleanup is not None
        self.cleanup = built.cleanup
        return asyncio.create_task(built.flow.run(self.task.id, resumed=True))

    async def stop(self) -> None:
        await stop_engine(self.flow, self.clock)

    async def restart(self) -> None:
        await self.stop()
        self.flow = self._start()

    async def dispatch(self) -> None:
        """Admit the queued task the way the scheduler does, then run its flow again."""
        queued = self.env.tasks.require(self.task.id)
        assert queued.status is Status.QUEUED
        assert queued.slot is None
        self.env.tasks.apply_transition(
            queued.id, Transition.stay(queued, step=queued.step, status=Status.INTEGRATING)
        )
        self.env.tasks.set_slot(queued.id, _SLOT)
        self.flow = self._start()

    async def await_poll(self, views: int) -> None:
        await wait_until(
            lambda: self.github.views() >= views and self.clock.has_pending_sleep(), WAIT_SECONDS
        )

    async def poll_again(self) -> None:
        views = self.github.views() + 1

        def polled() -> bool:
            if self.github.views() < views and self.clock.has_pending_sleep():
                self.clock.advance(pr_poll_seconds())
            return self.github.views() >= views and self.clock.has_pending_sleep()

        await wait_until(polled, WAIT_SECONDS)

    async def until(self, condition: Any) -> None:
        await wait_until_advancing(
            self.clock, condition, WAIT_SECONDS, step_seconds=pr_poll_seconds()
        )

    def waiting(self) -> bool:
        current = self.env.tasks.require(self.task.id)
        return (
            current.status is Status.INTEGRATING
            and current.slot is None
            and current.wait_reason == WaitReason.PR_OPEN.value
        )

    def handled(self) -> bool:
        return bool(self.events.since(0, kinds=[EventKind.FEEDBACK_HANDLED], task_id=self.task.id))

    def escalated(self) -> bool:
        return self.env.tasks.require(self.task.id).status is Status.ESCALATED

    def retry(self, reason: EscalationReason) -> None:
        [escalation] = fetch_open_escalations(self.env.connection)
        assert escalation.reason is reason
        escalated = self.env.tasks.require(self.task.id)
        resolved = transition_for_resolution(
            escalated, reason, step=Step.INTEGRATE, resolution=ResolutionKind.RETRY
        )
        self.env.tasks.apply_transition(self.task.id, resolved)
        queued = self.env.tasks.require(self.task.id)
        self.env.tasks.apply_transition(
            queued.id, Transition.stay(queued, step=queued.step, status=Status.INTEGRATING)
        )
        self.env.tasks.set_slot(queued.id, _SLOT)
        self.flow = self._start()

    def escalation_question(self) -> str:
        [escalation] = fetch_open_escalations(self.env.connection)
        return escalation.headline + (self.env.project_root / escalation.packet_path).read_text()

    def comments(self) -> list[str]:
        record = self.github.pull_requests()[0]
        threads = [c["body"] for t in record["reviewThreads"] for c in t["comments"]]
        return [*threads, *(c["body"] for c in record["conversationComments"])]

    def replies(self) -> list[str]:
        return [body for body in self.comments() if _REPLY_MARKER in body]

    async def remote_branch(self) -> str:
        assert self.task.repo is not None
        return await git(self.remote_dir, "rev-parse", self.task.repo.branch)

    async def request_changes(self, *, with_thread: bool = True) -> None:
        sha = await self.remote_branch()
        threads = (
            [thread_node(THREAD, [comment_node(1, _COMMENT_ID, "rename it", review_id=REVIEW)])]
            if with_thread
            else []
        )
        self.github.set_fields(
            1,
            headRefOid=sha,
            reviews=[review_node(1, REVIEW, "CHANGES_REQUESTED", body="please fix")],
            reviewThreads=threads,
        )


async def git(repo_dir: Path, *args: str) -> str:
    result = await RUNNER.run([_GIT, *args], timeout=_GIT_TIMEOUT, cwd=repo_dir, check=True)
    return result.stdout.strip()


async def open_pull(
    env: EngineEnv,
    github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    max_iterations: int | None = None,
    task_commit: bool = False,
    pr_feedback: PullRequestFeedbackScope | None = None,
) -> _Pull:
    settings = (
        flow_settings(pr_feedback=pr_feedback)
        if max_iterations is None
        else flow_settings(max_iterations=max_iterations, pr_feedback=pr_feedback)
    )
    await init_project_repo(env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(env, mode=Mode.IMPLEMENT)
    if task_commit:
        worktree = task_root(env.project_root, task.id)
        (worktree / _TASK_FILE).write_text("task\n")
        await git(worktree, "add", _TASK_FILE)
        await git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "task work")
    pull = _Pull(env, settings, github, remote_dir, task)
    await pull.await_poll(1)
    assert pull.waiting()
    return pull


LOCAL_FILE = "local.txt"


async def script_round(pull: _Pull, tmux_server: TmuxServer) -> None:
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "address")),
            verdict("accept", REVIEW, THREAD),
            verdict("accept", REVIEW, THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)


async def publish_round(pull: _Pull, tmux_server: TmuxServer) -> None:
    await script_round(pull, tmux_server)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)


async def lose_checkpoint_of(pull: _Pull, column: str) -> None:
    pull.env.connection.execute(f"""
        CREATE TRIGGER lose_checkpoint BEFORE UPDATE OF {column} ON feedback_rounds
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, WAIT_SECONDS)
    pull.env.connection.execute("DROP TRIGGER lose_checkpoint")


async def land_remotely(pull: _Pull, style: MergeStyle) -> None:
    assert pull.task.repo is not None
    merged = await merge_on_remote(pull.remote_dir, "main", pull.task.repo.branch, style)
    pull.github.merge(1, merged)


async def task_state(pull: _Pull) -> tuple[bool, str | None]:
    assert pull.task.repo is not None
    root = pull.env.project_root
    branch = pull.task.repo.branch
    present = task_root(root, pull.task.id).exists()
    exists = await RUNNER.run(
        [_GIT, _REV_PARSE, _VERIFY, _QUIET, f"refs/heads/{branch}"],
        timeout=_GIT_TIMEOUT,
        cwd=root,
    )
    return present, exists.stdout.strip() or None


async def on_remote_base(pull: _Pull, commit: str) -> bool:
    result = await RUNNER.run(
        [_GIT, _MERGE_BASE, _IS_ANCESTOR, commit, _MAIN],
        timeout=_GIT_TIMEOUT,
        cwd=pull.remote_dir,
    )
    return result.returncode == 0
