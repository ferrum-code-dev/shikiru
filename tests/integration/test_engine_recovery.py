from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_agent.scripted import FAKE_CHANGE_FILENAME

from shikiru.agents.contracts import QuotaWindow, Rung, role_dir
from shikiru.config.settings import AgentConfig
from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.domain.commands import SetRung
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import QUOTA_MARGIN, QUOTA_PROBE
from shikiru.domain.steps import Step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Role, Status
from shikiru.process import SubprocessRunner
from shikiru.store.escalations import fetch_open_escalations
from shikiru.store.quotas import QuotaStore
from shikiru.store.reader import StateReader
from shikiru.workspace.roots import run_dir
from tests.support.clock import FakeClock
from tests.support.dashboard import StubServices, state_views
from tests.support.engine import FAKE_AGENT_ALIAS, FAKE_AGENT_NAME, FAKE_RUNG, set_role_script
from tests.support.factories import TASK_CREATED_AT
from tests.support.flow import (
    apply_set_rung,
    build_flow,
    build_flow_task,
    flow_settings,
    init_project_repo,
)
from tests.support.git_repo import commit_all
from tests.support.store import seed_task
from tests.support.waiting import wait_until, wait_until_advancing

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.dashboard.views import StateDashboardViews
    from shikiru.store.database import Database
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60
_RUNNER = SubprocessRunner()


class _RecordingClock(FakeClock):
    def __init__(self, start: datetime) -> None:
        super().__init__(start)
        self.sleep_requests: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleep_requests.append(seconds)
        await super().sleep(seconds)


async def _drive(flow_task: asyncio.Task[None], clock: FakeClock) -> None:
    def done() -> bool:
        return flow_task.done()

    await wait_until_advancing(clock, done, _FLOW_TIMEOUT_SECONDS)
    await flow_task


async def _assert_task_is_not_quota_waiting(
    views: StateDashboardViews,
    task_id: str,
    *,
    running_count: int,
    waiting_count: int,
) -> None:
    detail = await views.task_detail(task_id)
    summary = views.workspace_summary()
    waiting = views.task_browser(
        task_filter=TaskBrowserFilter.WAITING,
        search=task_id,
        offset=0,
        limit=1,
        selected_task_id=task_id,
    )
    assert detail is not None
    assert detail.blocker is None
    assert summary.running == running_count
    assert summary.waiting == waiting_count
    assert waiting.matching_count == 0


def _rung_settings(rung: Rung) -> Settings:
    settings = flow_settings()
    implementer = settings.roles.implementer.model_copy(update={"ladder": [FAKE_RUNG, rung]})
    roles = settings.roles.model_copy(update={"implementer": implementer})
    agents = dict(settings.agents)
    agents.setdefault(FAKE_AGENT_ALIAS, AgentConfig())
    return settings.model_copy(update={"roles": roles, "agents": agents})


async def _start_flow(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    *,
    settings: Settings,
    script: list[dict[str, object]],
    clock: FakeClock,
    mode: Mode = Mode.IMPLEMENT,
) -> tuple[asyncio.Task[None], str]:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=mode)
    role = Role.LEADER if mode is Mode.PLAN else Role.IMPLEMENTER
    await set_role_script(tmux_server, role, script)
    flow_env = build_flow(
        engine_env,
        settings,
        clock=clock,
        turn_clock=FakeClock(TASK_CREATED_AT),
    )
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))

    async def session_started() -> bool:
        run = engine_env.runs.get_live(task.id, role)
        return run is not None and run.session_id is not None

    await wait_until(session_started, timeout=10)
    return flow_task, task.id


