from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

_DEFAULT_SOCKET_NAME = "default"
_NO_SERVER_MARKERS = ("no server running", "error connecting to")
_PRIVATE_SESSION_NAME = "test"


async def _session_names_on_default_socket(runner: SubprocessRunner) -> list[str]:
    result = await runner.run(
        ["tmux", "-L", _DEFAULT_SOCKET_NAME, "list-sessions", "-F", "#{session_name}"],
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        if any(marker in result.stderr for marker in _NO_SERVER_MARKERS):
            return []
        message = f"unexpected tmux failure on the default socket: {result.stderr}"
        raise AssertionError(message)
    return result.stdout.splitlines()


async def test_tmux_server_socket_is_named_after_the_private_socket_not_default(
    tmux_server: TmuxServer,
) -> None:
    socket_path = (await tmux_server.run("display-message", "-p", "#{socket_path}")).stdout.strip()

    assert Path(socket_path).name == tmux_server.socket_name
    assert Path(socket_path).name != _DEFAULT_SOCKET_NAME


async def test_session_on_the_private_socket_is_invisible_on_the_default_socket(
    tmux_server: TmuxServer,
) -> None:
    runner = SubprocessRunner()
    before = await _session_names_on_default_socket(runner)

    await tmux_server.run("new-window")

    after = await _session_names_on_default_socket(runner)

    assert after == before
    assert _PRIVATE_SESSION_NAME not in after
