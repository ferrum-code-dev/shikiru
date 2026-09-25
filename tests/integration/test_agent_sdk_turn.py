from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import (
    AgentSignal,
    FailureKind,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
)
from shikiru.agents.sdk.turn import (
    StreamingTurn,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
)
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path


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
