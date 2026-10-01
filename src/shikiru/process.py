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
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from pathlib import Path

TURN_STOP_GRACE = 10.0
STDERR_DRAIN_TIMEOUT = 2.0
STDOUT_DRAIN_TIMEOUT = 2.0
STDOUT_FILENO = 1
STDERR_FILENO = 2
_STDERR_READ_SIZE = 65536
_STDOUT_READ_SIZE = 65536
_STDERR_TAIL_CHARS = 4000
STDERR_KEPT_BYTES = 1024 * 1024
START_TIME_TOLERANCE_SECONDS = 10.0
_NO_SIGNAL = cast("signal.Signals", 0)
_SHELL = "/bin/sh"
_PATH_VARIABLE = "PATH"
_GATE_SCRIPT = 'IFS= read -r _ || exit 1; exec "$@" </dev/null'
_GATE_ARG0 = "shikiru-gate"
_GATE_RELEASE = b"\n"
_ABORT_GRACE = 0.0


def ignore_started(_pid: int) -> None:
    """Accept the id of a just-spawned process and do nothing with it."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """The outcome of one process run through `SubprocessRunner`."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _killpg(pgid: int, signum: signal.Signals) -> bool:
    """Signal process group `pgid`; return whether it was sent.

    A missing group, or on macOS a group of only zombies (a permission error), has nothing left
    to signal.
    """
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError, PermissionError:
        return False
    return True


async def _end_process(process: asyncio.subprocess.Process, *, grace: float | None) -> None:
    """End the process group `process` leads: SIGTERM and `grace` seconds, then SIGKILL.

    Without a `grace` the group is SIGKILLed at once. It is always SIGKILLed once its leader
    has exited, so no descendant outlives it.
    """
    if process.returncode is None and grace is not None:
        _killpg(process.pid, signal.SIGTERM)
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=grace)
    if process.returncode is None:
        _killpg(process.pid, signal.SIGKILL)
    await process.wait()
    _killpg(process.pid, signal.SIGKILL)


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """A process id with its start time, which together never match a reused pid.

    A process group whose leader had already exited when it was recorded has no start time.
    """

    pid: int
    started_at: float | None

    def matches(self, live: ProcessIdentity | None) -> bool:
        """Return whether `live` is this very process, allowing for a shifted start time.

        Start times derive from the boot time on Linux and move when the clock is stepped, so
        they match within `START_TIME_TOLERANCE_SECONDS`. A reused pid starts a process later
        than that, since no pid is reissued so quickly.
        """
        if live is None or live.pid != self.pid:
            return False
        if self.started_at is None or live.started_at is None:
            return self.started_at is live.started_at
        return abs(live.started_at - self.started_at) <= START_TIME_TOLERANCE_SECONDS


def identify_pid(pid: int) -> ProcessIdentity | None:
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


