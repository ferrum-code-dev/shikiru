"""Notifier delivery through the private tmux session and command boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.engine.notifier import Notifier
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.engine import build_tmux
from tests.support.factories import TASK_CREATED_AT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer


async def test_notifier_delivers_only_completion_and_escalation_events(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    payload_path = tmp_path / "notification.json"
    command = f"/bin/sh -c {shlex.quote(f'cat > {payload_path}')}"
    notifier = Notifier(
        tmux,
        SubprocessRunner(),
        session_target=exact_target(session_name("test")),
        command=command,
        timeout_seconds=5,
    )
    events = EventLog(connection, clock)

    await notifier.notify(events.append(EventKind.TASK_QUEUED, message="queued", task_id="T-1"))
    assert not payload_path.exists()

    await notifier.notify(events.append(EventKind.ESCALATION, message="help", task_id="T-1"))
    payload = await asyncio.to_thread(payload_path.read_text)
    assert json.loads(payload)["kind"] == EventKind.ESCALATION.value

    await notifier.notify(events.append(EventKind.TASK_DONE, message="done", task_id="T-1"))
    payload = await asyncio.to_thread(payload_path.read_text)
    assert json.loads(payload)["kind"] == EventKind.TASK_DONE.value


async def test_notifier_keeps_external_delivery_when_tmux_display_fails(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    payload_path = tmp_path / "notification.json"
    command = f"/bin/sh -c {shlex.quote(f'cat > {payload_path}')}"
    notifier = Notifier(
        tmux,
        SubprocessRunner(),
        session_target=exact_target(session_name("missing")),
        command=command,
        timeout_seconds=5,
    )

    await notifier.notify(EventLog(connection, clock).append(EventKind.TASK_DONE, message="done"))

    assert json.loads(payload_path.read_text())["kind"] == EventKind.TASK_DONE.value


async def test_notifier_isolates_external_command_failure(
    connection: Connection,
    tmux_server: TmuxServer,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    notifier = Notifier(
        tmux,
        SubprocessRunner(),
        session_target=exact_target(session_name("test")),
        command=str(tmp_path / "missing-notification-command"),
        timeout_seconds=5,
    )

    event = EventLog(connection, clock).append(EventKind.ESCALATION, message="help")
    with caplog.at_level(logging.ERROR, logger="shikiru.engine.notifier"):
        await notifier.notify(event)

    failure = next(
        record for record in caplog.records if "external notification failed" in record.getMessage()
    )
    assert str(event.id) in failure.getMessage()
    assert failure.exc_info is not None


_HOOK_WAIT_SECONDS = 10.0
_SHORT_HOOK_TIMEOUT_SECONDS = 1.0
_LONG_HOOK_TIMEOUT_SECONDS = 600.0


@pytest.mark.parametrize("cancelled", [False, True], ids=["timeout", "cancellation"])
async def test_hook_descendants_die_with_the_hook(
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path, *, cancelled: bool
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    runner = SubprocessRunner()
    pid_file = tmp_path / "descendant.pid"
    script = f"sleep 1000 & echo $! > {pid_file}; wait"
    notifier = Notifier(
        build_tmux(tmux_server),
        runner,
        session_target=exact_target(session_name("missing")),
        command=f"/bin/sh -c {shlex.quote(script)}",
        timeout_seconds=_LONG_HOOK_TIMEOUT_SECONDS if cancelled else _SHORT_HOOK_TIMEOUT_SECONDS,
    )
    event = EventLog(connection, clock).append(EventKind.TASK_DONE, message="done")
    delivery = asyncio.create_task(notifier.notify(event))
    try:
        await wait_until(
            lambda: pid_file.exists() and bool(pid_file.read_text()), _HOOK_WAIT_SECONDS
        )
        descendant = runner.identify(int(pid_file.read_text()))
        assert descendant is not None
        if cancelled:
            delivery.cancel()
            with suppress(asyncio.CancelledError):
                await delivery
        else:
            await delivery

        await wait_until(lambda: not runner.is_running(descendant), _HOOK_WAIT_SECONDS)
    finally:
        with suppress(ProcessLookupError, ValueError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
