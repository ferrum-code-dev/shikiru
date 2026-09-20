"""The only subprocess gateway in Shikiru: argv lists, timeouts, typed errors, async."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, cast

from shikiru.errors import (
    CommandFailed,
    CommandTimeoutError,
    DetachedProcessError,
    ProcessReplacementError,
)

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
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input_bytes), timeout=timeout
                )
            except TimeoutError:
                await self._kill_and_wait(process)
                raise CommandTimeoutError(argv_tuple, timeout) from None
            except asyncio.CancelledError:
                await self._kill_and_wait(process)
                raise
        finally:
            self._close_transport(process)

        result = ProcessResult(
            argv=argv_tuple,
            returncode=cast("int", process.returncode),
            stdout=stdout_bytes.decode(),
            stderr=stderr_bytes.decode(),
        )
        if check and result.returncode != 0:
            raise CommandFailed(argv_tuple, result.returncode, result.stderr)
        return result

    def spawn_detached(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> int:
        """Start `argv` in a new session and return its process id without waiting."""
        argv_tuple = tuple(argv)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            file_actions = (
                (os.POSIX_SPAWN_DUP2, null_fd, 1),
                (os.POSIX_SPAWN_DUP2, null_fd, 2),
            )
            try:
                return os.posix_spawnp(
                    argv_tuple[0],
                    argv_tuple,
                    dict(env) if env is not None else dict(os.environ),
                    file_actions=file_actions,
                    setsid=True,
                )
            except OSError as error:
                raise DetachedProcessError(argv_tuple, error) from error
        finally:
            os.close(null_fd)

    def replace(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> NoReturn:
        """Replace this process with `argv`, resolving its executable on `PATH`."""
        argv_tuple = tuple(argv)
        executable = shutil.which(argv_tuple[0])
        if executable is None:
            error = FileNotFoundError(argv_tuple[0])
            raise ProcessReplacementError(argv_tuple, error) from error
        try:
            os.execve(  # noqa: S606
                executable,
                argv_tuple,
                dict(env) if env is not None else dict(os.environ),
            )
        except OSError as error:
            raise ProcessReplacementError(argv_tuple, error) from error

    @staticmethod
    async def _kill_and_wait(process: asyncio.subprocess.Process) -> None:
        cleanup = asyncio.create_task(SubprocessRunner._kill(process))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()

    @staticmethod
    def _close_transport(process: asyncio.subprocess.Process) -> None:
        transport = cast(
            "Any",
            process._transport,  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
        )
        transport.close()
