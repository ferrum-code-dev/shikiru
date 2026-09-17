from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

pytestmark = pytest.mark.asyncio


async def _default_socket_snapshot(runner: SubprocessRunner) -> tuple[int, str]:
    result = await runner.run(["tmux", "list-sessions"], timeout=5)
    return result.returncode, result.stdout


async def test_tmux_server_fixture_never_touches_the_default_socket(
    tmux_server: TmuxServer,
) -> None:
    runner = SubprocessRunner()
    before = await _default_socket_snapshot(runner)

    tmux_server.run("new-window")
    tmux_server.run("list-sessions")

    after_use = await _default_socket_snapshot(runner)
    assert after_use == before

    tmux_server.run("kill-server", check=False)

    after_kill = await _default_socket_snapshot(runner)
    assert after_kill == before
