"""PR feedback rounds end to end: fake `gh`, a bare remote and fake agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.feedback_rounds import FeedbackStage
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    FeedbackAnalysis,
    ImplementationReport,
    ReportKind,
)
from shikiru.domain.tasks import EscalationReason, Role, Step
from shikiru.paths import task_root, turns_dir
from shikiru.store.escalations import fetch_open_escalations
from tests.support.engine import set_role_script
from tests.support.git_repo import COMMIT_IDENTITY_ARGS
from tests.support.github import (
    MergeStyle,
    merge_on_remote,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    import pytest

    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

from tests.support.feedback_rounds import (
    BASE_FILE,
    REVIEW,
    RUNNER,
    THREAD,
    WAIT_SECONDS,
    decision,
    feedback_analysis,
    git,
    open_pull,
    verdict,
)


async def test_an_interrupted_reply_is_found_by_its_marker_instead_of_posted_twice(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "address")),
            verdict("accept", REVIEW, THREAD),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    fake_github.fail_next("lost-post", 1)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    assert len(pull.replies()) == 1
    assert not pull.handled()
    pull.retry(EscalationReason.INTEGRATION_BLOCKED)
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)

    assert len(pull.replies()) == 2
    assert len(set(pull.replies())) == 2
    await pull.stop()


async def test_a_feedback_rebase_is_verified_again_and_the_landing_keeps_every_commit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, task_commit=True)
    assert pull.task.repo is not None
    branch = pull.task.repo.branch
    remote = pull.settings.repo.remote
    root = engine_env.project_root
    (root / BASE_FILE).write_text("base\n")
    await git(root, "add", BASE_FILE)
    await git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base moved")
    await git(root, "push", remote, pull.task.repo.base)
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
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)

    started = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    verdicts = [e for e in started if e.data.get("report_kind") == "feedback_verdict"]
    assert len(verdicts) == 2
    assert await pull.remote_branch() == await git(root, "rev-parse", branch)

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
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "disagree")),
            verdict("accept", REVIEW, THREAD),
        ],
    )
    await pull.until(pull.flow.done)
    fake_github.close_on_next_graphql()
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.PR_CLOSED
    assert not any(f":{THREAD} " in reply for reply in pull.replies())


async def test_merged_feedback_recovers_prepared_landing_after_publication_checkpoint_loss(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, task_commit=True)
    assert pull.task.repo is not None
    root = engine_env.project_root
    (root / BASE_FILE).write_text("base\n")
    await git(root, "add", BASE_FILE)
    await git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base moved")
    await git(root, "push", pull.settings.repo.remote, pull.task.repo.base)
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
    engine_env.connection.execute("""
        CREATE TRIGGER fail_published BEFORE UPDATE OF published_sha ON feedback_rounds
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, WAIT_SECONDS)
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
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, max_iterations=1)
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "address")),
            verdict("accept", REVIEW, THREAD),
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
    await wait_until(pull.flow.done, WAIT_SECONDS)
    active = pull.store.active(pull.task.id)
    assert active is not None
    assert active.attempts == 0
    engine_env.connection.execute("DROP TRIGGER fail_verification")
    pull.retry(EscalationReason.ENGINE_ERROR)
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
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
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    assert pull.task.repo is not None
    root = engine_env.project_root
    worktree = task_root(root, pull.task.id)
    (worktree / "README.md").write_text("task change\n")
    await git(worktree, "add", "README.md")
    await git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "feedback work")
    verified = await git(worktree, "rev-parse", pull.task.repo.branch)
    (root / "README.md").write_text("base change\n")
    await git(root, "add", "README.md")
    await git(root, *COMMIT_IDENTITY_ARGS, "commit", "-m", "base change")
    await git(root, "push", pull.settings.repo.remote, pull.task.repo.base)
    await pull.request_changes()
    await pull.until(pull.flow.done)
    active = pull.store.active(pull.task.id)
    assert active is not None
    analysis = FeedbackAnalysis.model_validate(
        feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "address"))["report"]
    )
    pull.store.save_analysis(active.id, analysis, FeedbackStage.PUBLICATION)
    pull.store.save_verified(
        active.id,
        sha=verified,
        replies={REVIEW: "final", THREAD: "final"},
        stage=FeedbackStage.PUBLICATION,
    )
    result = await RUNNER.run(["git", "rebase", "main"], cwd=worktree, timeout=10)
    assert result.returncode != 0
    (worktree / "README.md").write_text("recovered resolution\n")
    await git(worktree, "add", "README.md")
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
            verdict("accept", REVIEW, THREAD),
        ],
    )
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    assert (worktree / "README.md").read_text() == "recovered resolution\n"
    turns = pull.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=pull.task.id)
    assert not any(
        event.data.get("role") == "implementer" and event.id > repair.id for event in turns
    )
    await pull.stop()
