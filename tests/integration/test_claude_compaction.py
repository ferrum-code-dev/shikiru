"""Claude's in-turn compaction: the `PostToolUse` hook and the process sequence (04 §4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shikiru.agents.contracts import AgentContext, Rung, TurnSpec, module_argv
from shikiru.agents.sdk.turn import TurnProcessResult
from shikiru.clock import SystemClock
from shikiru.plugins.agents.claude.agent import create
from shikiru.plugins.agents.claude.hook import STOP_REASON
from shikiru.process import SubprocessRunner

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_HOOK_MODULE = "shikiru.plugins.agents.claude.hook"
_ROLE = "implementer"
_STOP = "turn_hook_stop_live_redacted.jsonl"
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
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))
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
