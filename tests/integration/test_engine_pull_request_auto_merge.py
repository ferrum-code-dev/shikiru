"""Optional GitHub auto-merge of pull requests, observed at the `gh` boundary of a fake `gh`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.domain.reports import ReportKind
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status
from shikiru.engine.integration import PullRequestCloser
from shikiru.engine.pull_request_auto_merge import (
    AutoMergeReadFailed,
    AutoMergeReconciler,
    PullRequestAutoMerge,
)
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.workspace.github import GitHub
from tests.support.engine import build_fault_reporter, set_role_script
from tests.support.fake_gh import (
    ACCEPTED_AUTO_MERGE_HOLD,
    AUTO_MERGE_READ_BUDGET,
    AUTO_MERGE_REQUEST_FIELD,
    STATUS_READ_BUDGET,
    URL_PREFIX,
)
from tests.support.feedback_rounds import (
    REVIEW,
    THREAD,
    WAIT_SECONDS,
    decision,
    feedback_analysis,
    make_auto_merge,
    open_pull,
    verdict,
)
from tests.support.spawn_hook import processes_in_flight
from tests.support.waiting import wait_until, wait_until_advancing

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.feedback_rounds import FeedbackPull
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_PULL_NUMBER = 1
_ACCEPTED_SUBJECT = "feat: fake change"
_ROUND_SUBJECT = "feat: verify feedback"
_AUTO_FLAG = "--auto"
_DISABLE_AUTO_FLAG = "--disable-auto"
_REFUSAL = "auto-merge is not allowed"


def _enable_requests(github: FakeGitHub) -> list[list[str]]:
    return [argv for argv in github.argv("merge") if _AUTO_FLAG in argv]


def _disable_requests(github: FakeGitHub) -> list[list[str]]:
    return [argv for argv in github.argv("merge") if _DISABLE_AUTO_FLAG in argv]


def _auto_merge_service(pull: FeedbackPull) -> PullRequestAutoMerge:
    assert pull.auto_merge is not None
    return pull.auto_merge


def _auto_merge_reads(github: FakeGitHub) -> list[list[str]]:
    return [argv for argv in github.argv("view") if AUTO_MERGE_REQUEST_FIELD in argv]


def _status_reads(github: FakeGitHub) -> list[list[str]]:
    return [argv for argv in github.argv("view") if AUTO_MERGE_REQUEST_FIELD not in argv]


async def _advance_retry_task(pull: FeedbackPull, operation: asyncio.Task[None]) -> None:
    await wait_until_advancing(
        pull.clock,
        operation.done,
        WAIT_SECONDS,
        jump_while=pull.clock.has_pending_sleep,
    )
    await operation


def _fault_kinds(pull: FeedbackPull) -> list[EventKind]:
    return [
        event.kind
        for event in pull.events.since(
            0,
            kinds=(EventKind.ENGINE_FAULT, EventKind.ENGINE_RECOVERED),
            task_id=pull.task.id,
        )
    ]


async def _publish_requested_changes(pull: FeedbackPull, tmux_server: TmuxServer) -> None:
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


async def test_a_disabled_setting_requests_no_merge_for_an_opened_pull_request(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=False)

    assert len(fake_github.pull_requests()) == 1
    assert fake_github.argv("merge") == []
    assert fake_github.auto_merge(_PULL_NUMBER) is None
    await pull.stop()


async def test_an_enabled_setting_requests_a_squash_merge_of_the_published_head_with_its_title(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    record = fake_github.pull_requests()[0]
    head = await pull.remote_branch()
    title = record["title"]

    assert title == f"{_ACCEPTED_SUBJECT} ({pull.task.id})"
    assert _enable_requests(fake_github) == [
        [
            "pr",
            "merge",
            f"{URL_PREFIX}{_PULL_NUMBER}",
            _AUTO_FLAG,
            "--squash",
            "--subject",
            title,
            "--body",
            record["body"],
            "--match-head-commit",
            head,
        ]
    ]
    assert fake_github.auto_merge(_PULL_NUMBER) == {
        "method": "SQUASH",
        "subject": title,
        "body": record["body"],
        "head": head,
    }
    await pull.stop()


async def test_a_republished_round_withdraws_the_request_and_requests_its_new_values(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    assert fake_github.auto_merge(_PULL_NUMBER) is not None
    await _publish_requested_changes(pull, tmux_server)

    await pull.until(pull.flow.done)
    assert fake_github.auto_merge(_PULL_NUMBER) is None

    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    await wait_until(lambda: fake_github.auto_merge(_PULL_NUMBER) is not None, WAIT_SECONDS)
    record = fake_github.pull_requests()[0]
    head = await pull.remote_branch()
    expected_subject = f"{_ROUND_SUBJECT} ({pull.task.id})"

    assert record["title"] == expected_subject
    assert fake_github.auto_merge(_PULL_NUMBER) == {
        "method": "SQUASH",
        "subject": expected_subject,
        "body": record["body"],
        "head": head,
    }
    assert record["body"].startswith("Updated change.")
    await pull.stop()


async def test_a_hold_withdraws_the_request_and_resuming_requests_it_again(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))

    await pull.sweep_auto_merge()

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_disable_requests(fake_github)) == 1
    await pull.stop()

    engine_env.tasks.set_holds(pull.task.id, frozenset())
    await pull.restart()
    await wait_until(lambda: fake_github.auto_merge(_PULL_NUMBER) is not None, WAIT_SECONDS)
    assert len(_enable_requests(fake_github)) == 2
    await pull.stop()


async def test_unchanged_held_tasks_are_reconciled_at_the_pull_request_poll_interval(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    await pull.stop()

    await pull.sweep_auto_merge()
    auto_merge_reads = len(_auto_merge_reads(fake_github))
    assert fake_github.auto_merge(_PULL_NUMBER) is None

    for _ in range(5):
        await pull.sweep_auto_merge()

    assert len(_auto_merge_reads(fake_github)) == auto_merge_reads

    interval = pull.settings.orchestration.network.pr_poll_seconds
    pull.clock.advance(interval - 1)
    await pull.sweep_auto_merge()
    assert len(_auto_merge_reads(fake_github)) == auto_merge_reads

    pull.clock.advance(1)
    await pull.sweep_auto_merge()
    assert len(_auto_merge_reads(fake_github)) == auto_merge_reads + 1

    reconciler = AutoMergeReconciler(
        auto_merge=_auto_merge_service(pull),
        tasks=engine_env.tasks,
        faults=build_fault_reporter(pull.events, pull.clock, pull.settings),
        clock=pull.clock,
        settings=pull.settings.orchestration,
    )
    reads_before_start = len(_auto_merge_reads(fake_github))
    reconciler.start()
    await wait_until(
        lambda: (
            len(_auto_merge_reads(fake_github)) == reads_before_start + 1
            and processes_in_flight() == 0
        ),
        WAIT_SECONDS,
    )
    await reconciler.shutdown()
    await reconciler.sweep()
    assert len(_auto_merge_reads(fake_github)) == reads_before_start + 1


async def test_transient_status_read_failure_is_retried_before_withdrawal(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    fake_github.fail_next(STATUS_READ_BUDGET, 1)
    operation = asyncio.create_task(_auto_merge_service(pull).withdraw(pull.task.id))

    await _advance_retry_task(pull, operation)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_disable_requests(fake_github)) == 1
    assert not pull.escalated()
    assert EventKind.ENGINE_FAULT in _fault_kinds(pull)
    assert EventKind.ENGINE_RECOVERED in _fault_kinds(pull)
    await pull.stop()


async def test_transient_auto_merge_read_failure_is_retried_before_withdrawal(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    fake_github.fail_next(AUTO_MERGE_READ_BUDGET, 1)
    operation = asyncio.create_task(_auto_merge_service(pull).withdraw(pull.task.id))

    await _advance_retry_task(pull, operation)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_disable_requests(fake_github)) == 1
    assert not pull.escalated()
    assert EventKind.ENGINE_FAULT in _fault_kinds(pull)
    assert EventKind.ENGINE_RECOVERED in _fault_kinds(pull)
    await pull.stop()


async def test_a_transient_enable_failure_is_confirmed_and_retried_without_escalation(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    fake_github.set_fields(_PULL_NUMBER, autoMerge=None)
    fake_github.fail_next("auto-merge", 1)
    head = await pull.remote_branch()
    previous_requests = len(_enable_requests(fake_github))
    operation = asyncio.create_task(
        _auto_merge_service(pull).request(pull.task, f"{URL_PREFIX}{_PULL_NUMBER}", head)
    )

    await _advance_retry_task(pull, operation)

    assert len(_enable_requests(fake_github)) == previous_requests + 2
    assert fake_github.auto_merge(_PULL_NUMBER) is not None
    assert not pull.escalated()
    assert EventKind.ENGINE_FAULT in _fault_kinds(pull)
    assert EventKind.ENGINE_RECOVERED in _fault_kinds(pull)
    await pull.stop()


async def test_a_transient_disable_failure_is_confirmed_and_retried_without_escalation(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    fake_github.fail_next("withdraw", 1)
    operation = asyncio.create_task(_auto_merge_service(pull).withdraw(pull.task.id))

    await _advance_retry_task(pull, operation)

    assert len(_disable_requests(fake_github)) == 2
    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert not pull.escalated()
    assert EventKind.ENGINE_FAULT in _fault_kinds(pull)
    assert EventKind.ENGINE_RECOVERED in _fault_kinds(pull)
    await pull.stop()


async def test_a_hold_ends_the_enable_retry_wait_without_sending_another_request(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    await pull.stop()
    fake_github.set_fields(_PULL_NUMBER, autoMerge=None)
    fake_github.fail_next("auto-merge", 1)
    head = await pull.remote_branch()
    previous_requests = len(_enable_requests(fake_github))
    operation = asyncio.create_task(
        _auto_merge_service(pull).request(pull.task, f"{URL_PREFIX}{_PULL_NUMBER}", head)
    )

    await wait_until(
        lambda: (
            len(_enable_requests(fake_github)) == previous_requests + 1
            and pull.clock.has_pending_sleep()
        ),
        WAIT_SECONDS,
    )
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    pull.clock.advance_to_next_sleeper()
    await operation

    assert len(_enable_requests(fake_github)) == previous_requests + 1
    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert not pull.escalated()


async def test_exhausted_auto_merge_state_reads_fail_as_integration_blocked(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    status_reads = len(_status_reads(fake_github))
    fake_github.fail_next(STATUS_READ_BUDGET, 2)
    operation = asyncio.create_task(_auto_merge_service(pull).withdraw(pull.task.id))

    with pytest.raises(AutoMergeReadFailed, match="after 2 attempts"):
        await _advance_retry_task(pull, operation)

    assert len(_status_reads(fake_github)) == status_reads + 2
    assert fake_github.auto_merge(_PULL_NUMBER) is not None
    await pull.stop()


async def test_a_feedback_round_in_progress_withdraws_the_request(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    await _publish_requested_changes(pull, tmux_server)

    await pull.until(pull.flow.done)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_disable_requests(fake_github)) == 1
    assert engine_env.tasks.require(pull.task.id).status is Status.QUEUED
    await pull.stop()


async def test_an_adopted_auto_merge_is_withdrawn_before_first_poll_feedback_runs(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=False)
    await pull.request_changes()
    head = await pull.remote_branch()
    fake_github.set_fields(
        _PULL_NUMBER,
        autoMerge={
            "method": "SQUASH",
            "subject": None,
            "body": "external description",
            "head": head,
        },
    )
    pull.auto_merge = make_auto_merge(
        engine_env, enabled=True, settings=pull.settings, clock=pull.clock
    )

    await pull.restart()
    await pull.until(pull.flow.done)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_disable_requests(fake_github)) == 1
    assert engine_env.tasks.require(pull.task.id).status is Status.QUEUED
    assert not _enable_requests(fake_github)
    await pull.stop()


async def test_a_hold_during_an_inflight_request_withdraws_it_before_releasing_the_lock(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    await _publish_requested_changes(pull, tmux_server)
    await pull.until(pull.flow.done)
    fake_github.hold("merge")
    await pull.dispatch()
    await wait_until(lambda: len(_enable_requests(fake_github)) == 2, WAIT_SECONDS)

    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    withdrawal_started = asyncio.Event()

    async def sweep() -> None:
        withdrawal_started.set()
        await pull.sweep_auto_merge()

    withdrawal = asyncio.create_task(sweep())
    await wait_until(withdrawal_started.is_set, WAIT_SECONDS)
    assert not withdrawal.done()
    fake_github.release("merge")
    await withdrawal
    await pull.until(pull.flow.done)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_enable_requests(fake_github)) == 2
    assert len(_disable_requests(fake_github)) == 2
    await pull.stop()


async def test_an_inflight_request_rechecks_task_state_after_github_accepts_it(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    await _publish_requested_changes(pull, tmux_server)
    await pull.until(pull.flow.done)
    fake_github.hold("merge")
    await pull.dispatch()
    await wait_until(lambda: len(_enable_requests(fake_github)) == 2, WAIT_SECONDS)

    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    fake_github.release("merge")
    await wait_until(lambda: len(_disable_requests(fake_github)) == 2, WAIT_SECONDS)

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    await pull.stop()


async def test_restart_withdraws_a_request_accepted_before_gh_returns(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    await _publish_requested_changes(pull, tmux_server)
    await pull.until(pull.flow.done)
    fake_github.hold(ACCEPTED_AUTO_MERGE_HOLD)
    await pull.dispatch()
    await wait_until(
        lambda: (
            len(_enable_requests(fake_github)) == 2
            and fake_github.auto_merge(_PULL_NUMBER) is not None
        ),
        WAIT_SECONDS,
    )
    assert not pull.flow.done()

    await pull.stop()
    engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))
    fake_github.release(ACCEPTED_AUTO_MERGE_HOLD)
    await pull.restart()
    await pull.sweep_auto_merge()

    assert fake_github.auto_merge(_PULL_NUMBER) is None
    assert len(_enable_requests(fake_github)) == 2
    assert len(_disable_requests(fake_github)) == 2
    await pull.stop()


async def test_a_round_does_not_start_while_its_withdrawal_is_unconfirmed(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    fake_github.fail_next("withdraw", 2)
    await _publish_requested_changes(pull, tmux_server)

    await pull.until(pull.flow.done)

    assert fake_github.auto_merge(_PULL_NUMBER) is not None
    assert ReportKind.FEEDBACK_ANALYSIS.value not in pull.turn_kinds()
    assert pull.escalated()
    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_BLOCKED
    assert "could not be withdrawn" in pull.escalation_question()

    pull.retry(EscalationReason.INTEGRATION_BLOCKED)
    await wait_until(lambda: ReportKind.FEEDBACK_ANALYSIS.value in pull.turn_kinds(), WAIT_SECONDS)
    assert fake_github.auto_merge(_PULL_NUMBER) is None
    await pull.stop()


async def test_a_request_whose_reply_is_lost_is_confirmed_on_github_not_refused(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    fake_github.fail_next("lost-auto-merge", 1)

    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)

    assert len(_enable_requests(fake_github)) == 1
    assert fake_github.auto_merge(_PULL_NUMBER) is not None
    assert not pull.escalated()
    assert pull.waiting()
    await pull.stop()


async def test_a_restart_does_not_repeat_an_identical_request(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    views = fake_github.views()

    await pull.restart()
    await pull.await_poll(views + 1)

    assert len(_enable_requests(fake_github)) == 1
    await pull.stop()


async def test_cancelling_the_task_withdraws_the_request_before_closing_the_pull_request(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory, auto_merge=True)
    await pull.stop()
    closer = PullRequestCloser(
        github=GitHub(SubprocessRunner()),
        events=EventLog(engine_env.connection, engine_env.clock),
        project_root=engine_env.project_root,
        auto_merge=pull.auto_merge,
    )

    await closer.close_open(pull.task.id)

    record = fake_github.pull_requests()[0]
    assert record["state"] == "CLOSED"
    assert record["autoMerge"] is None
    assert len(_disable_requests(fake_github)) == 1


async def test_a_refused_request_escalates_with_the_reason_and_a_retry_requests_again(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(
        engine_env, fake_github, tmp_path_factory, auto_merge=True, auto_merge_attempts=2
    )
    fake_github.fail_next("auto-merge", 2)
    await _publish_requested_changes(pull, tmux_server)
    await pull.until(pull.flow.done)
    await pull.dispatch()

    await pull.until(pull.flow.done)

    [escalation] = fetch_open_escalations(engine_env.connection)
    assert escalation.reason is EscalationReason.INTEGRATION_BLOCKED
    assert _REFUSAL in pull.escalation_question()
    assert fake_github.auto_merge(_PULL_NUMBER) is None

    pull.retry(EscalationReason.INTEGRATION_BLOCKED)
    await wait_until(lambda: fake_github.auto_merge(_PULL_NUMBER) is not None, WAIT_SECONDS)
    await pull.stop()
