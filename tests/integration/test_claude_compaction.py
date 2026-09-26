"""Claude's in-turn compaction: the `PostToolUse` hook and the process sequence (04 §4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shikiru.agents.contracts import AgentContext, Rung, TurnSpec, module_argv
from shikiru.agents.sdk.turn import TurnProcess, TurnProcessResult
from shikiru.clock import SystemClock
from shikiru.plugins.agents.claude.agent import create
from shikiru.plugins.agents.claude.hook import STOP_REASON
from shikiru.plugins.agents.claude.turn import CONTINUE_PROMPT, ClaudeTurnSequence
from shikiru.process import SubprocessRunner

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_HOOK_MODULE = "shikiru.plugins.agents.claude.hook"
_ROLE = "implementer"
_STOP = "turn_hook_stop_live_redacted.jsonl"
_COMPACT = "turn_hook_compact_live_redacted.jsonl"
_CONTINUE = "turn_hook_continue_live_redacted.jsonl"
_CONTEXT_TOKENS = 10_000
_LIMIT_TOKENS = 12_000
_HOOK_TIMEOUT_SECONDS = 30.0
_STOP_DECISION = json.dumps({"continue": False, "stopReason": STOP_REASON})


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


def test_hook_stops_compact_and_continue_until_the_turn_ends(tmp_path: Path) -> None:
    spec = _spec(tmp_path, session_id=None)
    sequence = _sequence(spec)
    marker = spec.turn_dir / f"{_ROLE}.compact-marker"
    command: TurnProcess | None = sequence.start(spec)
    prompts: list[str] = []
    for stream in (_STOP, _COMPACT, _STOP, _COMPACT, _CONTINUE):
        assert command is not None
        prompts.append(command.argv[2])
        if stream == _STOP:
            marker.touch()
        command = sequence.advance(spec, _outcome(command, stream))

    assert command is None
    assert prompts == [spec.prompt, "/compact", CONTINUE_PROMPT, "/compact", CONTINUE_PROMPT]


def test_a_hook_stop_without_the_plugin_marker_ends_the_turn(tmp_path: Path) -> None:
    spec = _spec(tmp_path, session_id=None)
    sequence = _sequence(spec)
    command = sequence.start(spec)

    assert sequence.advance(spec, _outcome(command, _STOP)) is None


def _spec(tmp_path: Path, *, session_id: str | None) -> TurnSpec:
    run_dir = tmp_path / "run"
    turn_dir = tmp_path / "turn"
    turn_dir.mkdir()
    return TurnSpec(
        rung=Rung("claude", "haiku", "low"),
        role=_ROLE,
        prompt="read the files",
        cwd=tmp_path,
        run_dir=run_dir,
        turn_dir=turn_dir,
        add_dirs=(),
        env={},
        session_id=session_id,
        usage=None,
    )


def _sequence(spec: TurnSpec) -> ClaudeTurnSequence:
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))
    return agent.process_sequence(spec)


def _outcome(command: TurnProcess, stream: str) -> TurnProcessResult:
    signals = [
        signal
        for line in (_FIXTURES_DIR / stream).read_text().splitlines()
        for signal in command.parser.feed(line)
    ]
    signals.extend(command.parser.finish(0, ""))
    return TurnProcessResult(tuple(signals), 0, "")
