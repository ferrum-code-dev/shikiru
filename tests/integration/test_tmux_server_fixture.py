from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio

_EXPECTED_LOGIN_SHELL_COMMAND = "-sh"


async def test_session_pane_actually_runs_the_configured_default_shell(
    tmux_server: TmuxServer,
) -> None:
    pane_pid = (await tmux_server.run("display-message", "-p", "#{pane_pid}")).stdout.strip()

    result = await SubprocessRunner().run(["ps", "-o", "comm=", "-p", pane_pid], timeout=5)

    assert result.stdout.strip() == _EXPECTED_LOGIN_SHELL_COMMAND
