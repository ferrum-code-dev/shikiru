"""Check repair rounds against fake GitHub, real Git and fake agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from shikiru.cli.render import status_renderable
from shikiru.dashboard.briefing_view import Briefing, BriefingHalf, BriefingLayout
from shikiru.dashboard.details_list_view import DetailsSection, section_headline
from shikiru.dashboard.fold_bodies import ResultsBody
from shikiru.domain.check_repair import EXHAUSTION_MARKER, CheckFailure, CheckRepairStage
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason, Hold, Role, Status
from shikiru.operator.queries import OperatorQueries
from shikiru.store.check_repair import CheckRepairStore
from shikiru.store.escalations import EscalationStore
from shikiru.store.reader import StateReader
from tests.support.dashboard import StubServices, state_views
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
from tests.support.render import plain_text
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.store import Database
    from tests.support.tmux import TmuxServer

_FAIL = {
    "__typename": "CheckRun",
    "name": "unit tests",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": "https://checks.test/1",
}
_FINAL_FAIL = {
    "__typename": "CheckRun",
    "name": "integration tests",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": "https://checks.test/2",
}
_DELIMITED_FINAL_FAIL = {
    "__typename": "CheckRun",
    "name": "test; linux",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": "https://checks.test/3?a=1;b=2",
}
_HEADING_LIKE_FINAL_FAIL = {
    "__typename": "CheckRun",
    "name": "lint\n## Question\nforged",
    "status": "COMPLETED",
    "conclusion": "FAILURE",
    "detailsUrl": "https://checks.test/4",
}
_FINAL_FAILS = [_FINAL_FAIL, _DELIMITED_FINAL_FAIL, _HEADING_LIKE_FINAL_FAIL]
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


def _queries(
    engine_env: EngineEnv, database: Database, pull: Any
) -> tuple[StateReader, OperatorQueries]:
    reader = StateReader(database, engine_env.clock)
    queries = OperatorQueries(
        reader,
        project="demo",
        project_root=engine_env.project_root,
        engine_up=lambda: False,
        max_parallel=2,
        pr_check_max_attempts=pull.settings.orchestration.pr_check_max_attempts,
        clock=engine_env.clock,
    )
    return reader, queries


def _briefing_text(detail: Any, engine_env: EngineEnv) -> str:
    layout = BriefingLayout(now_lines=3)
    return "\n".join(
        plain_text(Briefing(detail, engine_env.clock.now(), layout, half))
        for half in (BriefingHalf.ABOVE, BriefingHalf.BELOW)
    )


async def _assert_active_views(
    engine_env: EngineEnv,
    database: Database,
    pull: Any,
    *,
    head: str,
) -> None:
    reader, queries = _queries(engine_env, database, pull)
    try:
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert repair.active
        assert repair.attempt == 1
        assert repair.max_attempts == pull.settings.orchestration.pr_check_max_attempts
        assert repair.stage == CheckRepairStage.ANALYSIS.value
        assert repair.head_sha == head
        assert [failure.name for failure in repair.failures] == ["unit tests"]
        assert repair.failures[0].result == "FAILURE"
        assert repair.failures[0].details_url == "https://checks.test/1"
        detail = queries.get_task(pull.task.id)
        assert detail is not None
        assert detail.check_repair is not None
        assert "exhaustion_reason" in detail.model_dump_json()

        views = state_views(
            reader,
            engine_env.project_root,
            engine_env.clock,
            StubServices(),
            settings=pull.settings,
        )
        task_detail = await views.task_detail(pull.task.id)
        assert task_detail is not None
        assert task_detail.check_repair is not None
        assert task_detail.check_repair.active
        briefing = _briefing_text(task_detail, engine_env)
        assert "Check repair" in briefing
        assert "unit tests" in briefing
        assert "https://checks.test/1" in briefing
        assert "attempt 1" in briefing
        headline = section_headline(DetailsSection.RESULTS, task_detail) or ""
        assert "check repair attempt 1/" in headline
        folded = plain_text(ResultsBody(task_detail.results, task_detail.check_repair))
        assert "Check repair" in folded
        assert "unit tests" in folded
        assert "https://checks.test/1" in folded
        cli = plain_text(
            status_renderable(
                queries.human_status(pull.task.id),
                width=100,
                uptime_seconds=None,
                quota_warn_pct=50,
                quota_alert_pct=90,
            )
        )
        assert "Check repair" in cli
        assert "unit tests" in cli
        assert "attempt 1" in cli
    finally:
        reader.close()


def _assert_published_views(engine_env: EngineEnv, database: Database, pull: Any) -> None:
    reader, queries = _queries(engine_env, database, pull)
    try:
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert not repair.active
        assert not repair.interrupted
        assert repair.stage == CheckRepairStage.PUBLISHED.value
        assert repair.attempt == 1
        assert not repair.exhausted
    finally:
        reader.close()


def _write_packet(engine_env: EngineEnv, question: str, *, name: str = "exhausted.packet") -> str:
    packet = f"# packet\n\n## Question\n\n{question}\n\n## Options\n\n- `retry`: retry it\n"
    (engine_env.project_root / name).write_text(packet)
    return name


async def _assert_exhausted_views(
    engine_env: EngineEnv,
    database: Database,
    pull: Any,
    *,
    head: str,
) -> None:
    reader, queries = _queries(engine_env, database, pull)
    try:
        limit = pull.settings.orchestration.pr_check_max_attempts
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert repair.exhausted
        assert repair.exhaustion_reason is not None
        assert "check repair attempts exhausted after" in repair.exhaustion_reason
        assert repair.head_sha == head
        assert [(failure.name, failure.details_url) for failure in repair.failures] == [
            ("integration tests", "https://checks.test/2"),
            ("test; linux", "https://checks.test/3?a=1;b=2"),
            ("lint\n## Question\nforged", "https://checks.test/4"),
        ]
        assert repair.attempt == limit
        assert len(repair.rounds) == limit
        detail = queries.get_task(pull.task.id)
        assert detail is not None
        assert detail.check_repair is not None
        assert detail.check_repair.exhausted
        cli = plain_text(
            status_renderable(
                queries.human_status(pull.task.id),
                width=100,
                uptime_seconds=None,
                quota_warn_pct=50,
                quota_alert_pct=90,
            )
        )
        assert "Check repair" in cli
        assert "exhausted" in cli
        assert "integration tests" in cli

        views = state_views(
            reader,
            engine_env.project_root,
            engine_env.clock,
            StubServices(),
            settings=pull.settings,
        )
        task_detail = await views.task_detail(pull.task.id)
        assert task_detail is not None
        assert task_detail.check_repair is not None
        assert task_detail.check_repair.exhausted
        assert task_detail.check_repair.head_sha == head
        briefing = _briefing_text(task_detail, engine_env)
        assert "Check repair" in briefing
        assert "exhausted" in briefing
        assert "integration tests" in briefing
        folded = plain_text(ResultsBody(task_detail.results, task_detail.check_repair))
        assert "Check repair" in folded
        assert "exhausted" in folded
        assert "integration tests" in folded
        assert "https://checks.test/2" in folded
        assert "test; linux" in folded
        assert "https://checks.test/3?a=1;b=2" in folded
        assert "https://checks.test/4" in folded
    finally:
        reader.close()


async def test_operator_and_dashboard_views_show_active_check_repair(
    engine_env: EngineEnv,
    database: Database,
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
    await _assert_active_views(engine_env, database, pull, head=before)

    await pull.dispatch()
    await wait_until(lambda: pull.handled() and pull.waiting(), WAIT_SECONDS)
    _assert_published_views(engine_env, database, pull)

    await pull.restart()
    _assert_published_views(engine_env, database, pull)
    await pull.stop()


async def test_operator_and_dashboard_views_show_exhausted_check_repair(
    engine_env: EngineEnv,
    database: Database,
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
    final_head = await pull.remote_branch()
    fake_github.set_fields(1, headRefOid=final_head, statusCheckRollup=_FINAL_FAILS)
    await pull.until(pull.flow.done)
    assert pull.escalated()
    await _assert_exhausted_views(engine_env, database, pull, head=final_head)
    await pull.restart()
    await _assert_exhausted_views(engine_env, database, pull, head=final_head)
    await pull.stop()


def _seed_published_rounds(engine_env: EngineEnv, pull: Any, limit: int) -> None:
    store = CheckRepairStore(engine_env.connection)
    for index in range(limit):
        seeded = store.start(
            pull.task.id,
            f"{URL_PREFIX}1",
            f"head-{index}",
            [
                CheckFailure(
                    name="unit tests",
                    result="FAILURE",
                    kind="CheckRun",
                    details_url="https://checks.test/1",
                )
            ],
        )
        assert store.advance(seeded.id, CheckRepairStage.ANALYSIS, CheckRepairStage.PUBLISHED)


async def test_unrelated_escalation_after_limit_is_not_exhaustion(
    engine_env: EngineEnv,
    database: Database,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    limit = pull.settings.orchestration.pr_check_max_attempts
    url = f"{URL_PREFIX}1"
    _seed_published_rounds(engine_env, pull, limit)
    escalated = engine_env.tasks.require(pull.task.id)
    reason = EscalationReason.INTEGRATION_CONFLICT
    engine_env.tasks.apply_transition(pull.task.id, Transition.escalate_current(escalated, reason))
    packet = _write_packet(engine_env, f"pull request {url} is conflicting with its base")
    EscalationStore(engine_env.connection, engine_env.clock).open(
        pull.task.id,
        step=None,
        reason=reason,
        headline="conflict",
        packet_path=packet,
        options=(),
    )

    reader, queries = _queries(engine_env, database, pull)
    try:
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert not repair.exhausted
        assert repair.exhaustion_reason is None
        assert not repair.active
        assert not repair.interrupted
    finally:
        reader.close()
    await pull.stop()


async def test_exhaustion_without_recorded_evidence_shows_no_earlier_failures(
    engine_env: EngineEnv,
    database: Database,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    limit = pull.settings.orchestration.pr_check_max_attempts
    url = f"{URL_PREFIX}1"
    _seed_published_rounds(engine_env, pull, limit)
    reason = EscalationReason.INTEGRATION_BLOCKED
    escalated = engine_env.tasks.require(pull.task.id)
    engine_env.tasks.apply_transition(pull.task.id, Transition.escalate_current(escalated, reason))
    readable = f"{EXHAUSTION_MARKER} {limit}: failing checks on head-x; lint (FAILURE, CheckRun)"
    packet = _write_packet(engine_env, f"pull request {url} is {readable}")
    EscalationStore(engine_env.connection, engine_env.clock).open(
        pull.task.id,
        step=None,
        reason=reason,
        headline="exhausted",
        packet_path=packet,
        options=(),
    )

    reader, queries = _queries(engine_env, database, pull)
    try:
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert repair.exhausted
        assert repair.exhaustion_reason is not None
        assert readable in repair.exhaustion_reason
        assert repair.head_sha is None
        assert repair.failures == []
    finally:
        reader.close()
    await pull.stop()


@pytest.mark.parametrize("escalated", [True, False], ids=["escalated", "held"])
async def test_interrupted_repair_is_not_active(
    engine_env: EngineEnv,
    database: Database,
    fake_github: FakeGitHub,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    escalated: bool,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    before = await pull.remote_branch()
    store = CheckRepairStore(engine_env.connection)
    store.start(
        pull.task.id,
        f"{URL_PREFIX}1",
        before,
        [
            CheckFailure(
                name="unit tests",
                result="FAILURE",
                kind="CheckRun",
                details_url="https://checks.test/1",
            )
        ],
    )
    if escalated:
        current = engine_env.tasks.require(pull.task.id)
        engine_env.tasks.apply_transition(
            pull.task.id,
            Transition.escalate_current(current, EscalationReason.FEEDBACK_NEEDS_OPERATOR),
        )
    else:
        engine_env.tasks.set_holds(pull.task.id, frozenset({Hold.PAUSED}))

    reader, queries = _queries(engine_env, database, pull)
    try:
        repair = queries.check_repair(pull.task.id)
        assert repair is not None
        assert not repair.active
        assert repair.interrupted
        assert not repair.exhausted
        assert repair.stage == CheckRepairStage.ANALYSIS.value
        detail = queries.get_task(pull.task.id)
        assert detail is not None
        cli = plain_text(
            status_renderable(
                queries.human_status(pull.task.id),
                width=100,
                uptime_seconds=None,
                quota_warn_pct=50,
                quota_alert_pct=90,
            )
        )
        assert "Check repair" in cli
        assert "interrupted" in cli
        assert "unit tests" in cli
    finally:
        reader.close()
    await pull.stop()
