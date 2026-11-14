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
    Compacted,
    FailureKind,
    Message,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import (
    StreamingTurn,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
    stream_log_path,
)
from shikiru.plugins.agents.claude.turn import CONTINUE_PROMPT, ClaudeTurnParser, ClaudeTurnSequence
from shikiru.plugins.agents.codex.turn import CodexTurnParser, CodexTurnSequence
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

_CODEX_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "codex"
_CLAUDE_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_CONTEXT_FAILURE_FIXTURE = "turn_context_failure.jsonl"
_TEST_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TURN_STOP_TIMEOUT_SECONDS = 10
_CODEX_SESSION_ID = "session-a"
_CODEX_SESSION_STARTED = f'{{"type":"thread.started","thread_id":"{_CODEX_SESSION_ID}"}}\n'.encode()
_CODEX_STOP_DIAGNOSTIC = "Reading additional input from stdin..."
_CODEX_MESSAGE_BEFORE_SESSION = (
    b'{"type":"item.completed","item":{"type":"agent_message","text":"early"}}\n'
)
_CODEX_FAILURE_BEFORE_SESSION = (
    b'{"type":"turn.failed","error":{"message":"provider rejected turn"}}\n'
)


@dataclass
class _Parser:
    def feed(self, line: str) -> list[AgentSignal]:
        if line == "�":
            return []
        entry = json.loads(line)
        if entry["event"] == "session":
            return [SessionStarted(entry["session_id"])]
        return [TurnEnded("finished")]

    def finish(
        self, exit_code: int, stderr: str, *, interrupted: bool = False
    ) -> list[AgentSignal]:
        del exit_code, stderr
        del interrupted
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


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _process(tmp_path: Path, program: str) -> TurnProcess:
    return TurnProcess(
        argv=(sys.executable, "-c", program),
        cwd=tmp_path,
        env={},
        parser=_Parser(),
    )


def _codex_process(
    tmp_path: Path,
    program: str,
    *,
    session_id: str | None = None,
    home: Path | None = None,
) -> TurnProcess:
    clock = FakeClock(_TEST_NOW)
    parser = CodexTurnParser(
        session_id=session_id,
        initial_usage=None,
        home=tmp_path / "codex-home" if home is None else home,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    return TurnProcess(
        argv=(sys.executable, "-c", program),
        cwd=tmp_path,
        env={},
        parser=parser,
    )


def _write_codex_rollout(home: Path, session_id: str) -> None:
    rollout = (
        home / "sessions" / "2030" / "01" / "01" / f"rollout-2030-01-01T00-00-00-{session_id}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_bytes((_CODEX_FIXTURES / "rollout_first.jsonl").read_bytes())


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
async def test_codex_intentional_stop_does_not_report_a_missing_result(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "codex-ready"
    program = (
        "import pathlib, signal, sys; "
        f"sys.stdout.buffer.write({_CODEX_SESSION_STARTED!r}); sys.stdout.flush(); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        f"sys.stderr.write({_CODEX_STOP_DIAGNOSTIC!r}); sys.stderr.flush(); "
        "signal.pause()"
    )
    spec = replace(_spec(tmp_path, session_id=None), rung=Rung("codex", "test"))
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_codex_process(tmp_path, program)),
    )
    signals = turn.signals()

    try:
        assert await anext(signals) == SessionStarted("session-a")
        await wait_until(ready.is_file, _TURN_STOP_TIMEOUT_SECONDS)
        await turn.stop()
        remaining = [signal async for signal in signals]
    finally:
        await turn.stop()

    assert not any(isinstance(signal, TurnFailed) for signal in remaining), remaining


@pytest.mark.asyncio
async def test_codex_unexpected_exit_without_a_result_remains_visible(tmp_path: Path) -> None:
    program = (
        f"import sys; sys.stdout.buffer.write({_CODEX_SESSION_STARTED!r}); "
        f"sys.stderr.write({_CODEX_STOP_DIAGNOSTIC!r}); sys.exit(7)"
    )
    spec = replace(_spec(tmp_path, session_id=None), rung=Rung("codex", "test"))
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_codex_process(tmp_path, program)),
    )

    signals = [signal async for signal in turn.signals()]

    assert signals[0] == SessionStarted("session-a")
    assert isinstance(signals[-1], TurnFailed)
    assert signals[-1].kind is FailureKind.UNKNOWN
    assert "without a structured result" in signals[-1].message
    assert "status 7" in signals[-1].message
    assert _CODEX_STOP_DIAGNOSTIC in signals[-1].message


