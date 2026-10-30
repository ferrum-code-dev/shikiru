from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shikiru.agents.contracts import (
    AgentSignal,
    FailureKind,
    Message,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import (
    StreamingTurn,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
    stream_log_path,
)
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from shikiru.plugins.agents.codex.turn import CodexTurnSequence
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

_CODEX_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "codex"
_CLAUDE_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_CONTEXT_FAILURE_FIXTURE = "turn_context_failure.jsonl"
_TEST_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TURN_STOP_TIMEOUT_SECONDS = 10


@dataclass
class _Parser:
    def feed(self, line: str) -> list[AgentSignal]:
        if line == "�":
            return []
        entry = json.loads(line)
        if entry["event"] == "session":
            return [SessionStarted(entry["session_id"])]
        return [TurnEnded("finished")]

    def finish(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        del exit_code, stderr
        return []


@dataclass
class _SingleProcessSequence(TurnProcessSequence):
    process: TurnProcess

    def start(self, spec: TurnSpec) -> TurnProcess:
        del spec
        return self.process

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        del spec, previous
        return None


def _spec(tmp_path: Path, *, session_id: str | None) -> TurnSpec:
    return TurnSpec(
        rung=Rung(agent="test", model="test"),
        role="implementer",
        prompt="test",
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={},
        session_id=session_id,
        usage=None,
    )


def _process(tmp_path: Path, program: str) -> TurnProcess:
    return TurnProcess(
        argv=(sys.executable, "-c", program),
        cwd=tmp_path,
        env={},
        parser=_Parser(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream", "reason"),
    [
        (b'{"event":"ended"}\n', "omitted SessionStarted"),
        (
            (b'{"event":"session","session_id":"other-session"}\n{"event":"ended"}\n'),
            "different session id",
        ),
    ],
)
async def test_resumed_turn_rejects_missing_or_different_session_start(
    tmp_path: Path, stream: bytes, reason: str
) -> None:
    program = f"import sys; sys.stdout.buffer.write({stream!r}); sys.stderr.write('diagnostic')"
    spec = _spec(tmp_path, session_id="expected-session")
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_process(tmp_path, program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert not any(isinstance(signal, SessionStarted) for signal in signals)
    failure = signals[-1]
    assert isinstance(failure, TurnFailed)
    assert failure.kind is FailureKind.UNKNOWN
    assert reason in failure.message
    assert "diagnostic" in failure.message


@pytest.mark.asyncio
async def test_turn_rejects_duplicate_session_started_in_one_process(tmp_path: Path) -> None:
    output = (
        b'{"event":"session","session_id":"session-a"}\n'
        b'{"event":"session","session_id":"session-a"}\n'
        b'{"event":"ended"}\n'
    )
    program = f"import sys; sys.stdout.buffer.write({output!r})"
    spec = _spec(tmp_path, session_id=None)
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_process(tmp_path, program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert signals[0] == SessionStarted("session-a")
    assert len(signals) == 2
    failure = signals[-1]
    assert isinstance(failure, TurnFailed)
    assert failure.kind is FailureKind.UNKNOWN
    assert "more than one SessionStarted" in failure.message


@pytest.mark.asyncio
async def test_turn_copies_raw_stdout_and_stderr_bytes_without_decoding_changes(
    tmp_path: Path,
) -> None:
    stdout = b'{"event":"session","session_id":"session-a"}\n\xff\n{"event":"ended"}\n'
    stderr = b"diagnostic\xfe\n"
    program = (
        f"import sys; sys.stdout.buffer.write({stdout!r}); sys.stderr.buffer.write({stderr!r})"
    )
    spec = _spec(tmp_path, session_id=None)
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_process(tmp_path, program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert signals == [SessionStarted("session-a"), TurnEnded("finished")]
    assert (spec.turn_dir / "implementer.stream.jsonl").read_bytes() == stdout
    assert (spec.turn_dir / "implementer.stderr.log").read_bytes() == stderr


@pytest.mark.asyncio
async def test_process_exit_without_a_result_yields_unknown_with_exit_and_stderr(
    tmp_path: Path,
) -> None:
    output = b'{"event":"session","session_id":"session-a"}\n'
    diagnostic = "agent stopped before a result"
    program = (
        f"import sys; sys.stdout.buffer.write({output!r}); "
        f"sys.stderr.write({diagnostic!r}); sys.exit(7)"
    )
    spec = _spec(tmp_path, session_id=None)
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_process(tmp_path, program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert signals[0] == SessionStarted("session-a")
    assert isinstance(signals[-1], TurnFailed)
    assert signals[-1].kind is FailureKind.UNKNOWN
    assert "status 7" in signals[-1].message
    assert diagnostic in signals[-1].message


@pytest.mark.asyncio
async def test_claude_activity_after_task_notification_reaches_the_turn_consumer(
    tmp_path: Path,
) -> None:
    stream = (_CLAUDE_FIXTURES / "turn_background_result_then_activity.jsonl").read_bytes()
    program = f"import sys; sys.stdout.buffer.write({stream!r}); sys.stdout.flush()"
    spec = replace(_spec(tmp_path, session_id=None), rung=Rung("claude", "haiku"))
    clock = FakeClock(_TEST_NOW)
    parser = ClaudeTurnParser(
        session_id="session-background-redacted",
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    process = TurnProcess((sys.executable, "-c", program), tmp_path, {}, parser)
    turn = await StreamingTurn.start(spec, SubprocessRunner(), _SingleProcessSequence(process))

    signals = [signal async for signal in turn.signals()]

    assert signals[0] == SessionStarted("session-background-redacted")
    assert Message("Verification is continuing.") in signals
    assert Message("Implementation is complete.") in signals
    assert len([signal for signal in signals if isinstance(signal, TurnEnded)]) == 1
    assert len([signal for signal in signals if isinstance(signal, TurnFailed)]) == 0
    assert (spec.turn_dir / "implementer.stream.jsonl").read_bytes() == stream


@dataclass
class _RetrySequence(TurnProcessSequence):
    first: TurnProcess
    retry: TurnProcess
    advanced: bool = False

    def start(self, spec: TurnSpec) -> TurnProcess:
        del spec
        return self.first

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        del spec, previous
        if self.advanced:
            return None
        self.advanced = True
        return self.retry


@pytest.mark.asyncio
async def test_every_process_of_a_turn_is_reported_when_it_is_spawned_including_a_retry(
    tmp_path: Path,
) -> None:
    session = b'{"event":"session","session_id":"session-a"}\n'
    first_program = f"import sys; sys.stdout.buffer.write({session!r})"
    retry_program = f"import sys; sys.stdout.buffer.write({session + b'{"event":"ended"}\n'!r})"
    reported: list[int] = []
    spec = replace(_spec(tmp_path, session_id=None), on_process=reported.append)
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _RetrySequence(_process(tmp_path, first_program), _process(tmp_path, retry_program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert signals[-1] == TurnEnded("finished")
    assert len(reported) == 2
    assert len(set(reported)) == 2


@pytest.mark.asyncio
async def test_stopping_a_context_failure_does_not_start_its_retry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    turn_dir = tmp_path / "turn"
    home = tmp_path / "codex-home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    block_fifo = tmp_path / "blocked.fifo"
    os.mkfifo(block_fifo)
    ready = tmp_path / "ready"
    calls = tmp_path / "calls"
    fixture = _CODEX_FIXTURES / _CONTEXT_FAILURE_FIXTURE
    executable = fake_bin / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'printf x >> "$CALLS_FILE"\n'
        'if [ ! -e "$READY_FILE" ]; then\n'
        '  cat "$FAILURE_FIXTURE"\n'
        '  : > "$READY_FILE"\n'
        '  cat "$BLOCK_FIFO" >/dev/null\n'
        "else\n"
        "  printf '%s\\n' \\\n"
        '    \'{"type":"thread.started","thread_id":"session-recovery"}\' \\\n'
        '    \'{"type":"item.completed","item":'
        '{"type":"agent_message","text":"retry began"}}\' \\\n'
        '    \'{"type":"turn.completed"}\'\n'
        "fi\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BLOCK_FIFO": str(block_fifo),
        "CALLS_FILE": str(calls),
        "FAILURE_FIXTURE": str(fixture),
        "READY_FILE": str(ready),
    }
    spec = TurnSpec(
        rung=Rung(agent="codex", model="test"),
        role="implementer",
        prompt="continue the task",
        cwd=repo,
        run_dir=run_dir,
        turn_dir=turn_dir,
        add_dirs=(),
        env=env,
        session_id=None,
        usage=None,
    )
    clock = FakeClock(_TEST_NOW)
    sequence = CodexTurnSequence(
        spec,
        args=(),
        environment=lambda _spec: env,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        home=home,
        git_dirs=(),
        compact_at_pct=80,
    )
    turn = await StreamingTurn.start(spec, SubprocessRunner(), sequence)
    signals = turn.signals()

    async def next_signal() -> AgentSignal:
        return await anext(signals)

    first = await asyncio.wait_for(anext(signals), timeout=_TURN_STOP_TIMEOUT_SECONDS)
    pending = asyncio.create_task(next_signal())
    await wait_until(
        lambda: (
            ready.is_file()
            and stream_log_path(turn_dir, spec.role).is_file()
            and "turn.failed" in stream_log_path(turn_dir, spec.role).read_text(encoding="utf-8")
        ),
        timeout=_TURN_STOP_TIMEOUT_SECONDS,
    )
    await turn.stop()
    remaining: list[AgentSignal] = [first]
    async with asyncio.timeout(_TURN_STOP_TIMEOUT_SECONDS):
        while True:
            try:
                remaining.append(await pending)
            except StopAsyncIteration:
                break
            pending = asyncio.create_task(next_signal())

    assert first == SessionStarted("session-recovery")
    assert any(isinstance(signal, TurnFailed) for signal in remaining)
    assert not any(signal == Message("retry began") for signal in remaining)
    assert calls.read_text(encoding="utf-8") == "x"
