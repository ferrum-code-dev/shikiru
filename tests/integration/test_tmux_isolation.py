from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

_DEFAULT_SOCKET_NAME = "default"
_NO_SERVER_MARKERS = ("no server running", "error connecting to")
_PRIVATE_SESSION_NAME = "test"
_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0


async def _session_names(runner: SubprocessRunner, socket_name: str) -> list[str]:
    result = await runner.run(
        ["tmux", "-L", socket_name, "list-sessions", "-F", "#{session_name}"],
        timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        if any(marker in result.stderr for marker in _NO_SERVER_MARKERS):
            return []
        message = f"unexpected tmux failure on socket {socket_name}: {result.stderr}"
        raise AssertionError(message)
    return result.stdout.splitlines()


@pytest.fixture
async def other_socket_name() -> AsyncIterator[str]:
    """A second, independent private tmux socket, never the operator's default one."""
    socket_name = f"shikiru-test-{uuid.uuid4().hex}"
    runner = SubprocessRunner()
    try:
        yield socket_name
    finally:
        await Tmux(runner, socket=socket_name).kill_server()


async def test_tmux_server_socket_is_named_after_the_private_socket_not_default(
    tmux_server: TmuxServer,
) -> None:
    socket_path = (await tmux_server.run("display-message", "-p", "#{socket_path}")).stdout.strip()

    assert Path(socket_path).name == tmux_server.socket_name
    assert Path(socket_path).name != _DEFAULT_SOCKET_NAME


async def test_session_on_the_private_socket_is_invisible_from_another_private_socket(
    tmux_server: TmuxServer, other_socket_name: str
) -> None:
    runner = SubprocessRunner()
    own_socket_sessions = await _session_names(runner, tmux_server.socket_name)
    assert _PRIVATE_SESSION_NAME in own_socket_sessions

    await runner.run(
        ["tmux", "-L", other_socket_name, "-f", "/dev/null", "new-session", "-d", "-s", "other"],
        timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
        check=True,
    )
    other_socket_sessions = await _session_names(runner, other_socket_name)

    assert _PRIVATE_SESSION_NAME not in other_socket_sessions