@pytest.mark.asyncio
async def test_stopping_a_resumed_start_with_rollout_evidence_keeps_session_available(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "resumed-start-ready"
    program = f"import pathlib, signal; pathlib.Path({str(ready)!r}).touch(); signal.pause()"
    home = tmp_path / "codex-home"
    _write_codex_rollout(home, _CODEX_SESSION_ID)
    spec = replace(_spec(tmp_path, session_id=_CODEX_SESSION_ID), rung=Rung("codex", "test"))
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(
            _codex_process(tmp_path, program, session_id=_CODEX_SESSION_ID, home=home)
        ),
    )
    signals = turn.signals()

    try:
        await wait_until(ready.is_file, _TURN_STOP_TIMEOUT_SECONDS)
        await turn.stop()
        assert [signal async for signal in signals] == []
    finally:
        await turn.stop()

    resumed_spec = replace(spec, turn_dir=tmp_path / "resumed-turn")
    resumed_program = f"import sys; sys.stdout.buffer.write({_CODEX_SESSION_STARTED!r}); "
    resumed_program += 'sys.stdout.buffer.write(b\'{"type":"turn.completed"}\\n\')'
    resumed = await StreamingTurn.start(
        resumed_spec,
        SubprocessRunner(),
        _SingleProcessSequence(
            _codex_process(
                tmp_path,
                resumed_program,
                session_id=_CODEX_SESSION_ID,
                home=home,
            )
        ),
    )

    resumed_signals = [signal async for signal in resumed.signals()]
    assert resumed_signals[0] == SessionStarted(_CODEX_SESSION_ID)
    assert resumed_signals[-1] == TurnEnded("")


@pytest.mark.asyncio
async def test_interrupted_resumed_start_preserves_explicit_codex_failure(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "resumed-failure-ready"
    home = tmp_path / "codex-home"
    _write_codex_rollout(home, _CODEX_SESSION_ID)
    program = (
        "import pathlib, signal, sys; "
        f"sys.stdout.buffer.write({_CODEX_FAILURE_BEFORE_SESSION!r}); sys.stdout.flush(); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "signal.pause()"
    )
    spec = replace(_spec(tmp_path, session_id=_CODEX_SESSION_ID), rung=Rung("codex", "test"))
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(
            _codex_process(tmp_path, program, session_id=_CODEX_SESSION_ID, home=home)
        ),
    )
    signals = turn.signals()

    try:
        await wait_until(ready.is_file, _TURN_STOP_TIMEOUT_SECONDS)
        await turn.stop()
        remaining = [signal async for signal in signals]
    finally:
        await turn.stop()

    assert len(remaining) == 1
    assert isinstance(remaining[0], TurnFailed)
    assert "provider rejected turn" in remaining[0].message


@pytest.mark.asyncio
async def test_intentional_stop_preserves_protocol_failure_before_session(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "invalid-start-ready"
    program = (
        "import pathlib, signal, sys; "
        f"sys.stdout.buffer.write({_CODEX_MESSAGE_BEFORE_SESSION!r}); sys.stdout.flush(); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "signal.pause()"
    )
    spec = replace(_spec(tmp_path, session_id="session-a"), rung=Rung("codex", "test"))
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_codex_process(tmp_path, program)),
    )
    signals = turn.signals()

    try:
        await wait_until(ready.is_file, _TURN_STOP_TIMEOUT_SECONDS)
        await turn.stop()
        remaining = [signal async for signal in signals]
    finally:
        await turn.stop()

    assert len(remaining) == 1
    assert isinstance(remaining[0], TurnFailed)
    assert remaining[0].kind is FailureKind.UNKNOWN
    assert "signal before SessionStarted" in remaining[0].message