async def test_quota_failure_blocks_then_continues_on_the_same_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(minutes=1)).isoformat(),
            }
        ],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    events = build_flow(engine_env, settings, clock=clock).events.since(0, task_id=task_id)
    kinds = [event.kind for event in events]
    assert EventKind.QUOTA_BLOCKED in kinds
    assert EventKind.QUOTA_CLEARED in kinds
    assert QuotaStore(engine_env.connection).block_for(FAKE_RUNG.agent) is None
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert run.session_id is not None
    turn_dirs = run_dir(engine_env.project_root, task_id) / "turns"
    continue_dirs = [
        path
        for path in turn_dirs.iterdir()
        if path.is_dir() and "Continue this task's current turn" in (path / "prompt.md").read_text()
    ]
    assert len(continue_dirs) == 1


async def test_live_fake_agent_quota_wait_is_shown_for_its_running_task(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    database: Database,
    tmp_path: Path,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    release = tmp_path / "release-continued-turn"
    os.mkfifo(release)
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {"kind": "fail", "failure_kind": "quota"},
            {"kind": "accept", "release_fifo": str(release)},
        ],
    )
    quota = QuotaStore(engine_env.connection)
    await wait_until(
        lambda: quota.block_for(FAKE_RUNG.agent) is not None and clock.has_pending_sleep(),
        timeout=10,
    )
    reader = StateReader(database, clock)
    try:
        flow_env = build_flow(engine_env, settings, clock=clock)
        views = state_views(
            reader,
            engine_env.project_root,
            clock,
            StubServices(),
            settings=settings,
        )
        detail = await views.task_detail(task_id)
        unrelated = seed_task(engine_env.connection, clock, title="Other account waiter")
        unrelated_detail = await views.task_detail(unrelated.id)
        summary = views.workspace_summary()
        waiting = views.task_browser(
            task_filter=TaskBrowserFilter.WAITING,
            search=task_id,
            offset=0,
            limit=1,
            selected_task_id=task_id,
        )
        assert detail is not None
        assert detail.wait_reason is None
        assert detail.blocker is not None
        evidence = "\n".join(detail.blocker.lines)
        assert "Current turn quota wait · implementer" in evidence
        assert "Provider-reported quota reset · not reported" in evidence
        assert "Engine retry/probe deadline" in evidence
        assert "Provider quota observation" in evidence
        assert unrelated_detail is not None
        assert unrelated_detail.blocker is None
        assert summary.running == 0
        assert summary.waiting == 2
        assert task_id in {row.task_id for row in waiting.rows}

        async def continued_turn_started() -> bool:
            started = flow_env.events.since(
                0,
                kinds=[EventKind.TURN_STARTED],
                task_id=task_id,
            )
            return len(started) > 1

        await wait_until_advancing(clock, continued_turn_started, _FLOW_TIMEOUT_SECONDS)
        await _assert_task_is_not_quota_waiting(
            views,
            task_id,
            running_count=1,
            waiting_count=1,
        )

        await asyncio.to_thread(release.write_text, "continue")
        await _drive(flow_task, clock)
        current = engine_env.tasks.require(task_id)
        await _assert_task_is_not_quota_waiting(
            views,
            task_id,
            running_count=int(current.status in (Status.RUNNING, Status.INTEGRATING)),
            waiting_count=1,
        )
    finally:
        reader.close()
        if not flow_task.done():
            flow_task.cancel()
            with suppress(asyncio.CancelledError):
                await flow_task


