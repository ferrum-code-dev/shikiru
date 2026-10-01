"""Tick faults and tmux loss never end the engine (02 §1, 06 §5.7)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import shlex
import signal
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.bootstrap import build_engine
from shikiru.config.settings import NotifyConfig
from shikiru.domain.commands import Prioritize, Resume, Submit
from shikiru.domain.events import STOP_REASON_KEY, EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.engine.cosmetic_decorator import COSMETIC_LOGGER_NAME
from shikiru.engine.daemon import EngineLock
from shikiru.engine.tick_guard import EngineFaultLimitError
from shikiru.engine.turn_processes import read_turn_pid, turn_pid_path
from shikiru.errors import CommandFailed, CommandTimeoutError
from shikiru.paths import engine_lock_path, run_dir, task_root
from shikiru.private import make_private_dir
from shikiru.process import SubprocessRunner
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database, OperationalDatabaseError
from shikiru.store.events import EventLog
from shikiru.store.runs import RunStore
from shikiru.store.tasks import TaskStore
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.session import SessionView, session_name
from shikiru.workspace.github import GitHub
from tests.support.clock import FakeClock
from tests.support.engine import (
    build_registry,
    build_tmux,
    engine_settings,
    set_script,
    wait_for_command,
)
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until, wait_until_advancing

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
    from pathlib import Path
    from typing import Any

    from shikiru.domain.events import Event
    from shikiru.domain.tasks import Task
    from shikiru.engine.daemon import Engine
    from shikiru.process import ProcessResult
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

_TARGET = exact_target(session_name("test"))
_WAIT_SECONDS = 15
_USER_OPTION_PREFIX = "@shk_"
_RENAME_WINDOW = "rename-window"
_PANE_TITLE_FLAG = "-T"
_TMUX_TIMEOUT_SECONDS = 5.0


class _FlakyTmux(Tmux):
    def __init__(self, server: TmuxServer, failures: int, error: Exception) -> None:
        super().__init__(server.runner, socket=server.socket_name, env=server.env)
        self.failures = failures
        self._error = error

    async def target_exists(self, target: str) -> bool:
        if target == _TARGET and self.failures:
            self.failures -= 1
            raise self._error
        return await super().target_exists(target)


class _BadWindowTmux(Tmux):
    def __init__(self, server: TmuxServer) -> None:
        super().__init__(server.runner, socket=server.socket_name, env=server.env)
        self.bad_window: str | None = None

    async def target_exists(self, target: str) -> bool:
        if target == self.bad_window:
            raise CommandFailed(("tmux",), 1, "can't find window")
        return await super().target_exists(target)


class _RecordedLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _DecorationTimeoutRunner(SubprocessRunner):
    async def run(self, argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        if _RENAME_WINDOW in argv or _PANE_TITLE_FLAG in argv or _is_user_option(argv):
            raise CommandTimeoutError(argv, _TMUX_TIMEOUT_SECONDS)
        return await super().run(argv, **kwargs)


def _is_user_option(argv: Sequence[str]) -> bool:
    return any(arg.startswith(_USER_OPTION_PREFIX) for arg in argv)


class _Harness:
    def __init__(self, project: Path, engine: Engine, clock: FakeClock) -> None:
        self.clock = clock
        self.engine = engine
        self.connection = Database(project / ".shikiru" / "state.db").connect()
        self.tasks = TaskStore(self.connection, clock)
        self.runs = RunStore(self.connection, clock)
        self.events = EventLog(self.connection, clock)
        self.queue = CommandQueue(self.connection, clock)
        self.task = asyncio.create_task(engine.run())

    async def until(self, condition: Callable[[], object]) -> None:
        async def reached() -> bool:
            result = condition()
            return bool(await result) if asyncio.iscoroutine(result) else bool(result)

        await wait_until_advancing(self.clock, reached, _WAIT_SECONDS)

    def kinds(self) -> list[EventKind]:
        return [event.kind for event in self.events.since(0)]


@asynccontextmanager
async def _engine(
    project: Path,
    tmux_server: TmuxServer,
    *,
    hook: str = "",
    flaky: tuple[int, Exception] | None = None,
    tmux_override: Tmux | None = None,
    fault_limit: int | None = None,
    max_parallel: int = 1,
    seed: Callable[[Database, FakeClock, Path], Awaitable[None]] | None = None,
) -> AsyncGenerator[_Harness]:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT, max_parallel=max_parallel)
    orchestration = settings.orchestration
    if fault_limit is not None:
        faults = orchestration.faults.model_copy(update={"unexpected_fault_limit": fault_limit})
        orchestration = orchestration.model_copy(update={"faults": faults})
    settings = settings.model_copy(
        update={
            "orchestration": orchestration,
            "notify": NotifyConfig(command=hook, timeout_seconds=30),
        }
    )
    tmux = build_tmux(tmux_server)
    await init_project_repo(project)
    await SessionView(tmux).ensure("test", project, home_command=("/bin/sh",))
    if flaky is not None:
        tmux = _FlakyTmux(tmux_server, *flaky)
    if tmux_override is not None:
        tmux = tmux_override
    engine = build_engine(project, settings, clock=clock, tmux=tmux, registry=build_registry())
    if seed is not None:
        await seed(Database(project / ".shikiru" / "state.db"), clock, project)
    harness = _Harness(project, engine, clock)
    try:
        yield harness
    finally:
        if not harness.task.done():
            engine.request_stop()
            await harness.until(harness.task.done)
        harness.connection.close()


async def _seed_tasks(
    database: Database, clock: FakeClock, project: Path, *, held_running: bool = False
) -> None:
    connection = database.connect()
    try:
        tasks = TaskStore(connection, clock)
        events = EventLog(connection, clock)
        queue = CommandQueue(connection, clock)

        def insert(title: str) -> Task:
            queued = queue.append(Submit(title=title, request=title))
            assert isinstance(queued.command, Submit)
            return tasks.insert_from_submit(queued.command)

        cancelled = insert("cancelled")
        tasks.apply_transition(
            cancelled.id, Transition.stay(cancelled, step=Step.IMPLEMENT, status=Status.CANCELLED)
        )
        make_private_dir(task_root(project, cancelled.id))
        url = await GitHub(SubprocessRunner()).create_pull_request(
            project, base="main", head="shikiru/T-1-cancelled", title="cancelled", body="summary"
        )
        events.append(
            EventKind.PR_OPENED,
            task_id=cancelled.id,
            message="opened",
            data={"url": url, "branch": "shikiru/T-1-cancelled", "commits": ["c1"]},
        )
        running = insert("running")
        tasks.apply_transition(
            running.id,
            dataclasses.replace(
                Transition.stay(running, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
            ),
        )
        tasks.set_slot(running.id, 1)
        if held_running:
            tasks.set_holds(running.id, frozenset({Hold.PAUSED}))
        insert("queued")
    finally:
        connection.close()


async def _submit_running(harness: _Harness) -> str:
    queued = harness.queue.append(Submit(title="stalled", request="stalled", mode=Mode.IMPLEMENT))
    outcome = await wait_for_command(queued, harness.queue, harness.clock)
    task_id = str(outcome.result["task_id"]) if outcome.result is not None else ""

    async def running() -> bool:
        return harness.runs.get_live(task_id, Role.IMPLEMENTER) is not None

    await harness.until(running)
    return task_id


async def test_killing_the_tmux_server_while_a_task_runs_restores_the_layout_and_the_task_continues(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    async with _engine(tmp_path, tmux_server) as harness:
        task_id = await _submit_running(harness)
        await harness.until(lambda: (task := harness.tasks.get(task_id)) and task.window_id)
        await tmux_server.run("kill-server", check=False)
        tmux = build_tmux(tmux_server)

        async def restored() -> bool:
            task = harness.tasks.get(task_id)
            return (
                await tmux.target_exists(_TARGET)
                and task is not None
                and task.window_id is not None
                and await tmux.target_exists(task.window_id)
                and all([await tmux.pane_exists(pane) for pane in task.panes.values()])
            )

        await harness.until(restored)

        assert not harness.task.done()
        task = harness.tasks.get(task_id)
        assert task is not None
        assert task.status is Status.RUNNING
        assert harness.runs.get_live(task_id, Role.IMPLEMENTER) is not None


async def test_a_fault_in_one_tick_does_not_stop_later_ticks(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _engine(tmp_path, tmux_server, flaky=(1, RuntimeError("boom"))) as harness:
        harness.queue.append(Submit(title="later", request="later", mode=Mode.IMPLEMENT))
        await harness.until(lambda: EventKind.ENGINE_FAULT in harness.kinds())
        await harness.until(lambda: EventKind.TASK_STARTED in harness.kinds())

        assert not harness.task.done()
        fault = harness.events.since(0, kinds=[EventKind.ENGINE_FAULT])[0]
        assert fault.data["error"] == "RuntimeError"


async def test_an_unrecoverable_stop_records_its_reason_and_notifies(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    payload = tmp_path / "notified.json"
    hook = f"/bin/sh -c {shlex.quote(f'cat > {payload}')}"
    async with _engine(
        tmp_path, tmux_server, hook=hook, flaky=(10**6, RuntimeError("boom")), fault_limit=2
    ) as harness:
        await harness.until(harness.task.done)
        with pytest.raises(EngineFaultLimitError):
            harness.task.result()

        stopped = harness.events.since(0, kinds=[EventKind.ENGINE_STOPPED])[0]
        assert stopped.data[STOP_REASON_KEY]
        assert str(stopped.data[STOP_REASON_KEY]) in payload.read_text()


async def _advance(harness: _Harness, seconds: int) -> None:
    deadline = harness.clock.now() + timedelta(seconds=seconds)

    async def reached() -> bool:
        return harness.clock.now() >= deadline

    await wait_until_advancing(harness.clock, reached, _WAIT_SECONDS, step_seconds=10)


async def test_a_failing_cleanup_does_not_stop_dispatch_or_resume(
    tmp_path: Path,
    tmux_server: TmuxServer,
    fake_github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])

    async def seed(database: Database, clock: FakeClock, project: Path) -> None:
        await _seed_tasks(database, clock, project)
        fake_github.break_pull_requests()

    async with _engine(tmp_path, tmux_server, max_parallel=2, seed=seed) as harness:
        await harness.until(lambda: harness.runs.get_live("T-2", Role.IMPLEMENTER) is not None)
        await harness.until(
            lambda: (
                "T-3"
                in [e.task_id for e in harness.events.since(0, kinds=[EventKind.TASK_STARTED])]
            )
        )

        faults = harness.events.since(0, kinds=[EventKind.ENGINE_FAULT])
        assert [fault.task_id for fault in faults] == ["T-1"]
        assert not harness.task.done()


async def _seed_held_running_with_resume(database: Database, clock: FakeClock, _: Path) -> None:
    connection = database.connect()
    try:
        tasks = TaskStore(connection, clock)
        queue = CommandQueue(connection, clock)
        queued = queue.append(Submit(title="held", request="held"))
        assert isinstance(queued.command, Submit)
        task = tasks.insert_from_submit(queued.command)
        tasks.apply_transition(
            task.id,
            dataclasses.replace(
                Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING), iteration=1
            ),
        )
        tasks.set_slot(task.id, 1)
        tasks.set_holds(task.id, frozenset({Hold.PAUSED}))
        queue.append(Resume(task_id=task.id))
    finally:
        connection.close()


@pytest.mark.parametrize(
    "error",
    [CommandFailed(("tmux",), 1, "no server"), CommandTimeoutError(("tmux",), 1.0)],
    ids=["tmux-failed", "tmux-timeout"],
)
async def test_a_pending_resume_waits_for_a_start_that_failed_and_its_turn_survives_the_retry(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    async with _engine(
        tmp_path, tmux_server, flaky=(1, error), seed=_seed_held_running_with_resume
    ) as harness:
        await harness.until(lambda: harness.runs.get_live("T-1", Role.IMPLEMENTER) is not None)
        await _advance(harness, 300)

        task = harness.tasks.get("T-1")
        assert task is not None
        assert (task.status, task.status_reason) == (Status.RUNNING, None)
        assert harness.runs.get_live("T-1", Role.IMPLEMENTER) is not None
        assert turn_pid_path(run_dir(tmp_path, "T-1"), Role.IMPLEMENTER).exists()
        assert EventKind.ENGINE_FAULT in harness.kinds()


async def test_no_flow_starts_beside_an_orphan_the_start_could_not_end(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    unreadable_record = turn_pid_path(run_dir(tmp_path, "T-1"), Role.IMPLEMENTER)

    async def seed(database: Database, clock: FakeClock, project: Path) -> None:
        await _seed_held_running_with_resume(database, clock, project)
        connection = database.connect()
        try:
            TaskStore(connection, clock).set_holds("T-1", frozenset())
        finally:
            connection.close()
        unreadable_record.mkdir(parents=True)

    async with _engine(tmp_path, tmux_server, seed=seed) as harness:
        await harness.until(lambda: EventKind.ENGINE_FAULT in harness.kinds())
        await _advance(harness, 60)
        assert harness.runs.get_live("T-1", Role.IMPLEMENTER) is None

        unreadable_record.rmdir()

        await harness.until(lambda: harness.runs.get_live("T-1", Role.IMPLEMENTER) is not None)


async def test_a_tmux_decoration_that_times_out_never_kills_or_escalates_a_turn(
    tmp_path: Path,
    tmux_server: TmuxServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    logs = _RecordedLogs()
    tmux = Tmux(_DecorationTimeoutRunner(), socket=tmux_server.socket_name, env=tmux_server.env)
    cosmetic_logger = logging.getLogger(COSMETIC_LOGGER_NAME)
    cosmetic_logger.addHandler(logs)
    try:
        async with _engine(tmp_path, tmux_server, tmux_override=tmux) as harness:
            task_id = await _submit_running(harness)
            await _advance(harness, 120)

            task = harness.tasks.get(task_id)
            assert task is not None
            assert task.status is Status.RUNNING
            assert harness.runs.get_live(task_id, Role.IMPLEMENTER) is not None
            assert EventKind.ESCALATION not in harness.kinds()
            assert EventKind.ENGINE_FAULT not in harness.kinds()
            failed = [record.args for record in logs.records]
            assert failed
            assert len(failed) == len(set(failed))
    finally:
        cosmetic_logger.removeHandler(logs)


async def test_one_bad_task_window_does_not_starve_other_tasks_and_is_renotified_while_it_lasts(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    tmux = _BadWindowTmux(tmux_server)
    async with _engine(tmp_path, tmux_server, tmux_override=tmux, max_parallel=2) as harness:
        broken = await _submit_running(harness)
        await harness.until(lambda: (task := harness.tasks.get(broken)) and task.window_id)
        task = harness.tasks.get(broken)
        assert task is not None
        tmux.bad_window = task.window_id
        harness.queue.append(Submit(title="other", request="other", mode=Mode.IMPLEMENT))

        def faults() -> list[Event]:
            return list(harness.events.since(0, kinds=[EventKind.ENGINE_FAULT]))

        await harness.until(
            lambda: any(
                e.task_id != broken for e in harness.events.since(0, kinds=[EventKind.TASK_STARTED])
            )
        )
        await harness.until(lambda: len(faults()) == 1)
        await _advance(harness, 310)
        await harness.until(lambda: len(faults()) >= 2)

        assert [fault.task_id for fault in faults()] == [broken, broken]
        assert faults()[1].data["repeat"] > 1
        assert not harness.task.done()


async def test_a_database_error_in_the_command_path_does_not_stop_the_engine(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _engine(tmp_path, tmux_server) as harness:
        harness.connection.execute("ALTER TABLE commands RENAME TO commands_away")
        await harness.until(lambda: EventKind.ENGINE_FAULT in harness.kinds())
        fault = harness.events.since(0, kinds=[EventKind.ENGINE_FAULT])[0]
        harness.connection.execute("ALTER TABLE commands_away RENAME TO commands")
        queued = harness.queue.append(Submit(title="after", request="after", mode=Mode.IMPLEMENT))

        outcome = await wait_for_command(queued, harness.queue, harness.clock)

        assert outcome.status is CommandStatus.DONE
        assert fault.data["recoverable"] is True
        assert not harness.task.done()


async def test_a_database_error_persisting_the_notification_cursor_does_not_stop_the_engine(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _engine(tmp_path, tmux_server) as harness:
        await harness.until(lambda: EventKind.ENGINE_STARTED in harness.kinds())
        harness.connection.execute("ALTER TABLE notification_cursor RENAME TO cursor_away")
        harness.queue.append(Submit(title="after", request="after", mode=Mode.IMPLEMENT))

        await harness.until(
            lambda: harness.task.done() or EventKind.ENGINE_FAULT in harness.kinds()
        )

        assert not harness.task.done()
        harness.connection.execute("ALTER TABLE cursor_away RENAME TO notification_cursor")
        await harness.until(lambda: EventKind.ENGINE_RECOVERED in harness.kinds())
        assert not harness.task.done()


async def test_a_database_error_at_shutdown_still_ends_the_running_turn_and_releases_the_lock(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])
    async with _engine(tmp_path, tmux_server) as harness:
        task_id = await _submit_running(harness)
        pid_path = turn_pid_path(run_dir(tmp_path, task_id), Role.IMPLEMENTER)
        await wait_until(pid_path.exists, _WAIT_SECONDS)
        identity = read_turn_pid(pid_path)
        harness.connection.execute("ALTER TABLE events RENAME TO events_away")
        harness.engine.request_stop()
        try:
            await harness.until(harness.task.done)

            with pytest.raises(OperationalDatabaseError):
                harness.task.result()
            assert not SubprocessRunner().group_is_running(identity.pid)
            assert EngineLock.pid_if_running(engine_lock_path(tmp_path)) is None
        finally:
            SubprocessRunner().signal_group(identity, signal.SIGKILL)


async def test_an_engine_that_starts_while_the_database_is_unwritable_keeps_trying(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async def lock(database: Database, _clock: FakeClock, _project: Path) -> None:
        connection = database.connect()
        connection.execute("ALTER TABLE events RENAME TO events_away")
        connection.close()

    async with _engine(tmp_path, tmux_server, seed=lock) as harness:
        started = harness.clock.now()
        await harness.until(
            lambda: harness.task.done() or harness.clock.now() - started > timedelta(seconds=20)
        )

        assert not harness.task.done()
        harness.connection.execute("ALTER TABLE events_away RENAME TO events")
        await harness.until(lambda: EventKind.ENGINE_STARTED in harness.kinds())
        assert not harness.task.done()


@pytest.mark.usefixtures("fake_github")
async def test_a_command_hit_by_a_database_error_stays_pending_and_applies_afterwards(
    tmp_path: Path, tmux_server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_script(monkeypatch, [{"kind": "stall"}])

    async def seed(database: Database, clock: FakeClock, project: Path) -> None:
        await _seed_tasks(database, clock, project, held_running=True)

    async with _engine(tmp_path, tmux_server, seed=seed) as harness:
        harness.connection.execute("ALTER TABLE tasks RENAME TO tasks_away")
        queued = harness.queue.append(Prioritize(task_id="T-3", priority=7))
        await harness.until(lambda: EventKind.ENGINE_FAULT in harness.kinds())
        status = harness.queue.status(queued.id)
        assert status is not None
        assert status.status is CommandStatus.PENDING
        harness.connection.execute("ALTER TABLE tasks_away RENAME TO tasks")

        outcome = await wait_for_command(queued, harness.queue, harness.clock)

        assert outcome.status is CommandStatus.DONE
        prioritised = harness.tasks.get("T-3")
        assert prioritised is not None
        assert prioritised.priority == 7
