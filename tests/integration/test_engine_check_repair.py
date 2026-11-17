"""Check repair rounds against fake GitHub, real Git and fake agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from shikiru.domain.check_repair import CheckRepairStage
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status
from shikiru.store.check_repair import CheckRepairStore
from tests.support.engine import set_role_script
from tests.support.fake_gh import URL_PREFIX
from tests.support.feedback_rounds import (
    REVIEW,
    THREAD,
    WAIT_SECONDS,
    analysis_prompts,
    decision,
    feedback_analysis,
    land_remotely,
    open_pull,
    verdict,
)
from tests.support.github import MergeStyle
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_FAIL = {
    "__typename": "CheckRun",
    "name": "unit tests",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": "https://checks.test/1",
}
_PENDING = {"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS"}


async def _fail(pull: Any, github: FakeGitHub) -> None:
    head = await pull.remote_branch()
    github.set_fields(1, headRefOid=head, statusCheckRollup=[_FAIL])


async def test_check_repair_is_published_and_merged(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [feedback_analysis(decision("check-0", "address")), verdict("accept", "check-0")],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    fixed = await pull.remote_branch()
    assert fixed != before
    assert "unit tests" in analysis_prompts(engine_env, pull.task.id)[-1]
    assert "https://checks.test/1" in analysis_prompts(engine_env, pull.task.id)[-1]
    assert pull.replies() == []
    [repair] = CheckRepairStore(engine_env.connection).rounds(pull.task.id, f"{URL_PREFIX}1")
    assert repair.stage is CheckRepairStage.PUBLISHED
    assert repair.verified_sha == fixed
    assert repair.published_sha == fixed
    fake_github.set_fields(1, headRefOid=fixed, statusCheckRollup=[])
    await land_remotely(pull, MergeStyle.MERGE_COMMIT)
    await pull.until(pull.flow.done)
    assert engine_env.tasks.require(pull.task.id).status is Status.DONE


async def test_pending_checks_and_restart_do_not_duplicate_attempts(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    head = await pull.remote_branch()
    fake_github.set_fields(1, headRefOid=head, statusCheckRollup=[_FAIL, _PENDING])
    await pull.poll_again()
    assert pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1") == []
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    url = f"{URL_PREFIX}1"
    assert len(pull.store.check_rounds(pull.task.id, url)) == 1
    await pull.restart()
    assert len(pull.store.check_rounds(pull.task.id, url)) == 1
    await pull.stop()


async def test_unavailable_check_evidence_escalates_without_attempt(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    fake_github.set_fields(1, headRefOid=None, statusCheckRollup=[_FAIL])
    await pull.until(pull.flow.done)
    assert pull.escalated()
    assert pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1") == []
    assert "head" in pull.escalation_question().lower()


async def test_external_failure_escalates_with_reason_and_evidence(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            feedback_analysis(
                decision(
                    "check-0", "needs_operator", detail="CI provider outage confirmed", reply=""
                )
            )
        ],
    )
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)
    question = pull.escalation_question()
    assert "CI provider outage confirmed" in question
    assert "unit tests" in question
    assert "https://checks.test/1" in question


async def test_repeated_failures_exhaust_only_after_checks_on_each_published_head(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    limit = pull.settings.orchestration.pr_check_max_attempts
    leader_steps: list[dict[str, Any]] = []
    for _ in range(limit):
        leader_steps.extend(
            [feedback_analysis(decision("check-0", "address")), verdict("accept", "check-0")]
        )
    await set_role_script(tmux_server, Role.LEADER, leader_steps)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}] * limit)
    await _fail(pull, fake_github)
    url = f"{URL_PREFIX}1"
    for attempt in range(limit):
        await pull.until(pull.flow.done)
        assert len(pull.store.check_rounds(pull.task.id, url)) == attempt + 1
        await pull.dispatch()
        await wait_until(
            lambda expected=attempt + 1: (
                pull.waiting()
                and len(pull.store.check_rounds(pull.task.id, url)) == expected
                and pull.store.check_rounds(pull.task.id, url)[-1].published_sha is not None
            ),
            WAIT_SECONDS,
        )
        await pull.poll_again()
        assert len(pull.store.check_rounds(pull.task.id, url)) == attempt + 1
        fixed = await pull.remote_branch()
        fake_github.set_fields(1, headRefOid=fixed, statusCheckRollup=[_FAIL])
    await pull.until(pull.flow.done)
    assert pull.escalated()
    question = pull.escalation_question()
    assert "exhausted" in question
    assert str(limit) in question
    assert "unit tests" in question
    assert "https://checks.test/1" in question


async def test_reconciles_pushed_fix_after_publication_checkpoint_is_lost(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [feedback_analysis(decision("check-0", "address")), verdict("accept", "check-0")],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    engine_env.connection.execute("""
        CREATE TRIGGER fail_check_publication BEFORE UPDATE OF published_sha ON feedback_rounds
        BEGIN SELECT RAISE(ABORT, 'checkpoint lost'); END
    """)
    await pull.dispatch()
    await wait_until(pull.flow.done, WAIT_SECONDS)
    engine_env.connection.execute("DROP TRIGGER fail_check_publication")
    fixed = await pull.remote_branch()
    fake_github.set_fields(1, headRefOid=fixed, statusCheckRollup=[_PENDING])
    pull.retry(EscalationReason.ENGINE_ERROR)
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    assert await pull.remote_branch() == fixed
    assert len(pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1")) == 1
    assert pull.replies() == []
    await pull.stop()


async def test_changed_remote_head_stops_check_repair_before_push(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    await set_role_script(
        tmux_server, Role.LEADER, [feedback_analysis(decision("check-0", "address"))]
    )
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    fake_github.set_fields(1, headRefOid="someone-elses-head")
    await pull.dispatch()
    await wait_until(pull.escalated, WAIT_SECONDS)
    assert "someone-elses-head" in pull.escalation_question()
    assert await pull.remote_branch() == before


@pytest.mark.parametrize("conflicting", [True, False], ids=["conflict", "wait budget"])
async def test_conflicts_and_wait_budget_stop_before_repair(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    conflicting: bool,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    head = await pull.remote_branch()
    fake_github.set_fields(
        1,
        headRefOid=head,
        mergeable="CONFLICTING" if conflicting else "MERGEABLE",
        statusCheckRollup=[_FAIL, _PENDING],
    )
    if not conflicting:
        pull.clock.advance(pull.settings.orchestration.timeouts.pr_wait)
    await pull.until(pull.flow.done)
    assert pull.escalated()
    assert pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1") == []
    question = pull.escalation_question()
    assert "conflict" in question.lower() if conflicting else "wait budget" in question.lower()


async def test_review_feedback_finishes_before_check_repair(
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
            feedback_analysis(decision("check-0", "address")),
            verdict("accept", "check-0"),
        ],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}] * 2)
    fake_github.set_fields(1, statusCheckRollup=[_FAIL])
    await pull.until(pull.flow.done)
    active = pull.store.active(pull.task.id)
    assert active is not None
    assert active.snapshot.sources
    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    assert len(pull.replies()) == 2
    fake_github.set_fields(1, headRefOid=await pull.remote_branch(), statusCheckRollup=[_FAIL])
    await pull.until(pull.flow.done)
    await pull.dispatch()
    await wait_until(
        lambda: (
            bool(pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1"))
            and pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1")[-1].published_sha
            is not None
            and pull.waiting()
        ),
        WAIT_SECONDS,
    )
    assert len(pull.replies()) == 2
    await pull.stop()


async def test_hold_stops_a_queued_repair_until_resumed(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [feedback_analysis(decision("check-0", "address")), verdict("accept", "check-0")],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    before = await pull.remote_branch()
    await _fail(pull, fake_github)
    await pull.until(pull.flow.done)
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    await pull.dispatch()
    await pull.until(pull.flow.done)
    assert await pull.remote_branch() == before
    assert len(pull.store.check_rounds(pull.task.id, f"{URL_PREFIX}1")) == 1
    engine_env.tasks.set_holds(pull.task.id, frozenset())
    await pull.restart()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    assert await pull.remote_branch() != before
    await pull.stop()