async def test_quota_wait_during_integration_conflict_is_reported(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    database: Database,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    (engine_env.project_root / FAKE_CHANGE_FILENAME).write_text("main change\n")
    await commit_all(_RUNNER, engine_env.project_root, "main change")
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [
            {"kind": "accept"},
            {"kind": "fail", "failure_kind": "quota"},
            {"kind": "accept"},
        ],
    )
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, settings, clock=clock, local_integration=True)
    flow_task = asyncio.create_task(flow_env.flow.run(task.id))
    quota = QuotaStore(engine_env.connection)

    def integration_quota_wait() -> bool:
        started = flow_env.events.since(
            0,
            kinds=[EventKind.TURN_STARTED],
            task_id=task.id,
        )
        return (
            any(event.data.get("step") == Step.INTEGRATE.value for event in started)
            and quota.block_for(FAKE_RUNG.agent) is not None
            and clock.has_pending_sleep()
        )

    await wait_until(integration_quota_wait, timeout=20)
    reader = StateReader(database, clock)
    try:
        views = state_views(
            reader,
            engine_env.project_root,
            clock,
            StubServices(),
            settings=settings,
        )
        detail = await views.task_detail(task.id)
        summary = views.workspace_summary()
        waiting = views.task_browser(
            task_filter=TaskBrowserFilter.WAITING,
            search=task.id,
            offset=0,
            limit=1,
            selected_task_id=task.id,
        )

        assert engine_env.tasks.require(task.id).status is Status.INTEGRATING
        assert engine_env.tasks.require(task.id).step is Step.INTEGRATE
        assert detail is not None
        assert detail.blocker is not None
        assert "Current turn quota wait · implementer" in "\n".join(detail.blocker.lines)
        assert summary.running == 0
        assert summary.waiting == 1
        assert [row.task_id for row in waiting.rows] == [task.id]
        await _drive(flow_task, clock)
    finally:
        reader.close()
        if not flow_task.done():
            flow_task.cancel()
            with suppress(asyncio.CancelledError):
                await flow_task


async def test_quota_wait_deadline_moves_to_a_different_agent(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None))
    timeouts = settings.orchestration.timeouts.model_copy(update={"max_quota_wait": 120})
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts})}
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(seconds=30)).isoformat(),
            }
        ],
    )
    quota = QuotaStore(engine_env.connection)
    await wait_until(lambda: quota.block_for(FAKE_RUNG.agent) is not None, timeout=10)
    extended_until = TASK_CREATED_AT + timedelta(minutes=5)
    quota.block(FAKE_RUNG.agent, extended_until, "another task")
    deadline = TASK_CREATED_AT + timedelta(seconds=120)

    async def advance_to_deadline() -> bool:
        if clock.now() >= deadline or flow_task.done():
            return True
        if clock.has_pending_sleep():
            clock.advance(1)
        return clock.now() >= deadline or flow_task.done()

    await wait_until(advance_to_deadline, timeout=10)
    await wait_until(
        lambda: (
            len([run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER])
            == 2
        ),
        timeout=10,
    )
    await _drive(flow_task, clock)

    runs = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert [run.agent for run in runs] == [FAKE_AGENT_ALIAS, FAKE_AGENT_NAME]
    assert runs[0].session_id != runs[1].session_id
    assert quota.block_for(FAKE_AGENT_NAME) is not None


_KNOWN_RESET = TASK_CREATED_AT + timedelta(minutes=40)
_EXPLICIT_RESET = TASK_CREATED_AT + timedelta(minutes=2)
_PROBE_UNTIL = TASK_CREATED_AT + QUOTA_PROBE
_NO_RESET_FAILURE: list[dict[str, object]] = [{"kind": "fail", "failure_kind": "quota"}]


def _stored_window(resets_at: datetime | None) -> QuotaWindow:
    return QuotaWindow(name="5h", used_pct=100.0, resets_at=resets_at, observed_at=TASK_CREATED_AT)


