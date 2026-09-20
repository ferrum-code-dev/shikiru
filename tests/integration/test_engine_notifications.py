"""Notifier delivery through the private tmux session and command boundary."""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.engine.notifier import Notifier
from shikiru.process import SubprocessRunner
from shikiru.store.events import EventLog
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.engine import build_tmux
from tests.support.factories import TASK_CREATED_AT

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
    connection: Connection, tmux_server: TmuxServer, tmp_path: Path
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    notifier = Notifier(
        tmux,
        SubprocessRunner(),
        session_target=exact_target(session_name("test")),
        command="false",
        timeout_seconds=5,
    )

    await notifier.notify(EventLog(connection, clock).append(EventKind.ESCALATION, message="help"))
