"""The only subprocess gateway in Shikiru: argv lists, timeouts, typed errors, async."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, cast

import psutil

from shikiru.errors import (
    CommandFailed,
    CommandTimeoutError,
    DetachedProcessError,
    ProcessReplacementError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

TURN_STOP_GRACE = 10.0
_STDERR_READ_SIZE = 65536
_STDOUT_READ_SIZE = 65536
_STDERR_TAIL_CHARS = 4000


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """The outcome of one process run through `SubprocessRunner`."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """A process id with its start time, which together never match a reused pid."""

    pid: int
    started_at: float


class SpawnedProcess:
    """A long-running child process with closed stdin and streamed stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._stdout_read = False
        self._stop_lock = asyncio.Lock()
        self._stderr = ""
        self._stderr_bytes = b""

    @property
    def pid(self) -> int:
        """Return this child process's operating system id."""
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        """Return the exit status when this process has exited."""
        return self._process.returncode

    @property
    def stderr(self) -> str:
        """Return all stderr captured after `wait()` completes."""
        return self._stderr

    @property
    def stderr_bytes(self) -> bytes:
        """Return the exact stderr bytes captured after `wait()` completes."""
        return self._stderr_bytes

    @property
    def stderr_tail(self) -> str:
        """Return a bounded stderr tail for failure messages."""
        return self._stderr[-_STDERR_TAIL_CHARS:]

    async def stdout_lines(self) -> AsyncIterator[bytes]:
        """Yield stdout lines as original bytes, including line endings, exactly once."""
        if self._stdout_read:
            message = "stdout_lines() can only be consumed once"
            raise RuntimeError(message)
        self._stdout_read = True
        if self._process.stdout is None:
            message = "spawned process has no stdout pipe"
            raise RuntimeError(message)
        pending = bytearray()
        while chunk := await self._process.stdout.read(_STDOUT_READ_SIZE):
            pending.extend(chunk)
            while (newline_index := pending.find(b"\n")) >= 0:
                line_end = newline_index + 1
                yield bytes(pending[:line_end])
                del pending[:line_end]
        if pending:
            yield bytes(pending)

    async def wait(self) -> int:
        """Wait for process exit and finish capturing stderr."""
        returncode = await self._process.wait()
        stderr_bytes = await self._stderr_task
        self._stderr_bytes = stderr_bytes
        self._stderr = stderr_bytes.decode(errors="replace")
        return returncode

    async def stop(self, *, grace: float = TURN_STOP_GRACE) -> None:
        """Send SIGTERM, then SIGKILL after `grace`, and reap the child."""
        async with self._stop_lock:
            if self._process.returncode is None:
                with suppress(ProcessLookupError):
                    self._process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=grace)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        self._process.kill()
                    await self._process.wait()
            await self.wait()

    async def _read_stderr(self) -> bytes:
        if self._process.stderr is None:
            message = "spawned process has no stderr pipe"
            raise RuntimeError(message)
        chunks: list[bytes] = []
        while chunk := await self._process.stderr.read(_STDERR_READ_SIZE):
            chunks.append(chunk)
        return b"".join(chunks)


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

    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SpawnedProcess:
        """Start `argv` with stdin closed and stdout and stderr captured as streams."""
        process = await asyncio.create_subprocess_exec(
            *tuple(argv),
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return SpawnedProcess(process)

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

    def identify(self, pid: int) -> ProcessIdentity | None:
        """Return the identity of process `pid`, or `None` when no inspectable process runs.

        An exited (or zombie) process has no identity, and neither has one the caller may not
        inspect, since every process this engine starts is its own.
        """
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            return ProcessIdentity(pid, process.create_time())
        except psutil.NoSuchProcess, psutil.AccessDenied:
            return None

    def is_running(self, identity: ProcessIdentity) -> bool:
        """Return whether the very process `identity` names still runs."""
        return self.identify(identity.pid) == identity

    def send_signal(self, identity: ProcessIdentity, signum: signal.Signals) -> bool:
        """Send `signum` to `identity`'s process if it still runs; return whether it was sent."""
        if not self.is_running(identity):
            return False
        try:
            os.kill(identity.pid, signum)
        except ProcessLookupError:
            return False
        return True

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
