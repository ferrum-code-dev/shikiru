"""An isolated tmux server for tests, on a private socket."""

from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

_SESSION_NAME = "test"
_SESSION_WIDTH = "200"
_SESSION_HEIGHT = "50"
_DEFAULT_SHELL = "/bin/sh"
_TMUX_INHERITED_ENV_VARS = ("TMUX", "TMUX_PANE")


@dataclass(frozen=True, slots=True)
class TmuxServer:
    """A private tmux server on its own socket, isolated from the operator's own tmux."""

    socket_name: str
    env: dict[str, str]

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a tmux command scoped to this server's socket."""
        return subprocess.run(
            ["tmux", "-L", self.socket_name, *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
        )


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _TMUX_INHERITED_ENV_VARS}


@pytest.fixture
def tmux_server() -> Iterator[TmuxServer]:
    """Start a private, isolated tmux server and kill only that server on teardown."""
    socket_name = f"shikiru-test-{uuid.uuid4().hex}"
    env = _clean_env()
    server = TmuxServer(socket_name, env)
    server.run(
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
        server.run("kill-server", check=False)
