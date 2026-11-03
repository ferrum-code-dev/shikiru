"""PR feedback rounds end to end: fake `gh`, a bare remote and fake agents."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import transition_for_resolution
from shikiru.domain.events import EventKind
from shikiru.domain.feedback_rounds import FeedbackStage
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    FeedbackAnalysis,
    ImplementationReport,
    ReportKind,
)
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Mode, Role, Status, Step
from shikiru.engine.feedback_intake import FeedbackIntake
from shikiru.engine.integration import PullRequest
from shikiru.paths import task_root, turns_dir
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.feedback import FeedbackStore
from shikiru.workspace.base_refs import FetchedBaseRefs
from shikiru.workspace.git import Git
from shikiru.workspace.github import GitHub, PullRequestState
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
from tests.support.waiting import wait_until, wait_until_advancing

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from tests.support.clock import FakeClock
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_WAIT_SECONDS = 60
_SLOT = 1
_RUNNER = SubprocessRunner()
_REVIEW = "R1"
_THREAD = "T1"
_WORK_ID = "w1"
_REMOTE_NAME = "origin"
_OTHER_COMMIT = "someone else"
_TASK_FILE = "task.txt"
_BASE_FILE = "base.txt"


def _decision(source_id: str, action: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "action": action,
        "points": ["the point"],
        "detail": "the detail",
        "reply": f"reply to {source_id}",
    }
    if action == "address":
        fields["work_id"] = _WORK_ID
    fields.update(overrides)
    return fields


def _analysis(*decisions: dict[str, Any]) -> dict[str, Any]:
    has_work = any(decision["action"] == "address" for decision in decisions)
    return {
        "kind": "report",
        "report": {
            "summary": "analysed",
            "work": [{"id": _WORK_ID, "instruction": "make the change"}] if has_work else [],
            "decisions": list(decisions),
        },
    }


def _verdict(decision: str, *reply_ids: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "decision": decision,
        "summary": "verified",
        "replies": [{"source_id": source_id, "reply": "final"} for source_id in reply_ids],
    }
    if decision == "accept":
        report["commit_subject"] = "feat: verify feedback"
    if decision == "changes_requested":
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
        self.clock: FakeClock = env.clock  # type: ignore[assignment]
        self.cleanup: Callable[[Task], Awaitable[None]]
        self.store = FeedbackStore(env.connection)
        self.events = EventLog(env.connection, env.clock)
        self.flow = self._start()

    def _start(self) -> asyncio.Task[None]:
        env = self.env
        fetched = FetchedBaseRefs(
            Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            env.project_root,
            self.settings.repo.remote,
            fetch_timeout=self.settings.orchestration.network.fetch_timeout_seconds,
        )
        github = GitHub(_RUNNER)
        faults = build_fault_reporter(self.events, env.clock, self.settings)
        strategy = PullRequest(
            git=Git(_RUNNER, command_timeout=GIT_COMMAND_TIMEOUT),
            github=github,
            clock=env.clock,
            tasks=env.tasks,
            events=self.events,
            project_root=env.project_root,
            remote=self.settings.repo.remote,
            base_refs=fetched,
            faults=faults,
            settings=self.settings.orchestration,
            feedback=FeedbackIntake(
                github=github,
                store=self.store,
                events=self.events,
                faults=faults,
                project_root=env.project_root,
            ),
        )
        built = build_flow(
            env, self.settings, strategy=strategy, base_refs=fetched, feedback_store=self.store
        )
        assert built.cleanup is not None
        self.cleanup = built.cleanup
        return asyncio.create_task(built.flow.run(self.task.id, resumed=True))

    async def stop(self) -> None:
        self.flow.cancel()
        with suppress(asyncio.CancelledError):
            await self.flow

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
            lambda: self.github.views() >= views and self.clock.has_pending_sleep(), _WAIT_SECONDS
        )

    async def poll_again(self) -> None:
        views = self.github.views() + 1

        def polled() -> bool:
            if self.github.views() < views and self.clock.has_pending_sleep():
                self.clock.advance(pr_poll_seconds())
            return self.github.views() >= views and self.clock.has_pending_sleep()

        await wait_until(polled, _WAIT_SECONDS)

    async def until(self, condition: Any) -> None:
        await wait_until_advancing(
            self.clock, condition, _WAIT_SECONDS, step_seconds=pr_poll_seconds()
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
        return [body for body in self.comments() if "shikiru-feedback" in body]

    async def remote_branch(self) -> str:
        assert self.task.repo is not None
        return await _git(self.remote_dir, "rev-parse", self.task.repo.branch)

    async def request_changes(self, *, with_thread: bool = True) -> None:
        sha = await self.remote_branch()
        threads = (
            [thread_node(_THREAD, [comment_node(1, 11, "rename it", review_id=_REVIEW)])]
            if with_thread
            else []
        )
        self.github.set_fields(
            1,
            headRefOid=sha,
            reviews=[review_node(1, _REVIEW, "CHANGES_REQUESTED", body="please fix")],
            reviewThreads=threads,
        )


async def _git(repo_dir: Path, *args: str) -> str:
    result = await _RUNNER.run(["git", *args], timeout=10, cwd=repo_dir, check=True)
    return result.stdout.strip()


async def _open_pull(
    env: EngineEnv,
    github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    max_iterations: int | None = None,
    task_commit: bool = False,
) -> _Pull:
    settings = (
        flow_settings() if max_iterations is None else flow_settings(max_iterations=max_iterations)
    )
    await init_project_repo(env.project_root)
    remote_dir = tmp_path_factory.mktemp("remote") / "origin.git"
    await add_bare_remote(env.project_root, remote_dir, settings.repo.remote)
    task = await build_flow_task(env, mode=Mode.IMPLEMENT)
    if task_commit:
        worktree = task_root(env.project_root, task.id)
        (worktree / _TASK_FILE).write_text("task\n")
        await _git(worktree, "add", _TASK_FILE)
        await _git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "task work")
    pull = _Pull(env, settings, github, remote_dir, task)
    await pull.await_poll(1)
    assert pull.waiting()
    return pull


async def test_requested_changes_are_reworked_published_and_answered(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])

    await pull.until(pull.flow.done)
    queued = engine_env.tasks.require(pull.task.id)
    assert queued.status is Status.QUEUED
    assert queued.slot is None
    assert queued.wait_reason is None

    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)

    after = await pull.remote_branch()
    assert after != before
    local = await _git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
    assert local == after
    thread_reply, review_reply = sorted(pull.replies(), key=lambda body: "In reply to" in body)
    assert f"Fixed in {after[:12]}" in thread_reply
    assert "In reply to" in review_reply
    assert "Fixed in" not in review_reply
    assert len(pull.github.pull_requests()) == 1
    assert pull.task.repo is not None
    branch = pull.task.repo.branch
    merged = await merge_on_remote(
        pull.remote_dir,
        "main",
        branch,
        MergeStyle.MERGE_COMMIT,
    )
    fake_github.merge(1, merged)
    await pull.until(pull.flow.done)
    assert engine_env.tasks.require(pull.task.id).status is Status.DONE


async def test_only_new_changes_requested_reviews_start_a_round(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    sha = await pull.remote_branch()
    fake_github.set_fields(
        1,
        headRefOid=sha,
        reviews=[
            review_node(1, "A", "APPROVED", body="lgtm"),
            review_node(1, "C", "COMMENTED", body="a remark"),
            review_node(1, "D", "DISMISSED", body="old"),
            review_node(1, "P", "PENDING", body="draft", submitted_at=None),
        ],
    )
    for _ in range(3):
        await pull.poll_again()

    assert pull.store.active(pull.task.id) is None
    assert pull.waiting()
    started = pull.events.since(0, kinds=[EventKind.FEEDBACK_STARTED], task_id=pull.task.id)
    assert started == []
    await pull.stop()


async def test_a_round_that_only_disagrees_replies_without_publishing(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [_analysis(_decision(_REVIEW, "disagree")), _verdict("accept", _REVIEW)],
    )
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)

    assert await pull.remote_branch() == before
    [reply] = pull.replies()
    assert "Fixed in" not in reply
    started = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    rework = [e for e in started if e.data.get("report_kind") == "feedback_rework"]
    assert rework == []
    await pull.stop()


async def test_feedback_that_needs_the_operator_escalates_with_its_source(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(
                _decision(_REVIEW, "needs_operator", detail="which API do you want?", reply="")
            )
        ],
    )
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    question = pull.escalation_question()
    assert "which API do you want?" in question
    assert _REVIEW in question
    assert pull.replies() == []
    assert pull.store.active(pull.task.id) is not None


async def test_a_closed_pull_request_during_a_round_escalates_and_a_retry_opens_a_new_one(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(tmux_server, Role.LEADER, [_analysis(_decision(_REVIEW, "disagree"))])
    await pull.until(pull.flow.done)
    fake_github.set_state(1, PullRequestState.CLOSED)
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.PR_CLOSED
    assert pull.store.active(pull.task.id) is None
    assert pull.replies() == []
    assert engine_env.tasks.require(pull.task.id).repo is not None
    pull.retry(EscalationReason.PR_CLOSED)
    await wait_until(
        lambda: len(fake_github.pull_requests()) == 2 and pull.waiting(), _WAIT_SECONDS
    )
    await pull.stop()


async def test_a_push_that_loses_the_lease_keeps_both_sides_and_sends_no_replies(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    assert pull.task.repo is not None
    branch = pull.task.repo.branch
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    tip = await pull.remote_branch()
    tree = await _git(engine_env.project_root, "rev-parse", f"{tip}^{{tree}}")
    other = await _git(engine_env.project_root, "commit-tree", tree, "-p", tip, "-m", _OTHER_COMMIT)
    await _git(
        engine_env.project_root, "push", pull.settings.repo.remote, f"{other}:refs/heads/{branch}"
    )
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    assert await pull.remote_branch() == other
    assert pull.replies() == []
    assert pull.store.active(pull.task.id) is not None


async def test_changes_that_stay_rejected_escalate_after_the_attempt_limit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory, max_iterations=1)
    before = await pull.remote_branch()
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("changes_requested"),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    question = pull.escalation_question()
    assert "not resolved after 1 implementation attempts" in question
    assert _THREAD in question
    assert await pull.remote_branch() == before
    assert pull.replies() == []


async def test_an_interrupted_reply_is_found_by_its_marker_instead_of_posted_twice(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    fake_github.fail_next("lost-post", 1)
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    assert len(pull.replies()) == 1
    assert not pull.handled()
    pull.retry(EscalationReason.INTEGRATION_BLOCKED)
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)

    assert len(pull.replies()) == 2
    assert len(set(pull.replies())) == 2
    await pull.stop()


async def test_a_feedback_rebase_is_verified_again_and_the_landing_keeps_every_commit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory, task_commit=True)
    assert pull.task.repo is not None
    branch = pull.task.repo.branch
    remote = pull.settings.repo.remote
    root = engine_env.project_root
    (root / _BASE_FILE).write_text("base\n")
    await _git(root, "add", _BASE_FILE)
    await _git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base moved")
    await _git(root, "push", remote, pull.task.repo.base)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)

    started = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    verdicts = [e for e in started if e.data.get("report_kind") == "feedback_verdict"]
    assert len(verdicts) == 2
    assert await pull.remote_branch() == await _git(root, "rev-parse", branch)

    merged = await merge_on_remote(pull.remote_dir, "main", branch, MergeStyle.FAST_FORWARD)
    fake_github.merge(1, merged)
    await pull.until(pull.flow.done)
    [done] = pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)
    commits = done.data["commits"]
    assert any("task work" in commit for commit in commits)
    assert not any("base moved" in commit for commit in commits)
    assert len(commits) == 3


async def test_closure_seen_while_reading_a_reply_destination_posts_nothing(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "disagree")),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await pull.until(pull.flow.done)
    fake_github.close_on_next_graphql()
    await pull.dispatch()
    await wait_until(pull.escalated, _WAIT_SECONDS)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.PR_CLOSED
    assert not any(f":{_THREAD} " in reply for reply in pull.replies())


async def test_merged_feedback_recovers_prepared_landing_after_publication_checkpoint_loss(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory, task_commit=True)
    assert pull.task.repo is not None
    root = engine_env.project_root
    (root / _BASE_FILE).write_text("base\n")
    await _git(root, "add", _BASE_FILE)
    await _git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base moved")
    await _git(root, "push", pull.settings.repo.remote, pull.task.repo.base)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    engine_env.connection.execute("""
        CREATE TRIGGER fail_published BEFORE UPDATE OF published_sha ON feedback_rounds
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, _WAIT_SECONDS)
    engine_env.connection.execute("DROP TRIGGER fail_published")
    merged = await merge_on_remote(
        pull.remote_dir, "main", pull.task.repo.branch, MergeStyle.FAST_FORWARD
    )
    fake_github.merge(1, merged)
    pull.retry(EscalationReason.ENGINE_ERROR)
    await pull.until(pull.flow.done)
    [done] = pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)
    assert len(done.data["commits"]) == 3
    assert any("task work" in commit for commit in done.data["commits"])
    assert not any("base moved" in commit for commit in done.data["commits"])