@pytest.mark.asyncio
async def test_stop_after_process_exit_keeps_missing_result_failure(tmp_path: Path) -> None:
    ready = tmp_path / "unexpected-exit-ready"
    session_output = b'{"event":"session","session_id":"session-a"}\n'
    diagnostic = "agent stopped before a result"
    program = (
        "import pathlib, sys; "
        f"sys.stdout.buffer.write({session_output!r}); sys.stdout.flush(); "
        f"sys.stderr.write({diagnostic!r}); sys.stderr.flush(); "
        f"pathlib.Path({str(ready)!r}).touch(); sys.exit(7)"
    )
    spec = _spec(tmp_path, session_id=None)
    turn = await StreamingTurn.start(
        spec,
        SubprocessRunner(),
        _SingleProcessSequence(_process(tmp_path, program)),
    )
    signals = turn.signals()

    try:
        assert await anext(signals) == SessionStarted("session-a")
        await wait_until(lambda: not _process_exists(turn.pid), _TURN_STOP_TIMEOUT_SECONDS)
        await turn.stop()
        remaining = [signal async for signal in signals]
    finally:
        await turn.stop()

    assert len(remaining) == 1
    assert isinstance(remaining[0], TurnFailed)
    assert remaining[0].kind is FailureKind.UNKNOWN
    assert "without a turn result" in remaining[0].message
    assert "status 7" in remaining[0].message
    assert diagnostic in remaining[0].message


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
    assert Message("The latest follow-up is complete.") in signals
    assert len([signal for signal in signals if isinstance(signal, TurnEnded)]) == 1
    assert len([signal for signal in signals if isinstance(signal, TurnFailed)]) == 0
    assert len([signal for signal in signals if isinstance(signal, SessionStarted)]) == 1
    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert (usage.uncached_input_tokens, usage.output_tokens) == (122, 49109)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (4828854, 121323)
    assert (spec.turn_dir / "implementer.stream.jsonl").read_bytes() == stream


