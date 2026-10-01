"""Process-per-turn plugin building blocks (04 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from shikiru.agents.contracts import (
    AgentSignal,
    FailureKind,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
)
from shikiru.private import append_private_bytes, make_private_dir

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping
    from pathlib import Path

    from shikiru.process import SpawnedProcess, SubprocessRunner

STREAM_FILENAME_SUFFIX = ".stream.jsonl"
STDERR_FILENAME_SUFFIX = ".stderr.log"
_LINE_ENDINGS = "\r\n"
EMPTY_OUTPUT = "(empty)"


def stream_log_path(turn_dir: Path, role: str) -> Path:
    """Return where a turn keeps `role`'s raw stdout stream."""
    return turn_dir / f"{role}{STREAM_FILENAME_SUFFIX}"


def stderr_log_path(turn_dir: Path, role: str) -> Path:
    """Return where a turn keeps `role`'s captured stderr."""
    return turn_dir / f"{role}{STDERR_FILENAME_SUFFIX}"


def _append_raw(path: Path, content: bytes) -> None:
    make_private_dir(path.parent)
    append_private_bytes(path, content)


class TurnParser(Protocol):
    """Maps one agent's stdout lines and process exit into normalized signals."""

    def feed(self, line: str) -> list[AgentSignal]:
        """Map one newline-free stdout line to zero or more signals."""
        ...

    def finish(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        """Map process exit and captured stderr to final signals."""
        ...


@dataclass(frozen=True)
class TurnProcess:
    """One process in a plugin's compact, prompt or retry sequence."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    parser: TurnParser


@dataclass(frozen=True)
class TurnProcessResult:
    """The parsed outcome of one process in a turn sequence."""

    signals: tuple[AgentSignal, ...]
    exit_code: int
    stderr: str


class TurnProcessSequence(Protocol):
    """Builds the next process in one turn, using the previous process outcome."""

    def start(self, spec: TurnSpec) -> TurnProcess:
        """Build the first process, including pre-turn compaction when needed."""
        ...

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        """Build a compact or retry process, or finish the turn with `None`."""
        ...


class StreamingTurn:
    """Runs a plugin's process sequence and validates the turn's start and final signal."""

    def __init__(
        self,
        spec: TurnSpec,
        runner: SubprocessRunner,
        sequence: TurnProcessSequence,
        first_process: TurnProcess,
        process: SpawnedProcess,
    ) -> None:
        self._spec = spec
        self._runner = runner
        self._sequence = sequence
        self._current_process_spec = first_process
        self._process = process
        self._signal_reader_started = False
        self._session_id: str | None = None
        self._session_started = False
        self._process_session_started = False
        self._signal_count = 0

    @classmethod
    async def start(
        cls, spec: TurnSpec, runner: SubprocessRunner, sequence: TurnProcessSequence
    ) -> StreamingTurn:
        """Create the first child process and return a live `Turn`."""
        first_process = sequence.start(spec)
        process = await runner.spawn(
            first_process.argv,
            cwd=first_process.cwd,
            env=first_process.env,
            on_started=spec.on_process,
        )
        return cls(spec, runner, sequence, first_process, process)

    @property
    def pid(self) -> int:
        """Return the operating system id for the process running now."""
        return self._process.pid

    async def signals(self) -> AsyncIterator[AgentSignal]:
        """Yield normalized signals once, preserving each process's raw output in the turn dir."""
        if self._signal_reader_started:
            message = "signals() can only be consumed once"
            raise RuntimeError(message)
        self._signal_reader_started = True
        process = self._process
        try:
            while True:
                self._process_session_started = False
                execution = _ProcessExecution(
                    self._current_process_spec.parser,
                    self._stream_path,
                    self._stderr_path,
                    self._normalize,
                    process,
                )
                async for signal in execution.signals():
                    yield signal
                outcome = execution.outcome
                if execution.protocol_problem is not None:
                    yield self._unknown_failure(
                        execution.protocol_problem, outcome.exit_code, process.stderr_tail
                    )
                    return
                if not self._session_started:
                    yield self._unknown_failure(
                        "agent stream omitted SessionStarted",
                        outcome.exit_code,
                        process.stderr_tail,
                    )
                    return
                next_process = self._sequence.advance(self._spec, outcome)
                if next_process is None:
                    terminal = execution.terminal or self._unknown_failure(
                        "agent process ended without a turn result",
                        outcome.exit_code,
                        process.stderr_tail,
                    )
                    yield terminal
                    return
                process = await self._runner.spawn(
                    next_process.argv,
                    cwd=next_process.cwd,
                    env=next_process.env,
                    on_started=self._spec.on_process,
                )
                self._current_process_spec = next_process
                self._process = process
        finally:
            if process.returncode is None:
                await process.stop()

    async def stop(self) -> None:
        """Stop the process running now, allowing a later turn to resume its session."""
        await self._process.stop()

    @classmethod
    def _unknown_failure(cls, reason: str, exit_code: int, stderr_tail: str) -> TurnFailed:
        return TurnFailed(
            kind=FailureKind.UNKNOWN,
            message=cls._unknown_message(reason, exit_code, stderr_tail),
            resets_at=None,
        )

    def _normalize(self, signal: AgentSignal) -> tuple[AgentSignal | None, str | None]:
        if not isinstance(signal, SessionStarted):
            return self._normalize_other(signal)
        if self._process_session_started:
            return None, "process emitted more than one SessionStarted"
        self._process_session_started = True
        return self._normalize_session_start(signal)

    def _normalize_session_start(
        self, signal: SessionStarted
    ) -> tuple[AgentSignal | None, str | None]:
        if self._session_started:
            if signal.session_id != self._session_id:
                return None, "turn emitted more than one session id"
            return None, None
        if self._signal_count:
            return None, "agent stream emitted a signal before SessionStarted"
        if not signal.session_id.strip():
            return None, "agent stream emitted an empty session id"
        if self._spec.session_id is not None and signal.session_id != self._spec.session_id:
            return None, "resumed turn emitted a different session id"
        self._session_id = signal.session_id
        self._session_started = True
        self._signal_count += 1
        return signal, None

    def _normalize_other(self, signal: AgentSignal) -> tuple[AgentSignal | None, str | None]:
        if not self._session_started:
            self._signal_count += 1
            return None, "turn emitted a signal before SessionStarted"
        self._signal_count += 1
        return signal, None

    @staticmethod
    def _unknown_message(reason: str, exit_code: int, stderr_tail: str) -> str:
        tail = stderr_tail or EMPTY_OUTPUT
        return f"{reason}; process exited with status {exit_code}; stderr tail: {tail}"

    @property
    def _stream_path(self) -> Path:
        return stream_log_path(self._spec.turn_dir, self._spec.role)

    @property
    def _stderr_path(self) -> Path:
        return stderr_log_path(self._spec.turn_dir, self._spec.role)


class _ProcessExecution:
    def __init__(
        self,
        parser: TurnParser,
        stream_path: Path,
        stderr_path: Path,
        normalize: Callable[[AgentSignal], tuple[AgentSignal | None, str | None]],
        process: SpawnedProcess,
    ) -> None:
        self._parser = parser
        self._stream_path = stream_path
        self._stderr_path = stderr_path
        self._normalize = normalize
        self._process = process
        self._signals: list[AgentSignal] = []
        self.terminal: TurnEnded | TurnFailed | None = None
        self.protocol_problem: str | None = None
        self._outcome: TurnProcessResult | None = None

    @property
    def outcome(self) -> TurnProcessResult:
        if self._outcome is None:
            message = "process signals must be consumed before reading the outcome"
            raise RuntimeError(message)
        return self._outcome

    async def signals(self) -> AsyncIterator[AgentSignal]:
        async for raw_line in self._process.stdout_lines():
            _append_raw(self._stream_path, raw_line)
            line = raw_line.decode(errors="replace").rstrip(_LINE_ENDINGS)
            async for signal in self._parsed(self._parser.feed(line)):
                yield signal
        exit_code = await self._process.wait()
        _append_raw(self._stderr_path, self._process.stderr_bytes)
        async for signal in self._parsed(self._parser.finish(exit_code, self._process.stderr)):
            yield signal
        self._outcome = TurnProcessResult(tuple(self._signals), exit_code, self._process.stderr)

    async def _parsed(self, signals: list[AgentSignal]) -> AsyncIterator[AgentSignal]:
        for signal in signals:
            if self.terminal is not None:
                self.protocol_problem = self.protocol_problem or (
                    "process emitted a signal after its terminal signal"
                )
                self._signals.append(signal)
                continue
            if isinstance(signal, (TurnEnded, TurnFailed)):
                self.terminal = signal
            else:
                normalized, problem = self._normalize(signal)
                self.protocol_problem = self.protocol_problem or problem
                if normalized is not None and self.protocol_problem is None:
                    yield normalized
            self._signals.append(signal)