async def test_rework_checkpoint_failure_replays_completed_report_without_exhausting_budget(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory, max_iterations=1)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    engine_env.connection.execute("""
        CREATE TRIGGER fail_verification BEFORE UPDATE OF stage ON feedback_rounds
        WHEN NEW.stage = 'verification'
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, _WAIT_SECONDS)
    active = pull.store.active(pull.task.id)
    assert active is not None
    assert active.attempts == 0
    engine_env.connection.execute("DROP TRIGGER fail_verification")
    pull.retry(EscalationReason.ENGINE_ERROR)
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)
    started = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    assert len([e for e in started if e.data.get("report_kind") == "feedback_rework"]) == 1
    assert len([e for e in started if e.data.get("report_kind") == "feedback_verdict"]) == 1
    await pull.stop()


async def test_feedback_preparation_recovers_completed_conflict_resolution(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    assert pull.task.repo is not None
    root = engine_env.project_root
    worktree = task_root(root, pull.task.id)
    (worktree / "README.md").write_text("task change\n")
    await _git(worktree, "add", "README.md")
    await _git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "feedback work")
    verified = await _git(worktree, "rev-parse", pull.task.repo.branch)
    (root / "README.md").write_text("base change\n")
    await _git(root, "add", "README.md")
    await _git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base change")
    await _git(root, "push", pull.settings.repo.remote, pull.task.repo.base)
    await pull.request_changes()
    await pull.until(pull.flow.done)
    active = pull.store.active(pull.task.id)
    assert active is not None
    analysis = FeedbackAnalysis.model_validate(
        _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address"))["report"]
    )
    pull.store.save_analysis(active.id, analysis, FeedbackStage.PUBLICATION)
    pull.store.save_verified(
        active.id,
        sha=verified,
        replies={_REVIEW: "final", _THREAD: "final"},
        stage=FeedbackStage.PUBLICATION,
    )
    result = await _RUNNER.run(["git", "rebase", "main"], cwd=worktree, timeout=10)
    assert result.returncode != 0
    (worktree / "README.md").write_text("recovered resolution\n")
    await _git(worktree, "add", "README.md")
    turns_root = turns_dir(root, pull.task.id)
    label = f"{sum(1 for entry in turns_root.iterdir() if entry.is_dir()) + 1:02d}-integrate-i0"
    turn = turns_root / label
    turn.mkdir(parents=True, exist_ok=True)
    (turn / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
    (turn / REPORT_FILENAME).write_text(
        ImplementationReport(
            status="completed", summary="resolved", commits=[], tests=[], trackers_updated=False
        ).model_dump_json()
    )
    for kind in (EventKind.TURN_STARTED, EventKind.TURN_COMPLETED):
        pull.events.append(
            kind,
            task_id=pull.task.id,
            message="conflict repair",
            data={
                "turn": label,
                "step": Step.INTEGRATE.value,
                "role": Role.IMPLEMENTER.value,
                **({"outcome": "completed"} if kind is EventKind.TURN_COMPLETED else {}),
            },
        )
    repair = pull.events.since(0, kinds=[EventKind.TURN_COMPLETED], task_id=pull.task.id)[-1]
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            {"kind": "accept"},
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)
    assert (worktree / "README.md").read_text() == "recovered resolution\n"
    turns = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    assert not any(
        event.data.get("role") == "implementer" and event.id > repair.id for event in turns
    )
    await pull.stop()


_LOCAL_FILE = "local.txt"


async def _script_round(pull: _Pull, tmux_server: TmuxServer) -> None:
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis(_decision(_REVIEW, "disagree"), _decision(_THREAD, "address")),
            _verdict("accept", _REVIEW, _THREAD),
            _verdict("accept", _REVIEW, _THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)


async def _publish_round(pull: _Pull, tmux_server: TmuxServer) -> None:
    await _script_round(pull, tmux_server)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), _WAIT_SECONDS)


async def _lose_checkpoint_of(pull: _Pull, column: str) -> None:
    pull.env.connection.execute(f"""
        CREATE TRIGGER lose_checkpoint BEFORE UPDATE OF {column} ON feedback_rounds
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, _WAIT_SECONDS)
    pull.env.connection.execute("DROP TRIGGER lose_checkpoint")