@pytest.mark.parametrize(
    ("window_agent", "window", "script", "expected_reset", "expected_until"),
    [
        (
            FAKE_AGENT_NAME,
            _stored_window(_KNOWN_RESET),
            _NO_RESET_FAILURE,
            _KNOWN_RESET,
            _KNOWN_RESET + QUOTA_MARGIN,
        ),
        (
            FAKE_AGENT_ALIAS,
            _stored_window(_KNOWN_RESET),
            _NO_RESET_FAILURE,
            None,
            _PROBE_UNTIL,
        ),
        (
            FAKE_AGENT_NAME,
            _stored_window(TASK_CREATED_AT - timedelta(minutes=1)),
            _NO_RESET_FAILURE,
            None,
            _PROBE_UNTIL,
        ),
        (
            FAKE_AGENT_NAME,
            _stored_window(None),
            _NO_RESET_FAILURE,
            None,
            _PROBE_UNTIL,
        ),
        (
            FAKE_AGENT_NAME,
            _stored_window(_KNOWN_RESET),
            [
                {
                    "kind": "fail",
                    "failure_kind": "quota",
                    "resets_at": _EXPLICIT_RESET.isoformat(),
                }
            ],
            _EXPLICIT_RESET,
            _EXPLICIT_RESET + QUOTA_MARGIN,
        ),
    ],
    ids=[
        "stored-window-reset",
        "other-agent-window-ignored",
        "expired-window-ignored",
        "null-window-ignored",
        "explicit-reset-wins",
    ],
)
async def test_quota_failure_without_reset_uses_the_stored_window(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    *,
    window_agent: str,
    window: QuotaWindow,
    script: list[dict[str, object]],
    expected_reset: datetime | None,
    expected_until: datetime,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    QuotaStore(engine_env.connection).record(window_agent, window)
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=script,
    )

    await _drive(flow_task, clock)

    events = build_flow(engine_env, settings, clock=clock).events
    [failed] = events.since(0, task_id=task_id, kinds=[EventKind.TURN_FAILED])
    [blocked] = events.since(0, task_id=task_id, kinds=[EventKind.QUOTA_BLOCKED])
    failed_reset = failed.data["resets_at"]
    reset = None if failed_reset is None else datetime.fromisoformat(failed_reset)
    assert reset == expected_reset
    assert datetime.fromisoformat(blocked.data["until"]) == expected_until


