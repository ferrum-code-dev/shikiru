"""The only subprocess gateway in Shikiru: argv lists, timeouts, typed errors, async."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shikiru.errors import CommandFailed, CommandTimeoutError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """The outcome of one process run through `SubprocessRunner`."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    """Runs argv lists as subprocesses, never through a shell."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> ProcessResult:
        """Run `argv` and return its result, honouring `timeout` and killing it on expiry."""
        argv_tuple = tuple(argv)
        process = await asyncio.create_subprocess_exec(
            *argv_tuple,
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
            raise CommandTimeoutError(argv_tuple, timeout) from None

        result = ProcessResult(
            argv=argv_tuple,
            returncode=cast("int", process.returncode),
            stdout=stdout_bytes.decode(),
            stderr=stderr_bytes.decode(),
        )
        if check and result.returncode != 0:
            raise CommandFailed(argv_tuple, result.returncode, result.stderr)
        return result

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        process.kill()
        await process.wait()
