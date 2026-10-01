"""An isolated tmux server for tests, on a private socket."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import ProcessResult, SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SESSION_NAME = "test"
_SESSION_WIDTH = "200"
TMUX_COMMAND = "tmux"
SOCKET_FLAG = "-L"
CONFIG_FLAG = "-f"
_SESSION_HEIGHT = "50"
_DEFAULT_SHELL = "/bin/sh"
_TMUX_INHERITED_ENV_VARS = ("TMUX", "TMUX_PANE")
_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class TmuxServer:
    """A private tmux server on its own socket, isolated from the operator's own tmux."""

    socket_name: str
    env: dict[str, str]
    runner: SubprocessRunner = field(default_factory=SubprocessRunner)
    session: str = _SESSION_NAME

    async def run(self, *args: str, check: bool = True) -> ProcessResult:
        """Run a tmux command scoped to this server's socket."""
        return await self.runner.run(
            [TMUX_COMMAND, SOCKET_FLAG, self.socket_name, *args],
            timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
            env=self.env,
            check=check,
        )

    async def socket_path(self) -> Path:
        """Return this server's socket file path."""
        result = await self.run("display-message", "-p", "#{socket_path}")
        return Path(result.stdout.strip())


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _TMUX_INHERITED_ENV_VARS}


@pytest.fixture
async def tmux_server() -> AsyncIterator[TmuxServer]:
    """Start a private, isolated tmux server and kill only that server on teardown."""
    socket_name = f"shikiru-test-{uuid.uuid4().hex}"
    env = _clean_env()
    server = TmuxServer(socket_name, env)
    await server.run(
        "-f",
        "/dev/null",
        "set-option",
        "-g",
        "default-shell",
        _DEFAULT_SHELL,
        ";",
        "new-session",
        "-d",
        "-s",
        _SESSION_NAME,
        "-x",
        _SESSION_WIDTH,
        "-y",
        _SESSION_HEIGHT,
    )
    try:
        yield server
    finally:
        socket_path = await server.socket_path()
        await server.run("kill-server", check=False)
        socket_path.unlink(missing_ok=True)