@pytest.mark.asyncio
async def test_claude_repeated_init_with_a_different_session_remains_a_protocol_error(
    tmp_path: Path,
) -> None:
    output = (
        b'{"type":"system","subtype":"init","session_id":"session-a"}\n'
        b'{"type":"system","subtype":"init","session_id":"session-b"}\n'
        b'{"type":"result","subtype":"success","result":"finished"}\n'
    )
    program = f"import sys; sys.stdout.buffer.write({output!r})"
    spec = replace(_spec(tmp_path, session_id=None), rung=Rung("claude", "haiku"))
    clock = FakeClock(_TEST_NOW)
    parser = ClaudeTurnParser(
        session_id="unused-session",
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    process = TurnProcess((sys.executable, "-c", program), tmp_path, {}, parser)
    turn = await StreamingTurn.start(spec, SubprocessRunner(), _SingleProcessSequence(process))

    signals = [signal async for signal in turn.signals()]

    assert signals[0] == SessionStarted("session-a")
    assert isinstance(signals[-1], TurnFailed)
    assert "process emitted more than one SessionStarted" in signals[-1].message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "exit_code", "error_result", "expected_failure"),
    [
        ("diagnostic only", 0, None, None),
        ("authentication failed: invalid API key", 1, None, FailureKind.AUTH),
        (
            "",
            0,
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "terminal_reason": "error",
                "result": "Authentication failed: invalid API key",
            },
            FailureKind.AUTH,
        ),
    ],
)
async def test_claude_intermediate_success_waits_for_process_outcome(
    tmp_path: Path,
    stderr: str,
    exit_code: int,
    error_result: dict[str, object] | None,
    expected_failure: FailureKind | None,
) -> None:
    records: list[dict[str, object]] = [
        {"type": "system", "subtype": "init", "session_id": "session-process-outcome"},
        {
            "type": "result",
            "subtype": "success",
            "stop_reason": "end_turn",
            "terminal_reason": "completed",
            "result": "Intermediate verification completed.",
            "result_index": 0,
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Background follow-up is running."}]},
        },
    ]
    if error_result is not None:
        records.append(error_result)
    output = "".join(f"{json.dumps(record)}\n" for record in records).encode()
    program = (
        f"import sys; sys.stdout.buffer.write({output!r}); "
        f"sys.stderr.write({stderr!r}); sys.exit({exit_code})"
    )
    spec = replace(_spec(tmp_path, session_id=None), rung=Rung("claude", "haiku"))
    clock = FakeClock(_TEST_NOW)
    parser = ClaudeTurnParser(
        session_id="session-process-outcome",
        model="haiku",
        initial_usage=None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    process = TurnProcess((sys.executable, "-c", program), tmp_path, {}, parser)
    turn = await StreamingTurn.start(spec, SubprocessRunner(), _SingleProcessSequence(process))

    signals = [signal async for signal in turn.signals()]

    [terminal] = [signal for signal in signals if isinstance(signal, (TurnEnded, TurnFailed))]
    assert len([signal for signal in signals if isinstance(signal, SessionStarted)]) == 1
    if expected_failure is None:
        assert terminal == TurnEnded("Background follow-up is running.")
    else:
        assert isinstance(terminal, TurnFailed)
        assert terminal.kind is expected_failure
        assert "authentication failed" in terminal.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("post_tokens", [True, -1])
async def test_claude_malformed_compaction_does_not_start_continuation(
    tmp_path: Path,
    post_tokens: bool | int,
) -> None:
    session_id = "session-malformed-compaction"
    initial_stream = (
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "stop_reason": "end_turn",
                        "terminal_reason": "completed",
                        "result": "Implementation step completed.",
                    }
                ),
            ]
        )
        + "\n"
    )
    compact_stream = (
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "compact_boundary",
                        "compact_metadata": {"pre_tokens": 150_000, "post_tokens": post_tokens},
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "stop_reason": "end_turn",
                        "terminal_reason": "completed",
                        "result": "Compaction process completed.",
                    }
                ),
            ]
        )
        + "\n"
    )
    cli_dir = tmp_path / "bin"
    cli_dir.mkdir()
    cli = cli_dir / "claude"
    prompt_log = tmp_path / "prompts.txt"
    marker = tmp_path / "turn" / "implementer.compact-marker"
    cli.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import sys",
                "from pathlib import Path",
                "prompt = sys.argv[sys.argv.index('-p') + 1]",
                f"prompt_log = Path({str(prompt_log)!r})",
                "with prompt_log.open('a') as output_file:",
                "    output_file.write(prompt + '\\n')",
                "if prompt == '/compact':",
                f"    output = {compact_stream!r}",
                "else:",
                f"    output = {initial_stream!r}",
                f"    marker = Path({str(marker)!r})",
                "    marker.parent.mkdir(parents=True, exist_ok=True)",
                "    marker.touch()",
                "sys.stdout.write(output)",
                "sys.stdout.flush()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{cli_dir}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
    }
    spec = replace(
        _spec(tmp_path, session_id=session_id),
        rung=Rung("claude", "claude-haiku-4-5-20251001"),
        prompt="Implement the requested task.",
    )
    clock = FakeClock(_TEST_NOW)
    sequence = ClaudeTurnSequence(
        spec,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        compact_at_pct=80,
        args=(),
        environment=lambda turn_spec: {**environment, **turn_spec.env},
    )
    turn = await StreamingTurn.start(spec, SubprocessRunner(), sequence)

    signals = [signal async for signal in turn.signals()]

    [failure] = [signal for signal in signals if isinstance(signal, TurnFailed)]
    assert failure.kind is FailureKind.OTHER
    assert "malformed token counts" in failure.message
    assert not any(isinstance(signal, TurnEnded) for signal in signals)
    assert prompt_log.read_text().splitlines() == [spec.prompt, "/compact"]


