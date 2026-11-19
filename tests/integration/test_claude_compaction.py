"""Claude's in-turn compaction: the `PostToolUse` hook and the process sequence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import (
    AgentContext,
    ContextWindowReported,
    Rung,
    TurnSpec,
    module_argv,
)
from shikiru.agents.sdk.turn import TurnProcessResult
from shikiru.plugins.agents.claude.agent import create
from shikiru.plugins.agents.claude.hook import STOP_REASON
from shikiru.plugins.agents.claude.options import SETTINGS_FLAG
from shikiru.process import SubprocessRunner
from shikiru.store.context_windows import ContextWindowStore
from tests.support.clock import LONG_AGO, FakeClock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import AgentSignal
    from shikiru.agents.sdk.turn import TurnParser
    from shikiru.plugins.agents.claude.agent import ClaudeAgent
    from tests.support.store import Connection

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_HOOK_MODULE = "shikiru.plugins.agents.claude.hook"
_ROLE = "implementer"
_STOP = "turn_hook_stop_live_redacted.jsonl"
_CONTEXT_TOKENS = 10_000
_LIMIT_TOKENS = 12_000
_HOOK_TIMEOUT_SECONDS = 30.0
_STOP_DECISION = json.dumps({"continue": False, "stopReason": STOP_REASON})
_AGENT = "claude"
_MODEL = "claude-haiku-5-5"
_MODEL_WINDOW_TOKENS = 1_000_000
_SESSION = "session-compaction"
_HUGE_CONTEXT_TOKENS = 1_200_000
_RESULT_LINES = (
    json.dumps({"type": "system", "subtype": "init", "session_id": _SESSION, "model": _MODEL}),
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "msg-1",
                "content": [{"type": "text", "text": "Done."}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        }
    ),
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": "Done.",
            "session_id": _SESSION,
            "modelUsage": {
                _MODEL: {
                    "inputTokens": 10,
                    "outputTokens": 2,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.0,
                    "costBasis": "list",
                    "contextWindow": _MODEL_WINDOW_TOKENS,
                }
            },
        }
    ),
)


@pytest.mark.parametrize(
    ("result_chars", "stops"),
    [
        ((3000,), False),
        ((6000,), True),
        ((2400, 2400), False),
        ((2400, 2400, 2400), True),
    ],
    ids=["below", "reaches-limit", "parallel-below", "parallel-reaches-limit"],
)
async def test_hook_stops_the_turn_once_the_next_request_reaches_the_limit(
    tmp_path: Path, result_chars: tuple[int, ...], stops: bool
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"id": "msg-1", "usage": {"input_tokens": _CONTEXT_TOKENS}},
            }
        )
        + "\n"
    )
    marker = tmp_path / "marker"
    argv = module_argv(_HOOK_MODULE, str(tmp_path / "state"), str(marker), str(_LIMIT_TOKENS))
    outputs = [
        await SubprocessRunner().run(
            argv,
            timeout=_HOOK_TIMEOUT_SECONDS,
            input_text=json.dumps(
                {"transcript_path": str(transcript), "tool_response": "x" * chars}
            ),
            check=True,
        )
        for chars in result_chars
    ]

    assert marker.exists() is stops
    assert all(not output.stdout for output in outputs[:-1])
    assert outputs[-1].stdout.strip() == (_STOP_DECISION if stops else "")


@pytest.mark.parametrize(
    ("stream", "stderr", "exit_code"),
    [
        (_STOP, None, 0),
        ("turn_unknown_error.jsonl", "turn_unknown_error.stderr.log", 1),
        ("turn_rate_limit.jsonl", "turn_rate_limit.stderr.log", 1),
    ],
    ids=["operator-hook-stop", "unknown-failure", "quota-failure"],
)
def test_only_the_plugin_marker_or_a_context_failure_continues_the_turn(
    tmp_path: Path, stream: str, stderr: str | None, exit_code: int
) -> None:
    spec = TurnSpec(
        rung=Rung("claude", "haiku", "low"),
        role=_ROLE,
        prompt="read the files",
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={},
        session_id=None,
        usage=None,
    )
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))
    sequence = agent.process_sequence(spec)
    command = sequence.start(spec)
    stderr_text = (_FIXTURES_DIR / stderr).read_text() if stderr is not None else ""
    signals = [
        signal
        for line in (_FIXTURES_DIR / stream).read_text().splitlines()
        for signal in command.parser.feed(line)
    ]
    signals.extend(command.parser.finish(exit_code, stderr_text))

    assert sequence.advance(spec, TurnProcessResult(tuple(signals), exit_code, stderr_text)) is None


def _agent() -> ClaudeAgent:
    return create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))


def _spec(tmp_path: Path, *, session_id: str | None, context_window: int | None) -> TurnSpec:
    return TurnSpec(
        rung=Rung("claude", _MODEL, "low"),
        role=_ROLE,
        prompt="read the files and report",
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        session_id=session_id,
        usage=None,
        context_window=context_window,
    )


def _write_transcript(config_dir: Path, context_tokens: int) -> None:
    project = config_dir / "projects" / "scratch-repo"
    project.mkdir(parents=True, exist_ok=True)
    entry = {
        "type": "assistant",
        "message": {"id": "msg-1", "usage": {"input_tokens": context_tokens}},
    }
    (project / f"{_SESSION}.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")


def _replay(
    parser: TurnParser, lines: Sequence[str], exit_code: int = 0
) -> tuple[AgentSignal, ...]:
    signals = [signal for line in lines for signal in parser.feed(line)]
    signals.extend(parser.finish(exit_code, ""))
    return tuple(signals)


def _fixture_lines(name: str) -> tuple[str, ...]:
    return tuple((_FIXTURES_DIR / name).read_text(encoding="utf-8").splitlines())


def test_a_resumed_session_is_not_compacted_before_its_window_is_known(tmp_path: Path) -> None:
    _write_transcript(tmp_path / "claude-config", _HUGE_CONTEXT_TOKENS)
    spec = _spec(tmp_path, session_id=_SESSION, context_window=None)

    command = _agent().process_sequence(spec).start(spec)

    assert command.argv[2] == spec.prompt
    assert SETTINGS_FLAG not in command.argv


@pytest.mark.parametrize(
    ("context_tokens", "compacts"),
    [
        (158_000, False),
        (170_000, False),
        (799_999, False),
        (800_000, True),
    ],
    ids=["158k", "170k", "just-below-80pct", "at-80pct"],
)
def test_the_window_an_earlier_turn_reported_sets_the_later_compaction_limit(
    tmp_path: Path, connection: Connection, context_tokens: int, compacts: bool
) -> None:
    store = ContextWindowStore(connection)
    agent = _agent()
    first = _spec(tmp_path, session_id=None, context_window=store.window(_AGENT, _MODEL))
    signals = _replay(agent.process_sequence(first).start(first).parser, _RESULT_LINES)
    assert ContextWindowReported(_MODEL_WINDOW_TOKENS) in signals
    for signal in signals:
        if isinstance(signal, ContextWindowReported):
            store.record(_AGENT, _MODEL, signal.tokens)
    _write_transcript(tmp_path / "claude-config", context_tokens)

    later = _spec(tmp_path, session_id=_SESSION, context_window=store.window(_AGENT, _MODEL))
    command = agent.process_sequence(later).start(later)

    assert (command.argv[2] == "/compact") is compacts


def test_a_context_failure_still_compacts_once_when_the_window_is_unknown(tmp_path: Path) -> None:
    spec = _spec(tmp_path, session_id=_SESSION, context_window=None)
    sequence = _agent().process_sequence(spec)
    failed = _replay(sequence.start(spec).parser, _fixture_lines("turn_context_failure.jsonl"))
    compaction = sequence.advance(spec, TurnProcessResult(failed, 0, ""))
    assert compaction is not None
    assert compaction.argv[2] == "/compact"
    compacted = _replay(compaction.parser, _fixture_lines("turn_compact_process.jsonl"))
    rerun = sequence.advance(spec, TurnProcessResult(compacted, 0, ""))
    assert rerun is not None
    assert rerun.argv[2] == spec.prompt
    rerun_failed = _replay(rerun.parser, _fixture_lines("turn_context_failure.jsonl"))

    assert sequence.advance(spec, TurnProcessResult(rerun_failed, 0, "")) is None
