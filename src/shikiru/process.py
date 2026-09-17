"""The only subprocess gateway in Shikiru: argv lists, timeouts, typed errors, async."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.errors import CommandFailed, CommandTimeoutError

if TYPE_CHECKING:
    from pathlib import Path

_PROCESS_KILL_GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """The outcome of one process run through `SubprocessRunner`."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    """Runs argv lists as subprocesses, never through a shell."""

    async def run(
        self,
        argv: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> ProcessResult:
        """Run `argv` and return its result, honouring `timeout` and killing it on expiry."""
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        input_bytes = input_text.encode() if input_text is not None else None
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input_bytes), timeout=timeout
            )
        except TimeoutError:
            await self._kill(process)
            raise CommandTimeoutError(argv, timeout) from None

        result = ProcessResult(
            argv=argv,
            returncode=process.returncode or 0,
            stdout=stdout_bytes.decode(),
            stderr=stderr_bytes.decode(),
        )
        if check and result.returncode != 0:
            raise CommandFailed(argv, result.returncode, result.stderr)
        return result

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_PROCESS_KILL_GRACE_SECONDS)