async def _land_remotely(pull: _Pull, style: MergeStyle) -> None:
    assert pull.task.repo is not None
    merged = await merge_on_remote(pull.remote_dir, "main", pull.task.repo.branch, style)
    pull.github.merge(1, merged)


async def _task_state(pull: _Pull) -> tuple[bool, str | None]:
    assert pull.task.repo is not None
    root = pull.env.project_root
    branch = pull.task.repo.branch
    present = task_root(root, pull.task.id).exists()
    exists = await _RUNNER.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        timeout=10,
        cwd=root,
    )
    return present, exists.stdout.strip() or None


async def _on_remote_base(pull: _Pull, commit: str) -> bool:
    result = await _RUNNER.run(
        ["git", "merge-base", "--is-ancestor", commit, "main"], timeout=10, cwd=pull.remote_dir
    )
    return result.returncode == 0


@pytest.mark.parametrize("lost_start_event", [False, True], ids=["recorded", "start-event-lost"])
async def test_a_pull_request_merged_before_feedback_publication_keeps_the_local_feedback_commit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
    lost_start_event: bool,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    published = await pull.remote_branch()
    await _script_round(pull, tmux_server)
    await _lose_checkpoint_of(pull, "verified_sha")
    stranded = await _git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
    assert stranded != published
    await _land_remotely(pull, MergeStyle.MERGE_COMMIT)
    if lost_start_event:
        engine_env.connection.execute(
            "DELETE FROM events WHERE kind = ?", (EventKind.FEEDBACK_STARTED.value,)
        )
    pull.retry(EscalationReason.ENGINE_ERROR)
    await pull.until(pull.flow.done)

    done = engine_env.tasks.require(pull.task.id)
    assert done.status is Status.DONE
    [recorded] = pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)
    assert not any(stranded[:7] in commit for commit in recorded.data["commits"])
    assert await pull.remote_branch() == published
    assert pull.replies() == []
    assert not await _on_remote_base(pull, stranded)
    assert await _task_state(pull) == (True, stranded)

    await pull.restart()
    await pull.cleanup(done)
    await pull.cleanup(done)
    assert await _task_state(pull) == (True, stranded)
    assert len(pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)) == 1