@pytest.mark.asyncio
async def test_claude_compaction_sequence_continues_after_intermediate_results(
    tmp_path: Path,
) -> None:
    session_id = "session-background-redacted"
    compacted_fixture = (
        (_CLAUDE_FIXTURES / "turn_background_result_compaction.jsonl")
        .read_text()
        .replace("session-compact-redacted", session_id)
    )
    initial_stream = (
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "stop_reason": "end_turn",
                        "terminal_reason": "completed",
                        "result": "Intermediate work completed.",
                        "result_index": 0,
                        "usage": {
                            "input_tokens": 12619,
                            "output_tokens": 319121,
                            "cache_read_input_tokens": 15593363,
                            "cache_creation_input_tokens": 694420,
                        },
                        "total_cost_usd": 1.442999615,
                        "modelUsage": {
                            "claude-haiku-5-5": {
                                "inputTokens": 12619,
                                "outputTokens": 319121,
                                "cacheReadInputTokens": 15593363,
                                "cacheCreationInputTokens": 694420,
                                "costUSD": 1.442999615,
                                "contextWindow": 1000000,
                                "costBasis": "list",
                            }
                        },
                        "model_usage_since_last_result": {
                            "claude-haiku-5-5": {
                                "inputTokens": 12619,
                                "outputTokens": 319121,
                                "cacheReadInputTokens": 15593363,
                                "cacheCreationInputTokens": 694420,
                                "costUSD": 1.442999615,
                                "contextWindow": 1000000,
                                "costBasis": "list",
                            }
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    continuation_stream = (
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "stop_reason": "end_turn",
                        "terminal_reason": "completed",
                        "result": "Requested continuation completed.",
                        "result_index": 0,
                        "usage": {
                            "input_tokens": 9,
                            "output_tokens": 11,
                            "cache_read_input_tokens": 13,
                            "cache_creation_input_tokens": 17,
                        },
                        "total_cost_usd": 1.5,
                        "modelUsage": {
                            "claude-haiku-5-5": {
                                "inputTokens": 14739,
                                "outputTokens": 331091,
                                "cacheReadInputTokens": 15739413,
                                "cacheCreationInputTokens": 695347,
                                "costUSD": 1.5,
                                "contextWindow": 1000000,
                                "costBasis": "list",
                            }
                        },
                        "model_usage_since_last_result": {
                            "claude-haiku-5-5": {
                                "inputTokens": 9,
                                "outputTokens": 11,
                                "cacheReadInputTokens": 13,
                                "cacheCreationInputTokens": 17,
                                "costUSD": 0.018176785,
                                "contextWindow": 1000000,
                                "costBasis": "list",
                            }
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    cli_dir = tmp_path / "bin"
    cli_dir.mkdir()
    cli = cli_dir / "claude"
    prompt_log = tmp_path / "prompts.txt"
    marker = tmp_path / "turn" / "implementer.compact-marker"
    cli.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import sys",
                "from pathlib import Path",
                "prompt = sys.argv[sys.argv.index('-p') + 1]",
                f"prompt_log = Path({str(prompt_log)!r})",
                "with prompt_log.open('a') as output_file:",
                "    output_file.write(prompt + '\\n')",
                "if prompt == '/compact':",
                f"    output = {compacted_fixture!r}",
                f"elif prompt == {CONTINUE_PROMPT!r}:",
                f"    output = {continuation_stream!r}",
                "else:",
                f"    output = {initial_stream!r}",
                "    if prompt != '/compact':",
                f"        marker = Path({str(marker)!r})",
                "        marker.parent.mkdir(parents=True, exist_ok=True)",
                "        marker.touch()",
                "sys.stdout.write(output)",
                "sys.stdout.flush()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{cli_dir}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
    }
    spec = replace(
        _spec(tmp_path, session_id=session_id),
        rung=Rung("claude", "claude-haiku-4-5-20251001"),
        prompt="Implement the requested task.",
    )
    clock = FakeClock(_TEST_NOW)
    sequence = ClaudeTurnSequence(
        spec,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        compact_at_pct=80,
        args=(),
        environment=lambda turn_spec: {**environment, **turn_spec.env},
    )
    turn = await StreamingTurn.start(spec, SubprocessRunner(), sequence)

    signals = [signal async for signal in turn.signals()]

    assert len([signal for signal in signals if isinstance(signal, SessionStarted)]) == 1
    assert len([signal for signal in signals if isinstance(signal, Compacted)]) == 1
    assert [signal for signal in signals if isinstance(signal, TurnEnded)] == [
        TurnEnded("Requested continuation completed.")
    ]
    usages = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    assert [(usage.uncached_input_tokens, usage.output_tokens) for usage in usages] == [
        (12619, 319121),
        (14730, 331080),
        (14739, 331091),
    ]
    assert [usage.cost_usd for usage in usages] == [1.442999615, 1.481823215, 1.5]
    assert prompt_log.read_text().splitlines() == [
        "Implement the requested task.",
        "/compact",
        CONTINUE_PROMPT,
    ]


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
