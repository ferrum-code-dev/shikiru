"""PR feedback rounds end to end: fake `gh`, a bare remote and fake agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from shikiru.config.settings import ANY_COMMENT_FEEDBACK
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.events import EventKind
from shikiru.domain.feedback import (
    FeedbackMessage,
    FeedbackSnapshot,
    FeedbackSource,
    FeedbackSourceKind,
)
from shikiru.domain.feedback_rounds import FeedbackStage, reply_marker
from shikiru.domain.reports import ReportKind
from shikiru.domain.tasks import EscalationReason, Role, Status
from shikiru.store.escalations import fetch_open_escalations
from shikiru.workspace.github import PullRequestState
from tests.support.engine import set_role_script
from tests.support.fake_gh import URL_PREFIX
from tests.support.github import (
    MergeStyle,
    comment_node,
    conversation_node,
    merge_on_remote,
    review_node,
    thread_node,
)
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

from tests.support.feedback_rounds import (
    OTHER_COMMIT,
    REVIEW,
    THREAD,
    WAIT_SECONDS,
    analysis_prompts,
    decision,
    feedback_analysis,
    git,
    open_pull,
    operator_decision_section,
    verdict,
)

CLARIFIED = "T2"
FIRST_ANSWER = "use the new API"
SECOND_ANSWER = "use the v2 API"
LATER_REVIEW = "R2"


async def test_an_accepted_change_is_published_while_a_clarify_thread_gets_its_question(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await pull.request_changes(with_thread=False)
    fake_github.set_fields(
        1,
        reviewThreads=[
            thread_node(THREAD, [comment_node(1, 11, "rename it", review_id=REVIEW)]),
            thread_node(CLARIFIED, [comment_node(1, 12, "use the palette", review_id=REVIEW)]),
        ],
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(
                decision(REVIEW, "disagree"),
                decision(THREAD, "address"),
                decision(CLARIFIED, "clarify"),
            ),
            verdict("accept", REVIEW, THREAD, CLARIFIED),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)

    after = await pull.remote_branch()
    assert after != before
    assert not pull.escalated()
    [fix] = pull.thread_replies(THREAD)
    assert f"Fixed in {after[:12]}" in fix
    [question] = pull.thread_replies(CLARIFIED)
    assert "Fixed in" not in question
    assert len(pull.replies()) == 3
    await pull.stop()


async def test_requested_changes_are_reworked_published_and_answered(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
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
    queued = engine_env.tasks.require(pull.task.id)
    assert queued.status is Status.QUEUED
    assert queued.slot is None
    assert queued.wait_reason is None

    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)

    after = await pull.remote_branch()
    assert after != before
    local = await git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
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
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
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


async def test_any_comment_scope_starts_a_round_for_a_commenting_review_and_its_discussion(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, pr_feedback=ANY_COMMENT_FEEDBACK
    )
    fake_github.set_fields(
        1,
        headRefOid=await pull.remote_branch(),
        reviews=[review_node(1, REVIEW, "COMMENTED", body="a remark")],
        reviewThreads=[
            thread_node(THREAD, [comment_node(1, 11, "rename it", review_id=REVIEW)]),
        ],
    )
    await pull.until(lambda: pull.store.active(pull.task.id) is not None)

    started = pull.store.active(pull.task.id)
    assert started is not None
    assert started.review_ids == [REVIEW]
    assert started.comment_ids == ["PRRC_11"]
    await pull.stop()


async def _handled_round(pull: Any) -> None:
    url = f"{URL_PREFIX}1"
    round_ = pull.store.start(
        task_id=pull.task.id,
        pull_request_url=url,
        review_ids=[REVIEW],
        comment_ids=["PRRC_11"],
        snapshot=FeedbackSnapshot(
            pull_request_url=url,
            head_sha="sha",
            sources=[
                FeedbackSource(
                    id=THREAD,
                    kind=FeedbackSourceKind.DISCUSSION,
                    url="u",
                    messages=[FeedbackMessage(author="reviewer", body="rename it", url="u")],
                )
            ],
        ),
        expected_remote_sha="sha",
    )
    pull.store.set_stage(round_.id, FeedbackStage.HANDLED)


async def _discuss(pull: Any, *comments: dict[str, Any], review_state: str, resolved: bool) -> None:
    pull.github.set_fields(
        1,
        headRefOid=await pull.remote_branch(),
        reviews=[review_node(1, REVIEW, review_state, body="please fix")],
        reviewThreads=[
            thread_node(
                THREAD,
                [comment_node(1, 11, "rename it", review_id=REVIEW), *comments],
                resolved=resolved,
            )
        ],
    )


async def test_a_new_reply_in_a_change_request_discussion_starts_a_round_with_the_discussion_whole(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await _handled_round(pull)
    ours = comment_node(1, 12, reply_marker(pull.task.id, 1, THREAD))

    await _discuss(pull, ours, review_state="CHANGES_REQUESTED", resolved=True)
    fake_github.set_fields(1, conversationComments=[conversation_node(1, 90, "also, thanks")])
    for _ in range(3):
        await pull.poll_again()
    assert pull.store.active(pull.task.id) is None

    await _discuss(
        pull,
        ours,
        comment_node(1, 13, "still wrong"),
        review_state="CHANGES_REQUESTED",
        resolved=True,
    )
    await pull.until(lambda: pull.store.active(pull.task.id) is not None)

    started = pull.store.active(pull.task.id)
    assert started is not None
    assert started.review_ids == []
    assert started.comment_ids == ["PRRC_13"]
    [source] = started.snapshot.sources
    assert source.resolved
    assert [message.new for message in source.messages] == [False, False, True]
    await pull.stop()


async def test_a_reply_in_a_discussion_whose_review_was_dismissed_starts_no_round(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await _handled_round(pull)

    await _discuss(
        pull, comment_node(1, 13, "still wrong"), review_state="DISMISSED", resolved=False
    )
    for _ in range(3):
        await pull.poll_again()

    assert pull.store.active(pull.task.id) is None
    assert pull.waiting()
    await pull.stop()


@pytest.mark.parametrize("action", ["disagree", "clarify"])
async def test_a_round_without_accepted_work_replies_without_publishing(
    action: str,
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [feedback_analysis(decision(REVIEW, action)), verdict("accept", REVIEW)],
    )
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)

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
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(
                decision(REVIEW, "needs_operator", detail="which API do you want?", reply="")
            )
        ],
    )
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    question = pull.escalation_question()
    assert "which API do you want?" in question
    assert REVIEW in question
    assert pull.replies() == []
    assert pull.store.active(pull.task.id) is not None


async def test_a_closed_pull_request_during_a_round_escalates_and_a_retry_opens_a_new_one(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server, Role.LEADER, [feedback_analysis(decision(REVIEW, "disagree"))]
    )
    await pull.until(pull.flow.done)
    fake_github.set_state(1, PullRequestState.CLOSED)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.PR_CLOSED
    assert pull.store.active(pull.task.id) is None
    assert pull.replies() == []
    assert engine_env.tasks.require(pull.task.id).repo is not None
    pull.retry(EscalationReason.PR_CLOSED)
    await wait_until(lambda: len(fake_github.pull_requests()) == 2 and pull.waiting(), WAIT_SECONDS)
    await pull.stop()


async def test_a_push_that_loses_the_lease_keeps_both_sides_and_sends_no_replies(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    assert pull.task.repo is not None
    branch = pull.task.repo.branch
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
    tip = await pull.remote_branch()
    tree = await git(engine_env.project_root, "rev-parse", f"{tip}^{{tree}}")
    other = await git(engine_env.project_root, "commit-tree", tree, "-p", tip, "-m", OTHER_COMMIT)
    await git(
        engine_env.project_root, "push", pull.settings.repo.remote, f"{other}:refs/heads/{branch}"
    )
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    assert await pull.remote_branch() == other
    assert pull.replies() == []
    assert pull.store.active(pull.task.id) is not None


async def test_changes_that_stay_rejected_escalate_after_the_attempt_limit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, max_iterations=1)
    before = await pull.remote_branch()
    await pull.request_changes()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "disagree"), decision(THREAD, "address")),
            verdict("changes_requested"),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    question = pull.escalation_question()
    assert "not resolved after 1 implementation attempts" in question
    assert THREAD in question
    assert await pull.remote_branch() == before
    assert pull.replies() == []


async def test_an_operator_answer_reruns_the_analysis_and_only_that_analysis_sees_it(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "needs_operator", detail="which API?", reply="")),
            feedback_analysis(decision(REVIEW, "address")),
            verdict("accept", REVIEW),
            feedback_analysis(decision(LATER_REVIEW, "disagree")),
            verdict("accept", LATER_REVIEW),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.FEEDBACK_NEEDS_OPERATOR
    assert {option.label for option in escalation.options} == {
        ResolutionKind.ANSWER,
        ResolutionKind.CANCEL,
    }

    await pull.answer(FIRST_ANSWER)
    await pull.until(lambda: pull.handled() and pull.waiting())

    first, second = analysis_prompts(engine_env, pull.task.id)
    assert operator_decision_section(first) == ""
    assert FIRST_ANSWER in operator_decision_section(second)
    kinds = pull.turn_kinds()[2:]
    assert kinds[:3] == [
        ReportKind.FEEDBACK_ANALYSIS.value,
        ReportKind.FEEDBACK_ANALYSIS.value,
        ReportKind.FEEDBACK_REWORK.value,
    ]
    assert ReportKind.CONSULT.value not in kinds
    assert len(pull.replies()) == 1

    sha = await pull.remote_branch()
    fake_github.set_fields(
        1,
        headRefOid=sha,
        reviews=[review_node(1, LATER_REVIEW, "CHANGES_REQUESTED", body="one more")],
        reviewThreads=[],
    )
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await pull.until(
        lambda: (
            len(pull.events.since(0, kinds=[EventKind.FEEDBACK_HANDLED])) == 2 and pull.waiting()
        )
    )

    later = analysis_prompts(engine_env, pull.task.id)[-1]
    assert operator_decision_section(later) == ""
    await pull.stop()


async def test_an_answer_reaches_its_analysis_after_a_failed_attempt_and_a_restart(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "needs_operator", detail="which API?", reply="")),
            {"kind": "fail", "failure_kind": "auth"},
            feedback_analysis(decision(REVIEW, "address")),
            verdict("accept", REVIEW),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    await pull.answer(FIRST_ANSWER)
    await wait_until(pull.escalated, WAIT_SECONDS)
    [failure] = fetch_open_escalations(engine_env.connection)
    assert failure.reason is EscalationReason.AGENT_AUTH

    pull.retry(EscalationReason.AGENT_AUTH)
    await pull.until(lambda: pull.handled() and pull.waiting())

    assert FIRST_ANSWER in operator_decision_section(analysis_prompts(engine_env, pull.task.id)[-1])
    await pull.stop()


async def test_a_new_question_after_an_answer_is_answerable_and_replaces_the_answer(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await pull.request_changes(with_thread=False)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(decision(REVIEW, "needs_operator", detail="which API?", reply="")),
            feedback_analysis(
                decision(REVIEW, "needs_operator", detail="which version?", reply="")
            ),
            feedback_analysis(decision(REVIEW, "address")),
            verdict("accept", REVIEW),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)

    await pull.answer(FIRST_ANSWER)
    await wait_until(pull.escalated, WAIT_SECONDS)
    [question] = fetch_open_escalations(engine_env.connection)
    assert question.reason is EscalationReason.FEEDBACK_NEEDS_OPERATOR
    assert ResolutionKind.ANSWER in {option.label for option in question.options}

    await pull.answer(SECOND_ANSWER)
    await pull.until(lambda: pull.handled() and pull.waiting())

    _, _, last = analysis_prompts(engine_env, pull.task.id)
    assert SECOND_ANSWER in operator_decision_section(last)
    assert FIRST_ANSWER not in operator_decision_section(last)
    await pull.stop()