async def test_a_branch_advanced_after_verification_is_kept_when_the_pull_request_merges(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await _publish_round(pull, tmux_server)
    worktree = task_root(engine_env.project_root, pull.task.id)
    (worktree / _LOCAL_FILE).write_text("later\n")
    await _git(worktree, "add", _LOCAL_FILE)
    await _git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "later work")
    advanced = await _git(worktree, "rev-parse", "HEAD")
    await _land_remotely(pull, MergeStyle.FAST_FORWARD)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    assert await _task_state(pull) == (True, advanced)


@pytest.mark.parametrize("head_reported", [True, False], ids=["head-proves-it", "unproven"])
async def test_a_push_without_its_checkpoint_is_kept_unless_the_pull_request_head_proves_it(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
    head_reported: bool,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await _script_round(pull, tmux_server)
    await _lose_checkpoint_of(pull, "published_sha")
    pushed = await pull.remote_branch()
    local = await _git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
    assert pushed == local
    assert not pull.events.since(0, kinds=[EventKind.FEEDBACK_PUBLISHED], task_id=pull.task.id)
    await _land_remotely(pull, MergeStyle.FAST_FORWARD)
    if head_reported:
        fake_github.set_fields(1, headRefOid=pushed)
    pull.retry(EscalationReason.ENGINE_ERROR)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    present, branch = await _task_state(pull)
    assert (present, branch) == ((False, None) if head_reported else (True, local))


async def test_a_squashed_pull_request_with_published_feedback_is_cleaned_up(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await _open_pull(engine_env, fake_github, tmp_path_factory)
    await _publish_round(pull, tmux_server)
    await _land_remotely(pull, MergeStyle.SQUASH)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    assert await _task_state(pull) == (False, None)
