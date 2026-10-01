"""A slow or failing notification hook never holds up the engine loop (06 §5.7)."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

import psutil

from shikiru.bootstrap import build_engine
from shikiru.config.settings import NotifyConfig
from shikiru.domain.commands import Pause, Stop, Submit
from shikiru.domain.events import EventKind
from shikiru.engine.daemon import COMMAND_POLL_SECONDS
from shikiru.paths import engine_log_path
from shikiru.store.commands import CommandQueue, CommandStatus
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from shikiru.store.notification_cursor import NotificationCursor
from shikiru.tmux.session import SessionView
from tests.support.clock import FakeClock
from tests.support.engine import build_registry, build_tmux, engine_settings, wait_for_command
from tests.support.factories import CPU_UNCAPPED_PCT, TASK_CREATED_AT
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from shikiru.engine.daemon import Engine
    from tests.support.tmux import TmuxServer

_WAIT_SECONDS = 10.0
_FAILING_HOOK_EXIT_CODE = 3
_FAILING_HOOK_DELIVERIES = 3
_REPLAYED_ESCALATIONS = 2


class _Harness:
    def __init__(self, tmp_path: Path, engine: Engine, clock: FakeClock) -> None:
        self.tmp_path = tmp_path
        self.engine = engine
        self.clock = clock
        self.connection = Database(tmp_path / ".shikiru" / "state.db").connect()
        self.queue = CommandQueue(self.connection, clock)
        self.events = EventLog(self.connection, clock)
        self.engine_task: asyncio.Task[None] = asyncio.create_task(engine.run())

    async def acknowledged(self, command: Pause | Stop | Submit) -> CommandStatus:
        outcome = await wait_for_command(self.queue.append(command), self.queue, self.clock)
        return outcome.status

    async def until(self, condition: Callable[[], bool]) -> None:
        def reached() -> bool:
            if self.clock.has_pending_sleep():
                self.clock.advance(COMMAND_POLL_SECONDS)
            return condition()

        await wait_until(reached, _WAIT_SECONDS)

    def escalate(self, message: str) -> None:
        self.events.append(EventKind.ESCALATION, message=message)

    def engine_log(self) -> str:
        return engine_log_path(self.tmp_path).read_text()


@asynccontextmanager
async def _engine(
    tmp_path: Path, tmux_server: TmuxServer, hook: str, *, restart: bool = False
) -> AsyncGenerator[_Harness]:
    clock = FakeClock(TASK_CREATED_AT)
    settings = engine_settings(max_cpu_pct=CPU_UNCAPPED_PCT)
    settings = settings.model_copy(
        update={"notify": NotifyConfig(command=hook, timeout_seconds=30)}
    )
    tmux = build_tmux(tmux_server)
    if not restart:
        await init_project_repo(tmp_path)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    engine = build_engine(tmp_path, settings, clock=clock, tmux=tmux, registry=build_registry())
    harness = _Harness(tmp_path, engine, clock)
    try:
        await harness.until(lambda: harness.events.latest_id() > 0)
        yield harness
    finally:
        if not harness.engine_task.done():
            engine.request_stop()
            clock.advance(1)
            with suppress(Exception):
                await asyncio.wait_for(harness.engine_task, _WAIT_SECONDS)
        harness.connection.close()


def _hook(script: str) -> str:
    return f"/bin/sh -c {shlex.quote(script)}"


def _kill(pid_file: Path) -> None:
    if pid_file.exists() and (text := pid_file.read_text()):
        with suppress(ProcessLookupError):
            os.kill(int(text), 9)


async def test_a_hook_blocked_on_a_fifo_does_not_hold_up_commands_or_scheduling(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    fifo = tmp_path / "hook.fifo"
    pid_file = tmp_path / "hook.pid"
    os.mkfifo(fifo)
    hook = _hook(f"echo $$ > {pid_file}; read line < {fifo}")
    try:
        async with _engine(tmp_path, tmux_server, hook) as env:
            env.escalate("needs help")
            await env.until(lambda: pid_file.exists() and bool(pid_file.read_text()))

            assert (
                await env.acknowledged(Submit(title="work", request="work")) is CommandStatus.DONE
            )
            await env.until(lambda: bool(env.events.since(0, kinds=[EventKind.TASK_STARTED])))
            task_id = env.events.since(0, kinds=[EventKind.TASK_STARTED])[0].task_id
            assert task_id is not None
            assert await env.acknowledged(Pause(task_id=task_id)) is CommandStatus.DONE
            assert await env.acknowledged(Stop()) is CommandStatus.DONE

            await asyncio.wait_for(env.engine_task, _WAIT_SECONDS)
            assert not psutil.pid_exists(int(pid_file.read_text()))
    finally:
        _kill(pid_file)


async def test_a_failing_hook_is_logged_recorded_once_and_delivery_continues(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    received = tmp_path / "received.jsonl"
    hook = _hook(f"cat >> {received}; echo >> {received}; exit {_FAILING_HOOK_EXIT_CODE}")
    async with _engine(tmp_path, tmux_server, hook) as env:
        env.escalate("first")
        env.escalate("second")

        def delivered() -> list[str]:
            return received.read_text().splitlines() if received.exists() else []

        await env.until(lambda: len(delivered()) == _FAILING_HOOK_DELIVERIES)
        messages = [json.loads(line) for line in delivered()]
        assert [message["message"] for message in messages[:2]] == ["first", "second"]
        assert messages[2]["kind"] == EventKind.ENGINE_FAULT.value
        await env.until(
            lambda: (
                env.engine_log().count("external notification failed") == _FAILING_HOOK_DELIVERIES
            )
        )
        assert await env.acknowledged(Stop()) is CommandStatus.DONE


async def test_a_missing_hook_command_is_logged_per_event(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    async with _engine(tmp_path, tmux_server, str(tmp_path / "missing-hook")) as env:
        env.escalate("first")
        env.escalate("second")
        await env.until(
            lambda: (
                env.engine_log().count("external notification failed") == _FAILING_HOOK_DELIVERIES
            )
        )
        assert await env.acknowledged(Stop()) is CommandStatus.DONE


def _recording_hook(received: Path) -> str:
    return _hook(f"cat >> {received}; echo >> {received}")


def _delivered_messages(received: Path) -> list[str]:
    if not received.exists():
        return []
    return [json.loads(line)["message"] for line in received.read_text().splitlines() if line]


async def _stop(env: _Harness) -> None:
    assert await env.acknowledged(Stop()) is CommandStatus.DONE
    await asyncio.wait_for(env.engine_task, _WAIT_SECONDS)


def _append_while_down(tmp_path: Path, *events: tuple[EventKind, str]) -> None:
    connection = Database(tmp_path / ".shikiru" / "state.db").connect()
    try:
        log = EventLog(connection, FakeClock(TASK_CREATED_AT))
        for kind, message in events:
            log.append(kind, message=message)
    finally:
        connection.close()


async def test_an_escalation_queued_behind_a_stuck_hook_is_notified_once_after_a_restart(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    fifo = tmp_path / "hook.fifo"
    pid_file = tmp_path / "hook.pid"
    received = tmp_path / "received.jsonl"
    os.mkfifo(fifo)
    try:
        async with _engine(
            tmp_path, tmux_server, _hook(f"echo $$ > {pid_file}; read l < {fifo}")
        ) as env:
            env.escalate("stuck")
            await env.until(lambda: pid_file.exists() and bool(pid_file.read_text()))
            env.escalate("queued")
            await _stop(env)
    finally:
        _kill(pid_file)

    async with _engine(tmp_path, tmux_server, _recording_hook(received), restart=True) as env:
        await env.until(lambda: len(_delivered_messages(received)) == _REPLAYED_ESCALATIONS)
        assert _delivered_messages(received) == ["stuck", "queued"]
        last_escalation = env.events.since(0, kinds=[EventKind.ESCALATION])[-1].id
        await env.until(lambda: (NotificationCursor(env.connection).read() or 0) >= last_escalation)
        await _stop(env)

    async with _engine(tmp_path, tmux_server, _recording_hook(received), restart=True) as env:
        env.escalate("after")
        await env.until(lambda: "after" in _delivered_messages(received))
        assert _delivered_messages(received) == ["stuck", "queued", "after"]
        await _stop(env)


async def test_an_escalation_committed_while_down_is_notified_but_informational_is_not(
    tmp_path: Path, tmux_server: TmuxServer
) -> None:
    received = tmp_path / "received.jsonl"
    async with _engine(tmp_path, tmux_server, _recording_hook(received)) as env:
        await _stop(env)
    _append_while_down(
        tmp_path,
        (EventKind.TASK_DONE, "finished while down"),
        (EventKind.ESCALATION, "raised while down"),
    )

    async with _engine(tmp_path, tmux_server, _recording_hook(received), restart=True) as env:
        await env.until(lambda: bool(_delivered_messages(received)))
        env.escalate("marker")
        await env.until(lambda: "marker" in _delivered_messages(received))
        assert _delivered_messages(received) == ["raised while down", "marker"]
        await _stop(env)
