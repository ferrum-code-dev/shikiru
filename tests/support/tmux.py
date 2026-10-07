"""An isolated tmux server for tests, on a private socket."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import ProcessResult, SpawnedTerminalProcess, SubprocessRunner
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_SESSION_NAME = "test"
_SESSION_WIDTH = "200"
TMUX_COMMAND = "tmux"
SOCKET_FLAG = "-L"
CONFIG_FLAG = "-f"
_SESSION_HEIGHT = "50"
_DEFAULT_SHELL = "/bin/sh"
_TMUX_INHERITED_ENV_VARS = ("TMUX", "TMUX_PANE")
_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0
_CLIENT_ATTACH_TIMEOUT_SECONDS = 20.0
_CLIENT_TTY_FORMAT = "#{client_tty}"
_TERMINAL_TYPE = "xterm-256color"
_NULL_CONFIG_FILE = "/dev/null"
_ATTACH_SESSION_COMMAND = "attach-session"
_TARGET_FLAG = "-t"


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


@dataclass(slots=True)
class AttachedTmuxClient:
    """A real attached client hosted by a private PTY."""

    process: SpawnedTerminalProcess

    async def close(self) -> None:
        """Stop the client process and close its private terminal."""
        await self.process.stop(grace=_TMUX_COMMAND_TIMEOUT_SECONDS)


@asynccontextmanager
async def attach_tmux_client(server: TmuxServer, target: str) -> AsyncGenerator[AttachedTmuxClient]:
    """Attach one real tmux client to `target` using a private PTY."""
    process = await server.runner.spawn_terminal(
        [
            TMUX_COMMAND,
            SOCKET_FLAG,
            server.socket_name,
            CONFIG_FLAG,
            _NULL_CONFIG_FILE,
            _ATTACH_SESSION_COMMAND,
            _TARGET_FLAG,
            target,
        ],
        env={**server.env, "TERM": _TERMINAL_TYPE},
    )
    client = AttachedTmuxClient(process)
    try:

        async def attached() -> bool:
            result = await server.run(
                "list-clients", "-t", target, "-F", _CLIENT_TTY_FORMAT, check=False
            )
            return result.returncode == 0 and bool(result.stdout.strip())

        await wait_until(attached, timeout=_CLIENT_ATTACH_TIMEOUT_SECONDS)
        yield client
    finally:
        await client.close()


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _TMUX_INHERITED_ENV_VARS}


@pytest.fixture
async def tmux_server() -> AsyncGenerator[TmuxServer]:
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