async def test_stored_quota_reset_beyond_max_quota_wait_escalates(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    QuotaStore(engine_env.connection).record(
        FAKE_AGENT_NAME, _stored_window(TASK_CREATED_AT + timedelta(hours=9))
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=_NO_RESET_FAILURE,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.QUOTA_EXHAUSTED


async def test_unavailable_failure_uses_backoff_then_resumes_the_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "unavailable"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    assert final.status is Status.INTEGRATING
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    rows = [
        json.loads(line)
        for line in (
            role_dir(run_dir(engine_env.project_root, task_id), Role.IMPLEMENTER.value)
            / "view.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    starts = [row["session_id"] for row in rows if row["signal"] == "SessionStarted"]
    assert starts == [run.session_id, run.session_id]


@pytest.mark.parametrize("agent", [FAKE_AGENT_NAME, FAKE_AGENT_ALIAS])
async def test_unavailable_retries_end_with_the_next_ladder_rung(
    engine_env: EngineEnv, tmux_server: TmuxServer, agent: str
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    next_rung = Rung(agent=agent, model="fake-2", effort=None)
    settings = _rung_settings(next_rung)
    retries = settings.orchestration.retries.unavailable_max_retries
    failure: dict[str, object] = {"kind": "fail", "failure_kind": "unavailable"}
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[*[failure for _ in range(retries + 1)], {"kind": "accept"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.require(task_id)
    assert final.status is Status.INTEGRATING
    assert final.step is Step.INTEGRATE
    assert final.rung_index[Role.IMPLEMENTER] == 1
    events = build_flow(engine_env, settings, clock=clock).events
    failures = events.since(0, task_id=task_id, kinds=[EventKind.TURN_FAILED])
    assert len(failures) == retries + 1
    [changed] = events.since(0, task_id=task_id, kinds=[EventKind.RUNG_CHANGED])
    assert changed.data["to"] == next_rung.model
    assert changed.data["different_agent"] is False
    assert not events.since(0, task_id=task_id, kinds=[EventKind.QUOTA_BLOCKED])
    runs = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert runs[0].agent == agent
    assert runs[0].model == next_rung.model


async def test_custom_unavailable_retry_settings_are_used_by_engine_recovery(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = _RecordingClock(TASK_CREATED_AT)
    next_rung = Rung(agent=FAKE_AGENT_ALIAS, model="fake-2", effort=None)
    settings = _rung_settings(next_rung)
    retries = settings.orchestration.retries.model_copy(
        update={
            "unavailable_initial_seconds": 2,
            "unavailable_multiplier": 3,
            "unavailable_max_seconds": 10,
            "unavailable_max_retries": 2,
        }
    )
    orchestration = settings.orchestration.model_copy(update={"retries": retries})
    settings = settings.model_copy(update={"orchestration": orchestration})
    failure: dict[str, object] = {"kind": "fail", "failure_kind": "unavailable"}
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[failure, failure, failure, {"kind": "accept"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.require(task_id)
    assert final.status is Status.INTEGRATING
    assert final.step is Step.INTEGRATE
    assert final.rung_index[Role.IMPLEMENTER] == 1
    assert clock.sleep_requests == [2, 6]
    events = build_flow(engine_env, settings, clock=clock).events
    assert len(events.since(0, task_id=task_id, kinds=[EventKind.TURN_FAILED])) == 3
    [changed] = events.since(0, task_id=task_id, kinds=[EventKind.RUNG_CHANGED])
    assert changed.data["to"] == next_rung.model


async def test_auth_failure_escalates_with_the_agent_auth_reason(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "auth"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_AUTH
    assert await asyncio.to_thread(Path(escalation[0].packet_path).is_file)


async def test_other_failure_retries_once_on_the_same_session(
    database: Database, engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings()
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "fail", "failure_kind": "other"}],
    )

    await _drive(flow_task, clock)

    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert (
        len(
            [
                row
                for row in (
                    role_dir(run_dir(engine_env.project_root, task_id), Role.IMPLEMENTER.value)
                    / "view.jsonl"
                )
                .read_text()
                .splitlines()
                if json.loads(row)["signal"] == "SessionStarted"
            ]
        )
        == 2
    )
    reader = StateReader(database, clock)
    try:
        views = state_views(
            reader,
            engine_env.project_root,
            clock,
            StubServices(),
            settings=settings,
        )
        assert views.attention().decisions == 0
        assert views.workspace_summary().attention == 0
    finally:
        reader.close()


async def test_unknown_failure_with_high_confidence_retries(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"kind": "other", "confidence": 0.95}]}
                )
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "crash"}, {"kind": "accept"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.INTEGRATING
    events = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert events[0].data["accepted"] is True
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 120
    assert run.session_id is not None


async def test_unknown_failure_below_confidence_escalates_without_retry(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(
                    options={"asks": [{"kind": "other", "confidence": 0.79}]}
                )
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "crash"}],
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_ERROR
    helper_events = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert helper_events[0].data["accepted"] is False
    [run] = [run for run in engine_env.runs.for_task(task_id) if run.role is Role.IMPLEMENTER]
    assert run.usage.input_tokens == 0


async def test_unrecoverable_report_escalates_as_agent_protocol(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = flow_settings().model_copy(
        update={
            "agents": {
                FAKE_AGENT_NAME: AgentConfig(options={"asks": [{"unrecoverable": "no report"}]})
            }
        }
    )
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[{"kind": "no_report"}, {"kind": "no_report"}, {"kind": "no_report"}],
        mode=Mode.PLAN,
    )

    await _drive(flow_task, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.ESCALATED
    escalation = fetch_open_escalations(engine_env.connection)
    assert escalation[0].reason is EscalationReason.AGENT_PROTOCOL
    [helper_event] = build_flow(engine_env, settings, clock=clock).events.since(
        0, kinds=[EventKind.HELPER_ACTION], task_id=task_id
    )
    assert helper_event.data["role"] == Role.LEADER.value
    assert "fake:fake-1/None" in helper_event.data["reason"]
    assert "no report" in helper_event.data["reason"]


@pytest.mark.parametrize("hold", [Hold.PAUSED, Hold.INTERVENTION])
async def test_a_hold_set_during_a_quota_wait_stops_the_continue_turn_until_resumed(
    engine_env: EngineEnv, tmux_server: TmuxServer, hold: Hold
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=flow_settings(),
        clock=clock,
        script=[
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": (TASK_CREATED_AT + timedelta(minutes=1)).isoformat(),
            }
        ],
    )
    quota = QuotaStore(engine_env.connection)
    await wait_until(lambda: quota.block_for(FAKE_RUNG.agent) is not None, timeout=10)
    await wait_until(clock.has_pending_sleep, timeout=10)
    engine_env.tasks.set_holds(task_id, frozenset({hold}))
    turns_before = len(list((run_dir(engine_env.project_root, task_id) / "turns").iterdir()))

    await _drive(flow_task, clock)

    held = engine_env.tasks.get(task_id)
    assert held is not None
    assert held.status is Status.RUNNING
    assert (
        len(list((run_dir(engine_env.project_root, task_id) / "turns").iterdir())) == turns_before
    )
    engine_env.tasks.set_holds(task_id, frozenset())
    flow_env = build_flow(engine_env, flow_settings(), clock=clock)
    resumed = asyncio.create_task(flow_env.flow.run(task_id))
    await _drive(resumed, clock)

    final = engine_env.tasks.get(task_id)
    assert final is not None
    assert final.status is Status.INTEGRATING


_PINNED_RUNG = Rung(agent=FAKE_AGENT_NAME, model="fake-pinned", effort=None)
_FAR_RESET = TASK_CREATED_AT + timedelta(hours=2)
_UNAVAILABLE = {"kind": "fail", "failure_kind": "unavailable"}
_RUNG_MOVE_CASES = {
    "up_a_rung": (
        [_UNAVAILABLE, _UNAVAILABLE, _UNAVAILABLE, {**_UNAVAILABLE, "release_fifo": "{fifo}"}],
        FAKE_AGENT_NAME,
    ),
    "to_a_different_agent": (
        [
            {
                "kind": "fail",
                "failure_kind": "quota",
                "resets_at": _FAR_RESET.isoformat(),
                "release_fifo": "{fifo}",
            }
        ],
        FAKE_AGENT_ALIAS,
    ),
}


@pytest.mark.parametrize(
    ("failures", "moved_agent"), _RUNG_MOVE_CASES.values(), ids=_RUNG_MOVE_CASES.keys()
)
async def test_a_rung_set_during_a_failing_turn_survives_the_recovery_move(
    engine_env: EngineEnv,
    tmux_server: TmuxServer,
    tmp_path: Path,
    *,
    failures: list[dict[str, object]],
    moved_agent: str,
) -> None:
    pinned_role = Role.IMPLEMENTER
    release = tmp_path / "release-failure"
    os.mkfifo(release)
    clock = FakeClock(TASK_CREATED_AT)
    settings = _rung_settings(Rung(agent=moved_agent, model="fake-2", effort=None))
    timeouts = settings.orchestration.timeouts.model_copy(update={"max_quota_wait": 120})
    settings = settings.model_copy(
        update={"orchestration": settings.orchestration.model_copy(update={"timeouts": timeouts})}
    )
    script = [
        {key: str(release) if value == "{fifo}" else value for key, value in step.items()}
        for step in failures
    ]
    flow_task, task_id = await _start_flow(
        engine_env,
        tmux_server,
        settings=settings,
        clock=clock,
        script=[*script, {"kind": "accept"}],
    )

    flow_env = build_flow(engine_env, settings, clock=clock)

    def gated_turn_running() -> bool:
        started = flow_env.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id)
        return len(started) == len(failures)

    await wait_until_advancing(clock, gated_turn_running, 20)
    await apply_set_rung(
        engine_env,
        flow_env,
        SetRung(task_id=task_id, role=pinned_role, rung=_PINNED_RUNG),
    )
    await asyncio.to_thread(release.write_text, "go")
    await _drive(flow_task, clock)

    final = engine_env.tasks.require(task_id)
    assert final.overrides == {pinned_role: _PINNED_RUNG}
    assert final.rung_index.get(Role.IMPLEMENTER, 0) == 0
