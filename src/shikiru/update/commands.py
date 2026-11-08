"""The commands an update runs on this host, through `shikiru.process.SubprocessRunner`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from shikiru.errors import CommandTimeoutError
from shikiru.process import ProcessResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.process import SubprocessRunner

PATH_VARIABLE = "PATH"
VIRTUAL_ENV_VARIABLE = "VIRTUAL_ENV"
VENV_BIN_DIRECTORY = "bin"
TIMEOUT_EXIT_CODE = 124


class CommandRunner(Protocol):
    """Runs one argv list; a command still running after `timeout` seconds is a failed result."""

    async def run(self, argv: Sequence[str], *, timeout: float) -> ProcessResult: ...


def command_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Return `environ` with the virtual environment's `bin` removed from PATH.

    An update started from a checkout's virtual environment would otherwise run that checkout's
    `shikiru` instead of the installed one.
    """
    environment = dict(environ)
    virtual_env = environ.get(VIRTUAL_ENV_VARIABLE)
    if virtual_env and PATH_VARIABLE in environ:
        checkout_bin = Path(virtual_env, VENV_BIN_DIRECTORY)
        entries = environ[PATH_VARIABLE].split(os.pathsep)
        environment[PATH_VARIABLE] = os.pathsep.join(
            entry for entry in entries if Path(entry) != checkout_bin
        )
    return environment


@dataclass(frozen=True)
class HostCommands:
    """The `CommandRunner` of an update: commands run with `environment` as their environment."""

    environment: Mapping[str, str]
    runner: SubprocessRunner

    async def run(self, argv: Sequence[str], *, timeout: float) -> ProcessResult:
        """Run `argv`; a timed-out command returns a failed result naming the timeout."""
        try:
            return await self.runner.run(
                argv, timeout=timeout, env=command_environment(self.environment)
            )
        except CommandTimeoutError as error:
            return ProcessResult(
                argv=tuple(argv),
                returncode=TIMEOUT_EXIT_CODE,
                stdout="",
                stderr=str(error),
            )
