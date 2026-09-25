"""In-process daemon command acknowledgement and stop semantics."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fake_agent.agent import FakeAgent, FakeOptions

from shikiru.agents.contracts import AgentContext, HeadlessAgent, Rung
from shikiru.bootstrap import build_engine
from shikiru.config.settings import AgentConfig, LayoutSplit, PaneLeaf, ProjectConfig
from shikiru.domain.commands import (
    Cancel,
    CancelResolution,
    Pause,
    Prioritize,
    Resolve,
    Resume,
    Retry,
    SetRung,
    Stop,
    Submit,
)
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import (
    EscalationReason,
    Mode,
    RepoState,
    Role,
    Status,
    StatusReason,
    Step,
)
from shikiru.engine.daemon import EngineAlreadyRunningError, EngineLock, engine_is_running
from shikiru.engine.escalations import EscalationRaiser
from shikiru.errors import ConfigError
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.session import SessionView
from shikiru.workspace.roots import run_dir, task_root
from tests.support.clock import FakeClock
from tests.support.engine import build_registry, build_tmux, set_role_script, set_script
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.agents.registry import AgentRegistry
    from shikiru.config.settings import Settings
    from shikiru.domain.commands import Command
    from shikiru.store.commands import CommandOutcome, QueuedCommand
    from tests.support.tmux import TmuxServer

_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_SECOND_RUNG = Rung(agent="fake", model="fake-2", effort=None)
_LAYOUT = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="leader"),
        PaneLeaf(pane="implementer"),
        PaneLeaf(pane="system"),
        PaneLeaf(pane="shell"),
    ],
)


class _NoHeadlessFakeAgent(FakeAgent):
    def headless(self) -> HeadlessAgent | None:
        return None


def _create_no_headless_agent(context: AgentContext) -> FakeAgent:
    return _NoHeadlessFakeAgent(context.runner, FakeOptions())


def _settings(*, max_cpu_pct: int = 0, max_parallel: int = 1) -> Settings:
    settings = make_settings(
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
        helper_ladder=[_FAKE_RUNG],
        agents={"fake": AgentConfig()},
    )
    orchestration = settings.orchestration.model_copy(
        update={
            "max_parallel": max_parallel,
            "resources": settings.orchestration.resources.model_copy(
                update={"max_cpu_pct": max_cpu_pct, "min_free_ram_gb": 0}
            ),
        }
    )
    update: dict[str, Any] = {
        "project": ProjectConfig(name="test", trackers=[]),
        "orchestration": orchestration,
        "dashboard": settings.dashboard.model_copy(update={"layout": _LAYOUT}),
    }
    return settings.model_copy(update=update)


async def _wait_for_command(
    queued: QueuedCommand, queue: CommandQueue, clock: FakeClock
) -> CommandOutcome:
    async def completed() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        outcome = queue.status(queued.id)
        return outcome is not None and outcome.status is not CommandStatus.PENDING

    await wait_until(completed, timeout=10)
    outcome = queue.status(queued.id)
    assert outcome is not None
    return outcome


async def _append_and_wait(
    command: Command, queue: CommandQueue, clock: FakeClock
) -> CommandOutcome:
    return await _wait_for_command(queue.append(command), queue, clock)


async def test_in_process_engine_acknowledges_commands_and_stops_before_dispatch(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    registry: AgentRegistry = build_registry()
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=registry)
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    engine_task = asyncio.create_task(engine.run())

    submitted = queue.append(Submit(title="queued task", request="queued task"))
    outcome = await _wait_for_command(submitted, queue, clock)
    assert outcome.status is CommandStatus.DONE
    task_id = str(outcome.result["task_id"]) if outcome.result is not None else ""
    assert task_id

    prioritized = await _append_and_wait(Prioritize(task_id=task_id, priority=9), queue, clock)
    assert prioritized.status is CommandStatus.DONE
    paused = await _append_and_wait(Pause(task_id=task_id), queue, clock)
    assert paused.status is CommandStatus.DONE
    resumed = await _append_and_wait(Resume(task_id=task_id), queue, clock)
    assert resumed.status is CommandStatus.DONE
    rung = await _append_and_wait(
        SetRung(task_id=task_id, role=Role.IMPLEMENTER, rung=_SECOND_RUNG), queue, clock
    )
    assert rung.status is CommandStatus.DONE
    retried = await _append_and_wait(Retry(task_id=task_id), queue, clock)
    assert retried.status is CommandStatus.DONE
    cancelled = await _append_and_wait(Cancel(task_id=task_id), queue, clock)
    assert cancelled.status is CommandStatus.DONE
    stopped = await _append_and_wait(Stop(), queue, clock)
    assert stopped.status is CommandStatus.DONE

    await engine_task
    events = EventLog(connection, clock).since(0, task_id=task_id)
    task = TaskStore(connection, clock).get(task_id)
    assert task is not None
    assert task.status is Status.CANCELLED
    assert not any(event.kind is EventKind.TASK_STARTED for event in events)
    connection.close()


async def test_unexpected_flow_error_escalates_without_stopping_engine(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _settings(max_cpu_pct=100)
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
    escalations = EscalationStore(connection, clock)

    try:

        async def escalated() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            return (
                (current := tasks.get(task.id)) is not None
                and current.status is Status.ESCALATED
                and bool(escalations.list_open())
            )

        await wait_until(escalated, timeout=10)
        assert not engine_task.done()
        escalation = escalations.list_open()[0]
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
        leader_ladder=[_FAKE_RUNG],
        implementer_ladder=[_FAKE_RUNG],
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
    settings = _settings(max_cpu_pct=100)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="stop batch", request="stop batch"))
    stopped = queue.append(Stop())
    engine_task = asyncio.create_task(engine.run())

    submit_outcome = await _wait_for_command(submitted, queue, clock)
    stop_outcome = await _wait_for_command(stopped, queue, clock)
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


async def test_engine_log_captures_child_errors_and_detaches_on_shutdown(tmp_path: Path) -> None:
    engine = build_engine(tmp_path, _settings(), registry=build_registry())
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
    settings = _settings(max_cpu_pct=100)
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
    tasks.set_repo(task.id, RepoState(base="missing", base_sha="0" * 40, branch="shikiru/broken"))
    task_root(tmp_path, task.id).mkdir(parents=True)
    engine_task = asyncio.create_task(engine.run())
    try:
        store = EscalationStore(connection, clock)

        async def escalated() -> bool:
            if clock.has_pending_sleep():
                clock.advance(0.25)
            return bool(store.list_open())

        await wait_until(escalated, timeout=20)
        escalations = store.list_open()
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


async def test_queued_resolution_is_acknowledged_by_the_daemon(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = _settings()
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    tasks = TaskStore(connection, clock)
    queue = CommandQueue(connection, clock)
    submitted = queue.append(Submit(title="resolve me", request="resolve me"))
    assert isinstance(submitted.command, Submit)
    task = tasks.insert_from_submit(submitted.command)
    tasks.apply_transition(
        task.id,
        Transition.stay(task, step=Step.IMPLEMENT, status=Status.ESCALATED),
    )
    tasks.set_slot(task.id, 1)
    events = EventLog(connection, clock)
    escalation_store = EscalationStore(connection, clock)
    escalation = await EscalationRaiser(
        escalation_store, events, RunStore(connection, clock), tmp_path
    ).raise_escalation(
        task,
        step=Step.IMPLEMENT,
        reason=EscalationReason.DECISION_REQUIRED,
        question="choose",
        attempted="test",
        reports=(),
        facts="",
        view_tail="",
    )
    resolve = queue.append(Resolve(escalation_id=escalation.id, resolution=CancelResolution()))
    stop = queue.append(Stop())
    engine_task = asyncio.create_task(engine.run())

    resolve_outcome = await _wait_for_command(resolve, queue, clock)
    stop_outcome = await _wait_for_command(stop, queue, clock)
    assert resolve_outcome.status is CommandStatus.DONE
    assert stop_outcome.status is CommandStatus.DONE
    await engine_task

    updated = tasks.get(task.id)
    assert updated is not None
    assert updated.status is Status.CANCELLED
    resolved = escalation_store.get(escalation.id)
    assert resolved is not None
    assert resolved.status is EscalationStatus.RESOLVED
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
    settings = _settings(max_cpu_pct=100)
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
        outcome = await _wait_for_command(submitted, queue, clock)
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
    settings = _settings(max_cpu_pct=100, max_parallel=2)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    queue = CommandQueue(connection, clock)
    first = queue.append(Submit(title="parallel one", request="parallel one", mode=Mode.IMPLEMENT))
    second = queue.append(Submit(title="parallel two", request="parallel two", mode=Mode.IMPLEMENT))
    engine_task = asyncio.create_task(engine.run())

    first_outcome = await _wait_for_command(first, queue, clock)
    second_outcome = await _wait_for_command(second, queue, clock)
    first_id = str(first_outcome.result["task_id"]) if first_outcome.result is not None else ""
    second_id = str(second_outcome.result["task_id"]) if second_outcome.result is not None else ""

    async def both_started() -> bool:
        if clock.has_pending_sleep():
            clock.advance(0.25)
        started = EventLog(connection, clock).since(0, kinds=[EventKind.TASK_STARTED])
        live = RunStore(connection, clock).list_live()
        started_ids = {event.task_id for event in started}
        live_ids = {run.task_id for run in live if run.role is Role.IMPLEMENTER}
        return started_ids == {first_id, second_id} and live_ids == {first_id, second_id}

    await wait_until(both_started, timeout=10)
    first_task = TaskStore(connection, clock).get(first_id)
    second_task = TaskStore(connection, clock).get(second_id)
    assert first_task is not None
    assert second_task is not None
    assert {first_task.slot, second_task.slot} == {0, 1}

    stopped = queue.append(Stop())
    stop_outcome = await _wait_for_command(stopped, queue, clock)
    assert stop_outcome.status is CommandStatus.DONE
    await engine_task
    connection.close()