class SpawnedProcess:
    """A long-running child process with closed stdin and streamed stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._stderr_tail = bytearray()
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._stdout_read = False
        self._group_swept = False
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
        """Return the last `STDERR_KEPT_BYTES` of stderr captured after `wait()` completes."""
        return self._stderr

    @property
    def stderr_bytes(self) -> bytes:
        """Return the exact last `STDERR_KEPT_BYTES` of stderr after `wait()` completes."""
        return self._stderr_bytes

    @property
    def stderr_tail(self) -> str:
        """Return a bounded stderr tail for failure messages."""
        return self._stderr[-_STDERR_TAIL_CHARS:]

    async def stdout_lines(self) -> AsyncIterator[bytes]:
        """Yield stdout lines as original bytes, including line endings, exactly once.

        Once the leader has exited, `STDOUT_DRAIN_TIMEOUT` seconds later its process group is
        SIGKILLed, so a descendant holding the pipe cannot keep the stream open.
        """
        if self._stdout_read:
            message = "stdout_lines() can only be consumed once"
            raise RuntimeError(message)
        self._stdout_read = True
        if self._process.stdout is None:
            message = "spawned process has no stdout pipe"
            raise RuntimeError(message)
        pending = bytearray()
        sweeper = asyncio.create_task(self._sweep_group_after_exit())
        try:
            while chunk := await self._process.stdout.read(_STDOUT_READ_SIZE):
                pending.extend(chunk)
                while (newline_index := pending.find(b"\n")) >= 0:
                    line_end = newline_index + 1
                    yield bytes(pending[:line_end])
                    del pending[:line_end]
        finally:
            sweeper.cancel()
        if pending:
            yield bytes(pending)

    async def wait(self) -> int:
        """Wait for process exit, end its process group, and capture stderr within a bound."""
        returncode = await self._process.wait()
        self._sweep_group()
        _, pending = await asyncio.wait({self._stderr_task}, timeout=STDERR_DRAIN_TIMEOUT)
        for task in pending:
            task.cancel()
        self._stderr_bytes = bytes(self._stderr_tail)
        self._stderr = self._stderr_bytes.decode(errors="replace")
        return returncode

    def _sweep_group(self) -> None:
        if not self._group_swept:
            self._group_swept = True
            _killpg(self._process.pid, signal.SIGKILL)

    async def _sweep_group_after_exit(self) -> None:
        await self._process.wait()
        await asyncio.sleep(STDOUT_DRAIN_TIMEOUT)
        self._sweep_group()

    async def stop(self, *, grace: float = TURN_STOP_GRACE) -> None:
        """Signal the process group with SIGTERM, then SIGKILL after `grace`, and reap it."""
        async with self._stop_lock:
            await _end_process(self._process, grace=grace)
            await self.wait()

    async def _read_stderr(self) -> None:
        if self._process.stderr is None:
            message = "spawned process has no stderr pipe"
            raise RuntimeError(message)
        while chunk := await self._process.stderr.read(_STDERR_READ_SIZE):
            self._stderr_tail.extend(chunk)
            del self._stderr_tail[:-STDERR_KEPT_BYTES]


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
        stop_grace: float | None = None,
    ) -> ProcessResult:
        """Run `argv` and return its result, honouring `timeout`.

        On expiry or cancellation the process is SIGKILLed, or with a `stop_grace` given SIGTERMed
        and killed only when it outlasts that many seconds. The process leads its own process
        group, which is ended with it so no descendant (a hook, an MCP server) outlives the call.
        """
        argv_tuple = tuple(argv)
        process = await asyncio.create_subprocess_exec(
            *argv_tuple,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        input_bytes = input_text.encode() if input_text is not None else None
        try:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input_bytes), timeout=timeout
                )
            except TimeoutError:
                await self._end_and_wait(process, grace=stop_grace)
                raise CommandTimeoutError(argv_tuple, timeout) from None
            except asyncio.CancelledError:
                await self._end_and_wait(process, grace=stop_grace)
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
        on_started: Callable[[int], None] = ignore_started,
    ) -> SpawnedProcess:
        """Start `argv` with stdin closed and stdout and stderr captured as streams.

        The process leads its own session and does not run `argv` until `on_started` has
        returned, so the caller can durably record its id first. When the caller dies or
        `on_started` raises before that, `argv` never runs and the process exits or is killed.
        """
        argv_tuple = tuple(argv)
        search_path = (env if env is not None else os.environ).get(_PATH_VARIABLE)
        if shutil.which(argv_tuple[0], path=search_path) is None:
            raise FileNotFoundError(argv_tuple[0])
        gate_read, gate_write = os.pipe()
        try:
            process = await asyncio.create_subprocess_exec(
                _SHELL,
                "-c",
                _GATE_SCRIPT,
                _GATE_ARG0,
                *argv_tuple,
                cwd=cwd,
                env=env,
                stdin=gate_read,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        finally:
            os.close(gate_read)
        spawned = SpawnedProcess(process)
        try:
            on_started(process.pid)
            os.write(gate_write, _GATE_RELEASE)
        except BaseException:
            await spawned.stop(grace=_ABORT_GRACE)
            raise
        finally:
            os.close(gate_write)
        return spawned

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
                (os.POSIX_SPAWN_DUP2, null_fd, STDOUT_FILENO),
                (os.POSIX_SPAWN_DUP2, null_fd, STDERR_FILENO),
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
        """Return the identity of process `pid`, or `None` when no inspectable process runs."""
        return identify_pid(pid)

    def is_running(self, identity: ProcessIdentity) -> bool:
        """Return whether the very process `identity` names still runs."""
        return identity.matches(self.identify(identity.pid))

    def group_is_running(self, pgid: int) -> bool:
        """Return whether any process of group `pgid` still runs."""
        return _killpg(pgid, _NO_SIGNAL)

    def signal_group(self, identity: ProcessIdentity, signum: signal.Signals) -> bool:
        """Send `signum` to the process group `identity` leads; return whether it was sent.

        A group outlives its leader while any member runs, and no pid equal to a live group id is
        reused, so only a pid now held by a different process is refused.
        """
        live = self.identify(identity.pid)
        if live is not None and not identity.matches(live):
            return False
        return _killpg(identity.pid, signum)

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
    async def _end_and_wait(process: asyncio.subprocess.Process, *, grace: float | None) -> None:
        cleanup = asyncio.create_task(_end_process(process, grace=grace))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup

    @staticmethod
    def _close_transport(process: asyncio.subprocess.Process) -> None:
        transport = cast(
            "Any",
            process._transport,  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
        )
        transport.close()
