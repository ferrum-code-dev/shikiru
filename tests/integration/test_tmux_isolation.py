from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

_DEFAULT_SOCKET_NAME = "default"


async def test_tmux_server_socket_is_named_after_the_private_socket_not_default(
    tmux_server: TmuxServer,
) -> None:
    socket_path = (await tmux_server.run("display-message", "-p", "#{socket_path}")).stdout.strip()

    assert Path(socket_path).name == tmux_server.socket_name
    assert Path(socket_path).name != _DEFAULT_SOCKET_NAME
