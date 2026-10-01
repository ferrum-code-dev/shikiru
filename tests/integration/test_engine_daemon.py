"""In-process daemon command acknowledgement and stop semantics."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import stat
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fake_agent.agent import FakeAgent, FakeOptions

from shikiru.agents.contracts import AgentContext, HeadlessAgent, Rung
from shikiru.bootstrap import build_engine
from shikiru.config.settings import (
    AgentConfig,
    LoggingConfig,
    RepoConfig,
)
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    HandBack,
    Pause,
    Prioritize,
    Resolve,
    Resume,
    Retry,
    RetryResolution,
    Say,
    SetRung,
    Stop,
    Submit,
    TakeOver,
)
from shikiru.domain.escalation import EscalationStatus, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import (
    Counters,
    EscalationReason,
    Hold,
    Mode,
    RepoState,
    Role,
    Status,
    StatusReason,
    Step,
    Task,
)
from shikiru.engine.daemon import EngineAlreadyRunningError, EngineLock, engine_is_running
from shikiru.engine.notifier import Notifier
from shikiru.engine.takeover import HAND_BACK_MESSAGE
from shikiru.errors import CommandTimeoutError, ConfigError
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore, fetch_open_escalations
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import PANE_COLOR_OPTION
from shikiru.tmux.session import SessionView
from shikiru.ui.tokens import HOLD_COLOR, OPERATOR_COLOR
from shikiru.view_log import CLI_VIEW_COMMAND
from shikiru.workspace.github import GitHub, PullRequestState
from shikiru.workspace.roots import run_dir, task_root
from tests.support.clock import FakeClock
from tests.support.engine import (
    FAKE_RUNG,
    build_raiser,
    build_registry,
    build_tmux,
    engine_settings,
    set_role_script,
    set_script,
    wait_for_command,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT, make_settings
from tests.support.flow import init_project_repo
from tests.support.permissions import requires_enforced_permissions
from tests.support.store import reopen_command
from tests.support.waiting import wait_until
from tests.support.workspace import task_root_builder

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

    from shikiru.agents.registry import AgentRegistry
    from shikiru.domain.commands import Command
    from shikiru.domain.runs import Run
    from shikiru.process import ProcessResult
    from shikiru.store.commands import CommandOutcome, QueuedCommand
    from tests.support.github import FakeGitHub
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

_RESPAWN_PANE = "respawn-pane"
_READ_ONLY_DIRECTORY = 0o500
_WRITABLE_DIRECTORY = 0o700
_SECOND_RUNG = Rung(agent="fake", model="fake-2", effort=None)


@dataclass(frozen=True)
class _ResolutionCase:
    reason: EscalationReason
    step: Step
    resolution: AnswerResolution | RetryResolution | AcceptResolution | CancelResolution
    expected_step: Step
    expected_status: Status
    expected_command_status: CommandStatus
    expected_escalation_status: EscalationStatus
    expected_override: Rung | None = None


class _NoHeadlessFakeAgent(FakeAgent):
    def headless(self) -> HeadlessAgent | None:
        return None


def _create_no_headless_agent(context: AgentContext) -> FakeAgent:
    return _NoHeadlessFakeAgent(context.runner, FakeOptions())


async def _append_and_wait(
    command: Command, queue: CommandQueue, clock: FakeClock
) -> CommandOutcome:
    return await wait_for_command(queue.append(command), queue, clock)


async def _submit(command: Submit, queue: CommandQueue, clock: FakeClock) -> str:
    outcome = await _append_and_wait(command, queue, clock)
    assert outcome.result is not None
    return str(outcome.result["task_id"])


def _required_task(tasks: TaskStore, task_id: str) -> Task:
    task = tasks.get(task_id)
    assert task is not None
    return task


async def test_in_process_engine_acknowledges_commands_and_stops_before_dispatch(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    registry: AgentRegistry = build_registry()
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=registry)
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())

    submitted = queue.append(Submit(title="queued task", request="queued task"))
    outcome = await wait_for_command(submitted, queue, clock)
    assert outcome.status is CommandStatus.DONE
    task_id = str(outcome.result["task_id"]) if outcome.result is not None else ""
    assert task_id

    prioritized = await _append_and_wait(Prioritize(task_id=task_id, priority=9), queue, clock)
    assert prioritized.status is CommandStatus.DONE
    paused = await _append_and_wait(Pause(task_id=task_id), queue, clock)
    assert paused.status is CommandStatus.DONE
    task = _required_task(tasks, task_id)
    assert task.priority == 9
    assert Hold.PAUSED in task.holds
    resumed = await _append_and_wait(Resume(task_id=task_id), queue, clock)
    assert resumed.status is CommandStatus.DONE
    task = _required_task(tasks, task_id)
    assert Hold.PAUSED not in task.holds
    rung = await _append_and_wait(
        SetRung(task_id=task_id, role=Role.IMPLEMENTER, rung=_SECOND_RUNG), queue, clock
    )
    assert rung.status is CommandStatus.DONE
    task = _required_task(tasks, task_id)
    assert task.overrides[Role.IMPLEMENTER] == _SECOND_RUNG
    task = _required_task(tasks, task_id)
    tasks.apply_transition(
        task_id,
        dataclasses.replace(
            Transition.stay(task, step=task.step, status=task.status),
            counters=Counters(rejections_on_rung=2, consults=3, conflict_rounds=4),
        ),
    )
    retried = await _append_and_wait(Retry(task_id=task_id), queue, clock)
    assert retried.status is CommandStatus.DONE
    task = _required_task(tasks, task_id)
    assert task.status is Status.QUEUED
    assert task.counters == Counters(rejections_on_rung=2)
    cancelled = await _append_and_wait(Cancel(task_id=task_id), queue, clock)
    assert cancelled.status is CommandStatus.DONE
    stopped = await _append_and_wait(Stop(), queue, clock)
    assert stopped.status is CommandStatus.DONE

    await engine_task
    events = EventLog(connection, clock).since(0, task_id=task_id)
    task = _required_task(tasks, task_id)
    assert task.status is Status.CANCELLED
    assert not any(event.kind is EventKind.TASK_STARTED for event in events)
    assert any(event.kind is EventKind.TASK_CANCELLED for event in events)
    connection.close()


async def test_unexpected_flow_error_escalates_without_stopping_engine(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    tmux = build_tmux(tmux_server)
    await init_project_repo(tmp_path)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="corrupt history", request="run"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    queue.mark_done(submitted.id, {"task_id": task.id})
    turn_dir = run_dir(tmp_path, task.id) / "turns" / "00-stale"
    turn_dir.mkdir(parents=True)
    (turn_dir / "engine-verdict.json").write_text("{}")
    engine_task = asyncio.create_task(engine.run())
    try:

        async def escalated() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            return (
                (current := tasks.get(task.id)) is not None
                and current.status is Status.ESCALATED
                and bool(fetch_open_escalations(connection))
            )

        await wait_until(escalated, timeout=10)
        assert not engine_task.done()
        escalation = fetch_open_escalations(connection)[0]
        assert escalation.reason is EscalationReason.ENGINE_ERROR
        await _append_and_wait(Stop(), queue, clock)
        await engine_task
    finally:
        if not engine_task.done():
            engine.request_stop()
            clock.advance(1)
            await engine_task
        connection.close()


def test_engine_rejects_helper_without_headless_support(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    helper_rung = Rung(agent="no-headless", model="fake-helper", effort=None)
    settings = make_settings(
        leader_ladder=[FAKE_RUNG],
        implementer_ladder=[FAKE_RUNG],
        helper_ladder=[helper_rung],
        agents={"fake": AgentConfig(), "no-headless": AgentConfig()},
    )
    registry = build_registry()
    registry.register("no-headless", _create_no_headless_agent)

    with pytest.raises(ConfigError, match="no headless support"):
        build_engine(
            tmp_path,
            settings,
            clock=FakeClock(TASK_CREATED_AT),
            tmux=build_tmux(tmux_server),
            registry=registry,
        )


async def test_stop_before_first_pass_prevents_same_batch_dispatch(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="stop batch", request="stop batch"))
    stopped = queue.append(Stop())
    engine_task = asyncio.create_task(engine.run())

    submit_outcome = await wait_for_command(submitted, queue, clock)
    stop_outcome = await wait_for_command(stopped, queue, clock)
    assert submit_outcome.status is CommandStatus.DONE
    assert stop_outcome.status is CommandStatus.DONE
    await engine_task

    task_id = str(submit_outcome.result["task_id"]) if submit_outcome.result is not None else ""
    task = TaskStore(connection, clock).get(task_id)
    assert task is not None
    assert task.status is Status.QUEUED
    assert task.window_id is None
    assert not EventLog(connection, clock).since(0, kinds=[EventKind.TASK_STARTED])
    connection.close()


def test_engine_lock_is_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / ".shikiru" / "engine.lock"
    lock = EngineLock(lock_path)
    with lock:
        assert engine_is_running(lock_path)
        with pytest.raises(EngineAlreadyRunningError):
            EngineLock(lock_path).acquire()
    assert not engine_is_running(lock_path)


def test_liveness_check_does_not_create_a_missing_lock_path(tmp_path: Path) -> None:
    lock_path = tmp_path / ".shikiru" / "engine.lock"

    assert not engine_is_running(lock_path)
    assert not lock_path.exists()


async def test_engine_log_captures_child_errors_and_detaches_on_shutdown(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    engine = build_engine(
        tmp_path, engine_settings(), tmux=build_tmux(tmux_server), registry=build_registry()
    )
    log_path = tmp_path / ".shikiru" / "logs" / "engine.log"
    child_logger = logging.getLogger("shikiru.engine.test")
    child_logger.error("child module failure")
    engine.request_stop()
    await engine.run()
    child_logger.error("after engine shutdown")

    log_contents = log_path.read_text()
    assert "child module failure" in log_contents
    assert "after engine shutdown" not in log_contents


async def test_an_engine_error_in_a_task_escalates_it_and_the_daemon_keeps_running(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="broken root", request="broken root"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    queue.mark_done(submitted.id, {"task_id": task.id})
    tasks.apply_transition(task.id, Transition.stay(task, step=Step.VERIFY, status=Status.QUEUED))
    tasks.set_repo(
        task.id,
        RepoState(base="missing", base_ref="missing", base_sha="0" * 40, branch="shikiru/broken"),
    )
    task_root(tmp_path, task.id).mkdir(parents=True)
    engine_task = asyncio.create_task(engine.run())
    try:

        async def escalated() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            return bool(fetch_open_escalations(connection))

        await wait_until(escalated, timeout=20)
        escalations = fetch_open_escalations(connection)
        assert [row.reason for row in escalations] == [EscalationReason.ENGINE_ERROR]
        packet = await asyncio.to_thread(Path(escalations[0].packet_path).read_text)
        assert "missing" in packet
        stop_outcome = await _append_and_wait(Stop(), queue, clock)
        assert stop_outcome.status is CommandStatus.DONE
        await engine_task
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


@pytest.mark.parametrize("packet_recorded", [False, True], ids=["no-escalation", "missing-packet"])
async def test_a_restarted_engine_gives_every_escalated_task_one_actionable_escalation(
    tmp_path: Path, tmux_server: TmuxServer, *, packet_recorded: bool
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="orphaned", request="orphaned"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    queue.mark_done(submitted.id, {"task_id": task.id})
    tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.IMPLEMENT, status=Status.ESCALATED)
    )
    tasks.set_slot(task.id, 1)
    if packet_recorded:
        EscalationStore(connection, clock).open(
            task.id,
            step=Step.IMPLEMENT,
            reason=EscalationReason.AGENT_ERROR,
            headline="lost packet",
            packet_path=str(tmp_path / ".shikiru" / "runs" / task.id / "escalations" / "E-1.md"),
            options=options_for(EscalationReason.AGENT_ERROR),
        )
    engine_task = asyncio.create_task(engine.run())
    try:
        stop_outcome = await _append_and_wait(Stop(), queue, clock)
        assert stop_outcome.status is CommandStatus.DONE
        await engine_task
        [escalation] = fetch_open_escalations(connection)
        assert escalation.task_id == task.id
        assert await asyncio.to_thread(Path(escalation.packet_path).read_text)
        assert _required_task(tasks, task.id).status is Status.ESCALATED
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _ResolutionCase(
                EscalationReason.DECISION_REQUIRED,
                Step.IMPLEMENT,
                AnswerResolution(text="use the safer parser"),
                Step.CONSULT,
                Status.QUEUED,
                CommandStatus.DONE,
                EscalationStatus.RESOLVED,
            ),
            id="answer-queues-consult",
        ),
        pytest.param(
            _ResolutionCase(
                EscalationReason.ITERATIONS_EXHAUSTED,
                Step.VERIFY,
                AcceptResolution(),
                Step.INTEGRATE,
                Status.QUEUED,
                CommandStatus.DONE,
                EscalationStatus.RESOLVED,
            ),
            id="accept-queues-integration",
        ),
        pytest.param(
            _ResolutionCase(
                EscalationReason.AGENT_ERROR,
                Step.IMPLEMENT,
                RetryResolution(rung=_SECOND_RUNG),
                Step.IMPLEMENT,
                Status.QUEUED,
                CommandStatus.DONE,
                EscalationStatus.RESOLVED,
                expected_override=_SECOND_RUNG,
            ),
            id="retry-keeps-step-and-applies-rung",
        ),
        pytest.param(
            _ResolutionCase(
                EscalationReason.DECISION_REQUIRED,
                Step.IMPLEMENT,
                CancelResolution(),
                Step.IMPLEMENT,
                Status.CANCELLED,
                CommandStatus.DONE,
                EscalationStatus.RESOLVED,
            ),
            id="cancel-finishes-task",
        ),
        pytest.param(
            _ResolutionCase(
                EscalationReason.AGENT_ERROR,
                Step.IMPLEMENT,
                AcceptResolution(),
                Step.IMPLEMENT,
                Status.ESCALATED,
                CommandStatus.REJECTED,
                EscalationStatus.OPEN,
            ),
            id="invalid-resolution-keeps-escalation-open",
        ),
    ],
)
async def test_resolution_commands_persist_task_and_escalation_outcomes(
    tmp_path: Path, tmux_server: TmuxServer, case: _ResolutionCase
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="resolve me", request="resolve me"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    queue.mark_done(submitted.id, {"task_id": task.id})
    tasks.apply_transition(
        task.id,
        Transition.stay(task, step=case.step, status=Status.ESCALATED),
    )
    tasks.set_slot(task.id, 1)
    events = EventLog(connection, clock)
    escalation_store = EscalationStore(connection, clock)
    escalation = await build_raiser(connection, clock, tmp_path, tmux=tmux).raise_escalation(
        task,
        step=case.step,
        reason=case.reason,
        question="choose",
        attempted="test",
        reports=(),
        facts="",
        view_tail="",
        apply_state=lambda: task,
    )
    resolve = queue.append(Resolve(escalation_id=escalation.id, resolution=case.resolution))
    stop = queue.append(Stop())
    engine_task = asyncio.create_task(engine.run())
    try:
        resolve_outcome = await wait_for_command(resolve, queue, clock)
        stop_outcome = await wait_for_command(stop, queue, clock)
        assert resolve_outcome.status is case.expected_command_status
        assert stop_outcome.status is CommandStatus.DONE
        await engine_task

        updated = tasks.get(task.id)
        assert updated is not None
        assert updated.status is case.expected_status
        assert updated.step is case.expected_step
        assert updated.overrides.get(Role.IMPLEMENTER) == case.expected_override
        persisted_escalation = escalation_store.get(escalation.id)
        assert persisted_escalation is not None
        assert persisted_escalation.status is case.expected_escalation_status
        task_events = events.since(0, task_id=task.id)
        resolved_events = [
            event for event in task_events if event.kind is EventKind.ESCALATION_RESOLVED
        ]
        assert bool(resolved_events) is (
            case.expected_escalation_status is EscalationStatus.RESOLVED
        )
        cancelled_events = [
            event for event in task_events if event.kind is EventKind.TASK_CANCELLED
        ]
        assert bool(cancelled_events) is (case.expected_status is Status.CANCELLED)
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


@dataclass
class _Escalated:
    operator: _Operator
    escalations: EscalationStore
    task: Task
    escalation_id: str
    tmux: Tmux
    session: str
    tasks: TaskStore
    restart_engine: Callable[[], Awaitable[None]]

    async def strand_window(self, tmp_path: Path) -> None:
        pane = await SessionView(self.tmux).create_task_window(
            self.session, window_name=self.task.id, cwd=tmp_path
        )
        window_id = await self.tmux.window_id(pane)
        await self.tmux.kill_window(window_id)
        self.tasks.set_window(self.task.id, window_id)

    def escalation_status(self) -> EscalationStatus:
        escalation = self.escalations.get(self.escalation_id)
        assert escalation is not None
        return escalation.status

    def resolved_events(self) -> int:
        events = self.operator.events.since(0, kinds=[EventKind.ESCALATION_RESOLVED])
        return len(events)


@asynccontextmanager
async def _escalated_task(tmp_path: Path, tmux_server: TmuxServer) -> AsyncGenerator[_Escalated]:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    session = await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))

    def start_engine() -> asyncio.Task[None]:
        engine = build_engine(
            tmp_path, engine_settings(), clock=clock, tmux=tmux, registry=build_registry()
        )
        return asyncio.create_task(engine.run())

    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="stale me", request="stale me"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    queue.mark_done(submitted.id, {"task_id": task.id})
    tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.IMPLEMENT, status=Status.ESCALATED)
    )
    tasks.set_slot(task.id, 1)
    runs = RunStore(connection, clock)
    escalations = EscalationStore(connection, clock)
    escalation = await build_raiser(connection, clock, tmp_path).raise_escalation(
        task,
        step=Step.IMPLEMENT,
        reason=EscalationReason.AGENT_ERROR,
        question="choose",
        attempted="test",
        reports=(),
        facts="",
        view_tail="",
        apply_state=lambda: task,
    )
    operator = _Operator(
        tasks, runs, EventLog(connection, clock), queue, clock, tmux_server, connection, tmp_path
    )
    engine_task = start_engine()

    async def restart_engine() -> None:
        nonlocal engine_task
        engine_task.cancel()
        with suppress(asyncio.CancelledError):
            await engine_task
        engine_task = start_engine()

    try:
        yield _Escalated(
            operator, escalations, task, escalation.id, tmux, session, tasks, restart_engine
        )
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def _open_pull_request_of(escalated: _Escalated, project_root: Path) -> None:
    url = await GitHub(SubprocessRunner()).create_pull_request(
        project_root, base="main", head="shikiru/T-1-stale-me", title="stale me", body="summary"
    )
    escalated.operator.events.append(
        EventKind.PR_OPENED,
        task_id=escalated.task.id,
        message="opened",
        data={"url": url, "branch": "shikiru/T-1-stale-me", "commits": ["c1"]},
    )


@pytest.mark.parametrize(
    "command",
    [Cancel(task_id="T-1"), Resolve(escalation_id="E-1", resolution=CancelResolution())],
    ids=["cancel", "resolve"],
)
async def test_cancelling_a_task_closes_its_open_pull_request(
    tmp_path: Path, tmux_server: TmuxServer, fake_github: FakeGitHub, command: Cancel | Resolve
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        await _open_pull_request_of(escalated, tmp_path)

        outcome = await escalated.operator.send(command)
        await _until_closed(escalated, fake_github)

        assert outcome.status is CommandStatus.DONE
        assert _withdrawn(escalated) == 1


async def _until_closed(escalated: _Escalated, fake_github: FakeGitHub) -> None:
    await escalated.operator.advance_until(
        lambda: (
            fake_github.pull_requests()[0]["state"] == PullRequestState.CLOSED
            and _withdrawn(escalated) == 1
        )
    )


def _withdrawn(escalated: _Escalated) -> int:
    return len(escalated.operator.events.since(0, kinds=[EventKind.PR_WITHDRAWN]))


async def test_a_slow_pull_request_close_does_not_delay_the_commands_behind_it(
    tmp_path: Path, tmux_server: TmuxServer, fake_github: FakeGitHub
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator = escalated.operator
        await _open_pull_request_of(escalated, tmp_path)
        fake_github.hold("close")

        cancelled = await operator.send(Cancel(task_id=escalated.task.id))
        await operator.advance_until(lambda: fake_github.calls("close") == 1)
        other = await operator.send(Submit(title="behind the close", request="behind the close"))

        assert cancelled.status is CommandStatus.DONE
        assert other.status is CommandStatus.DONE
        assert fake_github.pull_requests()[0]["state"] == PullRequestState.OPEN

        fake_github.release("close")
        await _until_closed(escalated, fake_github)


async def test_a_pull_request_close_that_failed_is_retried_and_repeated_after_a_restart(
    tmp_path: Path, tmux_server: TmuxServer, fake_github: FakeGitHub
) -> None:
    attempts = engine_settings().orchestration.network.pr_close_attempts
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator = escalated.operator
        await _open_pull_request_of(escalated, tmp_path)
        fake_github.fail_next("close", attempts)

        outcome = await operator.send(Cancel(task_id=escalated.task.id))
        await operator.advance_until(lambda: fake_github.calls("close") == attempts)
        await operator.advance_until(
            lambda: bool(operator.events.since(0, kinds=[EventKind.ENGINE_FAULT]))
        )

        assert outcome.status is CommandStatus.DONE
        assert fake_github.pull_requests()[0]["state"] == PullRequestState.OPEN
        assert _withdrawn(escalated) == 0

        await escalated.restart_engine()
        await _until_closed(escalated, fake_github)

        assert _withdrawn(escalated) == 1


@pytest.mark.parametrize(
    ("direct", "expected_status"),
    [(Cancel, Status.CANCELLED), (Retry, Status.QUEUED)],
    ids=["cancel", "retry"],
)
async def test_direct_cancel_and_retry_settle_the_escalation_so_a_stale_resolve_is_rejected(
    tmp_path: Path,
    tmux_server: TmuxServer,
    direct: type[Cancel | Retry],
    expected_status: Status,
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator, task_id = escalated.operator, escalated.task.id

        direct_outcome = await operator.send(direct(task_id=task_id))
        stale = await operator.send(
            Resolve(escalation_id=escalated.escalation_id, resolution=RetryResolution())
        )

        assert direct_outcome.status is CommandStatus.DONE
        assert stale.status is CommandStatus.REJECTED
        assert operator.task(task_id).status is expected_status
        assert escalated.escalation_status() is EscalationStatus.RESOLVED
        assert escalated.resolved_events() == 0


async def test_cancelling_a_task_keeps_a_dirty_worktree_and_tells_the_operator_where_it_is(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator, task_id = escalated.operator, escalated.task.id
        await init_project_repo(tmp_path)
        builder = task_root_builder(SubprocessRunner(), tmp_path, RepoConfig())
        escalated.tasks.set_repo(task_id, await builder.build(task_id, escalated.task.title))
        dirty = task_root(tmp_path, task_id) / "TODO.md"
        dirty.write_text("tracker entry\n")

        outcome = await operator.send(
            Resolve(escalation_id=escalated.escalation_id, resolution=CancelResolution())
        )

        assert outcome.status is CommandStatus.DONE
        assert outcome.result is not None
        assert outcome.result["kept_worktree"] == str(task_root(tmp_path, task_id))
        assert dirty.read_text() == "tracker entry\n"
        assert operator.task(task_id).status is Status.CANCELLED


async def test_a_resolved_escalation_cannot_be_resolved_again(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator = escalated.operator
        first = await operator.send(
            Resolve(escalation_id=escalated.escalation_id, resolution=CancelResolution())
        )

        again = await operator.send(
            Resolve(escalation_id=escalated.escalation_id, resolution=RetryResolution())
        )

        assert first.status is CommandStatus.DONE
        assert again.status is CommandStatus.REJECTED
        assert operator.task(escalated.task.id).status is Status.CANCELLED
        assert escalated.resolved_events() == 1


async def test_a_title_refresh_failure_after_resolve_does_not_reject_the_applied_resolution(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        await escalated.operator.send(Prioritize(task_id=escalated.task.id, priority=1))
        await escalated.strand_window(tmp_path)
        outcome = await escalated.operator.send(
            Resolve(escalation_id=escalated.escalation_id, resolution=RetryResolution())
        )

        assert outcome.status is CommandStatus.DONE
        assert escalated.operator.task(escalated.task.id).status is Status.QUEUED
        assert escalated.escalation_status() is EscalationStatus.RESOLVED
        assert escalated.resolved_events() == 1


async def test_retry_resolution_drops_only_cancelled_dependencies(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    tasks = TaskStore(connection, clock)

    def insert_task(title: str, dependencies: list[str] | None = None) -> Task:
        queued = queue.append(Submit(title=title, request=title, depends_on=dependencies or []))
        assert isinstance(queued.command, Submit)
        task = tasks.insert_from_submit(queued.command)
        queue.mark_done(queued.id, {"task_id": task.id})
        return task

    cancelled_dependency = insert_task("cancelled dependency")
    completed_dependency = insert_task("completed dependency")
    tasks.apply_transition(
        cancelled_dependency.id,
        Transition.stay(cancelled_dependency, step=Step.IMPLEMENT, status=Status.CANCELLED),
    )
    tasks.apply_transition(
        completed_dependency.id,
        Transition.stay(completed_dependency, step=Step.IMPLEMENT, status=Status.DONE),
    )
    task = insert_task(
        "retry task", dependencies=[cancelled_dependency.id, completed_dependency.id]
    )
    tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.IMPLEMENT, status=Status.ESCALATED)
    )
    tasks.set_slot(task.id, 1)
    escalation = await build_raiser(connection, clock, tmp_path).raise_escalation(
        task,
        step=Step.IMPLEMENT,
        reason=EscalationReason.DEPENDENCY_CANCELLED,
        question="retry?",
        attempted="a dependency was cancelled",
        reports=(),
        facts="",
        view_tail="",
        apply_state=lambda: task,
    )
    resolve = queue.append(Resolve(escalation_id=escalation.id, resolution=RetryResolution()))
    stop = queue.append(Stop())
    engine_task = asyncio.create_task(engine.run())
    try:
        resolve_outcome = await wait_for_command(resolve, queue, clock)
        stop_outcome = await wait_for_command(stop, queue, clock)
        assert resolve_outcome.status is CommandStatus.DONE
        assert stop_outcome.status is CommandStatus.DONE
        await engine_task

        retried = tasks.get(task.id)
        assert retried is not None
        assert retried.depends_on == [completed_dependency.id]
        assert retried.step is Step.IMPLEMENT
        assert retried.status is Status.QUEUED
        persisted_escalation = EscalationStore(connection, clock).get(escalation.id)
        assert persisted_escalation is not None
        assert persisted_escalation.status is EscalationStatus.RESOLVED
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_superseded_task_uses_shared_terminal_cleanup(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "supersede", "new_tasks": [{"title": "replacement", "request": "redo"}]}],
    )
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())
    try:
        submitted = queue.append(
            Submit(
                title="replace this task",
                request="replace this task",
                mode=Mode.IMPLEMENT,
            )
        )
        outcome = await wait_for_command(submitted, queue, clock)
        task_id = str(outcome.result["task_id"]) if outcome.result is not None else ""
        assert task_id

        async def cleaned() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            task = tasks.get(task_id)
            if task is None or task.status_reason is not StatusReason.SUPERSEDED:
                return False
            window_closed = task.window_id is None or not await tmux.target_exists(task.window_id)
            return window_closed and not task_root(tmp_path, task_id).exists()

        await wait_until(cleaned, timeout=40)
        superseded = tasks.get(task_id)
        assert superseded is not None
        assert superseded.status is Status.CANCELLED
        assert superseded.status_reason is StatusReason.SUPERSEDED
        assert superseded.window_id is not None
        assert not await tmux.target_exists(superseded.window_id)
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_engine_observes_two_in_process_flows_in_parallel(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    await init_project_repo(tmp_path)
    set_script(monkeypatch, [{"kind": "stall"}])
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT, max_parallel=2)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    first = queue.append(Submit(title="parallel one", request="parallel one", mode=Mode.IMPLEMENT))
    second = queue.append(Submit(title="parallel two", request="parallel two", mode=Mode.IMPLEMENT))
    engine_task = asyncio.create_task(engine.run())

    first_outcome = await wait_for_command(first, queue, clock)
    second_outcome = await wait_for_command(second, queue, clock)
    first_id = str(first_outcome.result["task_id"]) if first_outcome.result is not None else ""
    second_id = str(second_outcome.result["task_id"]) if second_outcome.result is not None else ""

    async def both_started() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        started = EventLog(connection, clock).since(0, kinds=[EventKind.TASK_STARTED])
        runs = RunStore(connection, clock)
        started_ids = {event.task_id for event in started}
        live_ids = {
            task_id
            for task_id in (first_id, second_id)
            if runs.get_live(task_id, Role.IMPLEMENTER) is not None
        }
        return started_ids == {first_id, second_id} and live_ids == {first_id, second_id}

    await wait_until(both_started, timeout=10)
    first_task = TaskStore(connection, clock).get(first_id)
    second_task = TaskStore(connection, clock).get(second_id)
    assert first_task is not None
    assert second_task is not None
    assert {first_task.slot, second_task.slot} == {0, 1}

    stopped = queue.append(Stop())
    stop_outcome = await wait_for_command(stopped, queue, clock)
    assert stop_outcome.status is CommandStatus.DONE
    await engine_task
    connection.close()


async def _wait_for_session(runs: RunStore, clock: FakeClock, task_id: str) -> Run:
    async def session_live() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        run = runs.get_live(task_id, Role.IMPLEMENTER)
        return run is not None and run.session_id is not None

    await wait_until(session_live, timeout=10)
    run = runs.get_live(task_id, Role.IMPLEMENTER)
    assert run is not None
    return run


async def test_take_over_holds_only_that_task_and_hand_back_resumes_its_session(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}, {"kind": "accept"}])
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT, max_parallel=2)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    runs = RunStore(connection, clock)
    events = EventLog(connection, clock)
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())
    try:
        task_id = await _submit(
            Submit(title="take me", request="take me", mode=Mode.IMPLEMENT), queue, clock
        )
        other_id = await _submit(
            Submit(title="leave me", request="leave me", mode=Mode.IMPLEMENT), queue, clock
        )

        run = await _wait_for_session(runs, clock, task_id)
        session_id = run.session_id
        step = _required_task(tasks, task_id).step

        taken = await _append_and_wait(
            TakeOver(task_id=task_id, role=Role.IMPLEMENTER), queue, clock
        )

        assert taken.status is CommandStatus.DONE
        assert Hold.INTERVENTION in _required_task(tasks, task_id).holds
        assert Hold.INTERVENTION not in _required_task(tasks, other_id).holds
        taken_run = runs.get_taken_over(task_id, Role.IMPLEMENTER)
        assert taken_run is not None
        assert taken_run.session_id == session_id
        held = _required_task(tasks, task_id).holds
        again = await _append_and_wait(
            TakeOver(task_id=task_id, role=Role.IMPLEMENTER), queue, clock
        )
        assert again.status is CommandStatus.REJECTED
        assert len(events.since(0, kinds=[EventKind.TAKEN_OVER], task_id=task_id)) == 1
        assert (
            _required_task(tasks, task_id).holds,
            runs.get_taken_over(task_id, Role.IMPLEMENTER),
        ) == (held, taken_run)

        pane = _required_task(tasks, task_id).panes[Role.IMPLEMENTER]
        await tmux_server.run("send-keys", "-t", pane, "/exit", "Enter")

        async def handed_back_and_advanced() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            task = _required_task(tasks, task_id)
            handed = events.since(0, kinds=[EventKind.HANDED_BACK], task_id=task_id)
            return bool(handed) and task.step is not step and Hold.INTERVENTION not in task.holds

        await wait_until(handed_back_and_advanced, timeout=30)
        resumed = runs.get_live(task_id, Role.IMPLEMENTER)
        assert resumed is not None
        assert resumed.id == run.id
        assert resumed.session_id == session_id
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


async def test_hand_back_clears_the_operator_colour_from_the_pane(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    await init_project_repo(tmp_path)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}])
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(
        tmp_path, engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT), clock=clock, tmux=tmux
    )
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    events = EventLog(connection, clock)
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())
    try:
        task_id = await _submit(
            Submit(title="take me", request="take me", mode=Mode.IMPLEMENT), queue, clock
        )
        await _wait_for_session(RunStore(connection, clock), clock, task_id)
        await _append_and_wait(TakeOver(task_id=task_id, role=Role.IMPLEMENTER), queue, clock)
        pane = _required_task(tasks, task_id).panes[Role.IMPLEMENTER]
        assert await _pane_color(tmux_server, pane) == OPERATOR_COLOR
        await _append_and_wait(Pause(task_id=task_id), queue, clock)

        await tmux_server.run("send-keys", "-t", pane, "/exit", "Enter")

        async def handed_back_on_hold() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            pane = _required_task(tasks, task_id).panes[Role.IMPLEMENTER]
            return await _pane_color(tmux_server, pane) == HOLD_COLOR

        await wait_until(handed_back_on_hold, timeout=30)
        assert events.since(0, kinds=[EventKind.HANDED_BACK], task_id=task_id)
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


@dataclass(frozen=True)
class _Operator:
    tasks: TaskStore
    runs: RunStore
    events: EventLog
    queue: CommandQueue
    clock: FakeClock
    tmux_server: TmuxServer
    connection: Connection
    project_root: Path

    async def send(self, command: Command) -> CommandOutcome:
        return await _append_and_wait(command, self.queue, self.clock)

    async def replay(self, queued: QueuedCommand) -> CommandOutcome:
        reopen_command(self.connection, queued.id)
        return await wait_for_command(queued, self.queue, self.clock)

    def task(self, task_id: str) -> Task:
        return _required_task(self.tasks, task_id)

    def message_files(self, task_id: str, text: str) -> list[Path]:
        return [
            path
            for path in run_dir(self.project_root, task_id).rglob("*.md")
            if path.read_text() == text
        ]

    def turns(self, task_id: str) -> int:
        return len(self.events.since(0, kinds=[EventKind.TURN_STARTED], task_id=task_id))

    def run_ids(self, task_id: str) -> list[int]:
        return [run.id for run in self.runs.for_task(task_id)]

    async def pane_pid(self, task_id: str, role: Role) -> str:
        pane = self.task(task_id).panes[role]
        result = await self.tmux_server.run("display-message", "-p", "-t", pane, "#{pane_pid}")
        return result.stdout.strip()

    async def advance_until(self, condition: Callable[[], bool]) -> None:
        async def reached() -> bool:
            if self.clock.has_pending_sleep():
                self.clock.advance(0.25)
            return condition()

        await wait_until(reached, timeout=30)


@asynccontextmanager
async def _operator_session(
    tmp_path: Path, tmux_server: TmuxServer, *, tmux_override: Tmux | None = None
) -> AsyncGenerator[tuple[_Operator, str, Run]]:
    await init_project_repo(tmp_path)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "stall"}, {"kind": "accept"}])
    clock = FakeClock(TASK_CREATED_AT)
    tmux = tmux_override if tmux_override is not None else build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(
        tmp_path,
        engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT),
        clock=clock,
        tmux=tmux,
        registry=build_registry(),
    )
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    operator = _Operator(
        TaskStore(connection, clock),
        RunStore(connection, clock),
        EventLog(connection, clock),
        CommandQueue(connection, clock),
        clock,
        tmux_server,
        connection,
        tmp_path,
    )
    engine_task = asyncio.create_task(engine.run())
    try:
        task_id = await _submit(
            Submit(title="own me", request="own me", mode=Mode.IMPLEMENT),
            operator.queue,
            clock,
        )
        run = await _wait_for_session(operator.runs, clock, task_id)
        yield operator, task_id, run
    finally:
        if not engine_task.done():
            engine_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await engine_task
        connection.close()


class _InteractivePaneRunner(SubprocessRunner):
    def __init__(self) -> None:
        self.failure: Exception | None = None

    async def run(self, argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        if self.failure is not None and _RESPAWN_PANE in argv and CLI_VIEW_COMMAND not in argv:
            raise self.failure
        return await super().run(argv, **kwargs)


async def test_a_take_over_whose_pane_cannot_be_started_changes_nothing_and_can_be_retried(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    runner = _InteractivePaneRunner()
    tmux = Tmux(runner, socket=tmux_server.socket_name, env=tmux_server.env)
    async with _operator_session(tmp_path, tmux_server, tmux_override=tmux) as (
        operator,
        task_id,
        run,
    ):
        runner.failure = CommandTimeoutError(("tmux",), 5.0)

        failed = await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))

        assert failed.status is CommandStatus.REJECTED
        assert Hold.INTERVENTION not in operator.task(task_id).holds
        assert operator.runs.get_taken_over(task_id, Role.IMPLEMENTER) is None
        assert operator.runs.get_live(task_id, Role.IMPLEMENTER) is not None
        runner.failure = None

        retried = await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))

        assert retried.status is CommandStatus.DONE
        assert Hold.INTERVENTION in operator.task(task_id).holds
        owned = operator.runs.get_taken_over(task_id, Role.IMPLEMENTER)
        assert owned is not None
        assert owned.id == run.id


async def test_replaying_a_take_over_never_respawns_its_live_interactive_pane(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, _run):
        sent = operator.queue.append(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        await wait_for_command(sent, operator.queue, operator.clock)
        live_pid = await operator.pane_pid(task_id, Role.IMPLEMENTER)

        replayed = await operator.replay(sent)

        assert replayed.status is CommandStatus.DONE
        assert await operator.pane_pid(task_id, Role.IMPLEMENTER) == live_pid


async def test_a_takeover_lost_with_the_tmux_server_is_handed_back_with_a_notified_event(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        await tmux_server.run("kill-server", check=False)

        await operator.advance_until(
            lambda: bool(operator.events.since(0, kinds=[EventKind.HANDED_BACK], task_id=task_id))
        )

        lost = operator.events.since(0, kinds=[EventKind.TAKEOVER_LOST], task_id=task_id)
        assert [event.data["role"] for event in lost] == [Role.IMPLEMENTER.value]
        assert Notifier.wants(lost[0])
        assert Hold.INTERVENTION not in operator.task(task_id).holds
        assert operator.runs.get_taken_over(task_id, Role.IMPLEMENTER) is None
        resumed = operator.runs.get_live(task_id, Role.IMPLEMENTER)
        assert resumed is not None
        assert resumed.id == run.id


@pytest.mark.parametrize("paused", [False, True], ids=["running", "paused"])
async def test_resume_is_rejected_while_a_role_is_taken_over(
    tmp_path: Path, tmux_server: TmuxServer, paused: bool
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        if paused:
            await operator.send(Pause(task_id=task_id))
        taken = await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        assert taken.status is CommandStatus.DONE
        holds = operator.task(task_id).holds
        pid = await operator.pane_pid(task_id, Role.IMPLEMENTER)
        turns = operator.turns(task_id)

        resumed = await operator.send(Resume(task_id=task_id))

        assert resumed.status is CommandStatus.REJECTED
        assert operator.task(task_id).holds == holds
        assert await operator.pane_pid(task_id, Role.IMPLEMENTER) == pid
        owned = operator.runs.get_taken_over(task_id, Role.IMPLEMENTER)
        assert owned is not None
        assert (owned.id, owned.session_id) == (run.id, run.session_id)
        assert operator.run_ids(task_id) == [run.id]
        assert operator.turns(task_id) == turns


async def test_a_partial_hand_back_keeps_the_intervention_hold_until_the_last_role_returns(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        leader = operator.runs.start(task_id, Role.LEADER, FAKE_RUNG)
        operator.runs.set_session(leader.id, "leader-session", None)
        step = operator.task(task_id).step
        for role in (Role.IMPLEMENTER, Role.LEADER):
            taken = await operator.send(TakeOver(task_id=task_id, role=role))
            assert taken.status is CommandStatus.DONE
        turns = operator.turns(task_id)
        leader_pid = await operator.pane_pid(task_id, Role.LEADER)

        handed = await operator.send(HandBack(task_id=task_id, role=Role.IMPLEMENTER))

        assert handed.status is CommandStatus.DONE
        assert operator.task(task_id).holds == {Hold.INTERVENTION}
        assert operator.runs.get_taken_over(task_id, Role.LEADER) is not None
        assert await operator.pane_pid(task_id, Role.LEADER) == leader_pid
        assert operator.turns(task_id) == turns
        assert operator.task(task_id).step is step

        await operator.send(HandBack(task_id=task_id, role=Role.LEADER))
        await operator.advance_until(lambda: operator.task(task_id).step is not step)

        assert operator.task(task_id).holds == frozenset()
        resumed = operator.runs.get_live(task_id, Role.IMPLEMENTER)
        assert resumed is not None
        assert (resumed.id, resumed.session_id) == (run.id, run.session_id)


async def test_pause_survives_a_takeover_and_its_hand_back_until_resume(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        step = operator.task(task_id).step
        await operator.send(Pause(task_id=task_id))
        await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        turns = operator.turns(task_id)

        await operator.send(HandBack(task_id=task_id, role=Role.IMPLEMENTER))

        assert operator.task(task_id).holds == {Hold.PAUSED}
        assert operator.turns(task_id) == turns
        assert operator.run_ids(task_id) == [run.id]

        resumed = await operator.send(Resume(task_id=task_id))
        assert resumed.status is CommandStatus.DONE
        await operator.advance_until(lambda: operator.task(task_id).step is not step)
        live = operator.runs.get_live(task_id, Role.IMPLEMENTER)
        assert live is not None
        assert (live.id, live.session_id) == (run.id, run.session_id)


async def _pane_color(tmux_server: TmuxServer, pane: str) -> str:
    result = await tmux_server.run("show-options", "-p", "-v", "-t", pane, PANE_COLOR_OPTION)
    return result.stdout.strip()


async def test_replaying_an_acknowledged_say_neither_duplicates_the_message_nor_its_event(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, _run):
        text = "use the new helper"
        sent = operator.queue.append(Say(task_id=task_id, role=Role.IMPLEMENTER, text=text))
        first = await wait_for_command(sent, operator.queue, operator.clock)

        replayed = await operator.replay(sent)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert len(operator.message_files(task_id, text)) == 1
        queued = operator.events.since(0, kinds=[EventKind.OPERATOR_MESSAGE], task_id=task_id)
        assert len(queued) == 1


async def test_replaying_an_acknowledged_submit_returns_its_task_without_a_second_one(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, _task_id, _run):
        sent = operator.queue.append(Submit(title="once", request="once", mode=Mode.IMPLEMENT))
        first = await wait_for_command(sent, operator.queue, operator.clock)

        replayed = await operator.replay(sent)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert replayed.result == first.result
        assert first.result is not None
        queued = operator.events.since(0, kinds=[EventKind.TASK_QUEUED])
        assert [event.task_id for event in queued].count(first.result["task_id"]) == 1


async def test_replaying_an_acknowledged_hand_back_is_a_no_op_success(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        await operator.send(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        sent = operator.queue.append(HandBack(task_id=task_id, role=Role.IMPLEMENTER))
        first = await wait_for_command(sent, operator.queue, operator.clock)

        replayed = await operator.replay(sent)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        handed = operator.events.since(0, kinds=[EventKind.HANDED_BACK], task_id=task_id)
        assert len(handed) == 1
        assert len(operator.message_files(task_id, HAND_BACK_MESSAGE)) == 1
        live = operator.runs.get_live(task_id, Role.IMPLEMENTER)
        assert live is not None
        assert live.id == run.id


async def _acknowledged_twice(
    operator: _Operator, command: Command
) -> tuple[CommandOutcome, CommandOutcome]:
    sent = operator.queue.append(command)
    first = await wait_for_command(sent, operator.queue, operator.clock)
    return first, await operator.replay(sent)


async def test_replaying_an_acknowledged_retry_leaves_the_restarted_turn_running(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, _run):
        turns = operator.turns(task_id)
        sent = operator.queue.append(Retry(task_id=task_id))
        first = await wait_for_command(sent, operator.queue, operator.clock)
        await operator.advance_until(lambda: operator.turns(task_id) > turns)
        restarted_turns = operator.turns(task_id)

        replayed = await operator.replay(sent)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert operator.task(task_id).status is not Status.QUEUED
        assert operator.turns(task_id) == restarted_turns
        assert len(operator.events.since(0, kinds=[EventKind.TASK_RETRIED])) == 1


@dataclass(frozen=True)
class _Replay:
    command: Command
    event: EventKind
    applied: Callable[[Task], bool]


_REPLAYS = {
    "cancel": _Replay(
        Cancel(task_id="T-1"),
        EventKind.TASK_CANCELLED,
        lambda task: task.status is Status.CANCELLED,
    ),
    "set_rung": _Replay(
        SetRung(task_id="T-1", role=Role.IMPLEMENTER, rung=_SECOND_RUNG),
        EventKind.RUNG_CHANGED,
        lambda task: task.overrides[Role.IMPLEMENTER] == _SECOND_RUNG,
    ),
    "pause": _Replay(
        Pause(task_id="T-1", now=True),
        EventKind.HOLD_SET,
        lambda task: task.holds == frozenset({Hold.PAUSED}),
    ),
}


@pytest.mark.parametrize("replay", _REPLAYS.values(), ids=_REPLAYS.keys())
async def test_replaying_an_acknowledged_state_command_appends_no_second_event(
    tmp_path: Path, tmux_server: TmuxServer, replay: _Replay
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, _run):
        first, replayed = await _acknowledged_twice(operator, replay.command)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert replay.applied(operator.task(task_id))
        assert len(operator.events.since(0, kinds=[replay.event])) == 1


async def test_replaying_a_take_over_whose_pane_was_lost_respawns_it_without_a_second_event(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _operator_session(tmp_path, tmux_server) as (operator, task_id, run):
        sent = operator.queue.append(TakeOver(task_id=task_id, role=Role.IMPLEMENTER))
        first = await wait_for_command(sent, operator.queue, operator.clock)
        pane = operator.task(task_id).panes[Role.IMPLEMENTER]
        await tmux_server.run("respawn-pane", "-k", "-t", pane, "/bin/sh")
        lost_pid = await operator.pane_pid(task_id, Role.IMPLEMENTER)

        replayed = await operator.replay(sent)

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert await operator.pane_pid(task_id, Role.IMPLEMENTER) != lost_pid
        assert len(operator.events.since(0, kinds=[EventKind.TAKEN_OVER])) == 1
        assert operator.task(task_id).holds == {Hold.INTERVENTION}
        owned = operator.runs.get_taken_over(task_id, Role.IMPLEMENTER)
        assert owned is not None
        assert owned.id == run.id


async def test_replaying_an_acknowledged_resolve_reports_it_without_a_second_event(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        operator = escalated.operator

        first, replayed = await _acknowledged_twice(
            operator,
            Resolve(escalation_id=escalated.escalation_id, resolution=CancelResolution()),
        )

        assert (first.status, replayed.status) == (CommandStatus.DONE, CommandStatus.DONE)
        assert operator.task(escalated.task.id).status is Status.CANCELLED
        assert escalated.resolved_events() == 1


@requires_enforced_permissions
@pytest.mark.parametrize(
    "command",
    [Cancel(task_id="T-1"), Resolve(escalation_id="E-1", resolution=CancelResolution())],
    ids=["cancel", "resolve"],
)
async def test_a_cleanup_failure_after_a_committed_cancel_does_not_reject_it(
    tmp_path: Path, tmux_server: TmuxServer, command: Cancel | Resolve
) -> None:
    async with _escalated_task(tmp_path, tmux_server) as escalated:
        locked = task_root(tmp_path, escalated.task.id) / "locked"
        locked.mkdir(parents=True)
        (locked / "file").write_text("x")
        locked.chmod(_READ_ONLY_DIRECTORY)
        try:
            outcome = await escalated.operator.send(command)
        finally:
            locked.chmod(_WRITABLE_DIRECTORY)

        assert outcome.status is CommandStatus.DONE
        assert escalated.operator.task(escalated.task.id).status is Status.CANCELLED
        faults = escalated.operator.events.since(0, kinds=[EventKind.ENGINE_FAULT])
        assert [fault.task_id for fault in faults] == [escalated.task.id]


_LOOSE_DIRECTORY = 0o755
_LOOSE_FILE = 0o644
_NO_GROUP_OR_OTHER = 0o077
_AGENT_OWNED = "fake_state"


def _modes_below(state_dir: Path) -> dict[Path, int]:
    modes: dict[Path, int] = {}
    for directory, subdirectories, names in state_dir.walk():
        if directory.name == _AGENT_OWNED:
            subdirectories.clear()
            continue
        files = [] if directory.name == "worktrees" else names
        subdirectories[:] = [] if directory.name == "worktrees" else subdirectories
        for path in (directory, *(directory / name for name in files)):
            if not path.is_symlink():
                modes[path] = stat.S_IMODE(path.stat().st_mode)
    return modes


async def test_a_running_engine_keeps_every_shikiru_file_and_directory_owner_only(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    await init_project_repo(tmp_path)
    state_dir = tmp_path / ".shikiru"
    old_run = state_dir / "runs" / "T-0"
    old_run.mkdir(parents=True)
    worktree_file = state_dir / "worktrees" / "T-0" / "src.py"
    worktree_file.parent.mkdir(parents=True)
    worktree_file.write_text("x = 1\n")
    old_log = old_run / "implementer" / "view.jsonl"
    old_log.parent.mkdir()
    old_log.write_text("{}\n")
    for path in (state_dir, state_dir / "runs", old_run, old_log.parent):
        path.chmod(_LOOSE_DIRECTORY)
    old_log.chmod(_LOOSE_FILE)
    worktree_file.chmod(_LOOSE_FILE)
    set_script(monkeypatch, [{"kind": "stall"}])
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(
        tmp_path,
        engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT),
        clock=clock,
        tmux=tmux,
        registry=build_registry(),
    )
    connection = Database(state_dir / "state.db").connect()
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())
    submitted = await _submit(
        Submit(title="private", request="private", mode=Mode.IMPLEMENT), queue, clock
    )
    await _wait_for_session(RunStore(connection, clock), clock, submitted)
    new_view = run_dir(tmp_path, submitted) / "implementer" / "view.jsonl"

    async def viewed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        return new_view.exists()

    await wait_until(viewed, timeout=10)
    modes = _modes_below(state_dir)
    await _append_and_wait(Stop(), queue, clock)
    await engine_task
    connection.close()

    assert {state_dir / "state.db-wal", state_dir / "logs" / "engine.log", new_view} <= modes.keys()
    for path, mode in {**modes, **_modes_below(state_dir)}.items():
        assert mode & _NO_GROUP_OR_OTHER == 0, (path, oct(mode))
        if path.is_dir():
            assert mode == _WRITABLE_DIRECTORY, (path, oct(mode))
    assert stat.S_IMODE(worktree_file.stat().st_mode) == _LOOSE_FILE


async def test_the_engine_log_rotates_at_the_configured_size_and_keeps_a_bounded_number_of_files(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    max_bytes, backups = 400, 2
    settings = engine_settings().model_copy(
        update={
            "logging": LoggingConfig(engine_log_max_bytes=max_bytes, engine_log_backups=backups)
        }
    )
    engine = build_engine(
        tmp_path, settings, tmux=build_tmux(tmux_server), registry=build_registry()
    )
    logger = logging.getLogger("shikiru.engine.test")
    for number in range(100):
        logger.error("line %03d %s", number, "x" * 40)
    engine.request_stop()
    await engine.run()

    logs = sorted((tmp_path / ".shikiru" / "logs").iterdir())
    assert [path.name for path in logs] == ["engine.log", "engine.log.1", "engine.log.2"]
    assert all(path.stat().st_size <= max_bytes for path in logs)
    assert all(stat.S_IMODE(path.stat().st_mode) & _NO_GROUP_OR_OTHER == 0 for path in logs)
    assert "line 099" in logs[0].read_text()
