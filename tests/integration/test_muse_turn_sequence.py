from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shikiru.agents.contracts import (
    RUNTIME_DEFAULT_MODEL,
    AgentSignal,
    Rung,
    TurnFailed,
    TurnSpec,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.plugins.agents.muse.options import (
    DEFAULT_COMPACT_AT_PCT,
    EXEC_SUBCOMMAND,
    HARD_THRESHOLD_FLAG,
    JSON_FLAG,
    MAX_COMPACT_AT_PCT,
    MODEL_FLAG,
    MUSE_COMMAND,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RUN_TERMINAL_FAILED_TYPE,
    SESSION_ID_FLAG,
    SOFT_THRESHOLD_FLAG,
)
from shikiru.plugins.agents.muse.turn import MuseTurnSequence
from shikiru.private import PRIVATE_FILE_MODE, write_private_text
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_TEST_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TURN_TIMEOUT_SECONDS = 10
_AGENT = "muse"
_MODEL = "muse-model"
_EFFORT = "high"
_SESSION_ID = "00000000-0000-7000-9000-000000000002"
_SPEC_PROMPT = "continue the task"
_SOFT_THRESHOLD = "0.8"
_HARD_THRESHOLD = "0.805"
_EXTRA_ARGS = ("--disable-shell",)
_CONTEXT_REASON = "context window exceeded"
_FAKE_ENV_CALLS = "FAKE_MUSE_CALLS"
_FAKE_ENV_RESPONSES = "FAKE_MUSE_RESPONSES"
_FAKE_MUSE = """#!{python}
import json
import os
import stat
import sys
from pathlib import Path

calls = Path(os.environ["FAKE_MUSE_CALLS"])
responses = json.loads(os.environ["FAKE_MUSE_RESPONSES"])
index = len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0
with calls.open("a", encoding="utf-8") as log:
    prompt_path = Path(sys.argv[sys.argv.index("--prompt-file") + 1])
    record = {{
        "argv": sys.argv[1:],
        "auto_update": os.environ.get("MUSE_NO_AUTO_UPDATE"),
        "prompt": prompt_path.read_text(encoding="utf-8"),
        "prompt_mode": stat.S_IMODE(prompt_path.stat().st_mode),
    }}
    log.write(json.dumps(record) + "\\n")
response = responses[index]
sys.stdout.write("".join(line + "\\n" for line in response["lines"]))
sys.exit(response["exit"])
"""


@dataclass(frozen=True)
class _Response:
    lines: list[str]
    exit_code: int


def _fixture_lines(name: str) -> list[str]:
    return (_FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()


def _success() -> _Response:
    return _Response(_fixture_lines("turn_first"), 0)


def _auth_failure() -> _Response:
    return _Response(_fixture_lines("turn_auth_rejected"), 1)


def _context_failure() -> _Response:
    session_record = json.loads(_fixture_lines("turn_first")[0])
    failure = {
        "stream": session_record["stream"],
        "payload_type": RUN_TERMINAL_FAILED_TYPE,
        "payload": {
            "kind": "run_terminal",
            "terminal": "failed",
            "text": "",
            "reason": _CONTEXT_REASON,
        },
    }
    return _Response([json.dumps(session_record), json.dumps(failure)], 1)


async def _run_turn(
    tmp_path: Path,
    responses: list[_Response],
    *,
    rung: Rung,
    session_id: str | None = None,
    base_environment: dict[str, str] | None = None,
    spec_environment: dict[str, str] | None = None,
    turn_prompt: str | None = None,
    compact_at_pct: float = DEFAULT_COMPACT_AT_PCT,
) -> tuple[list[dict[str, Any]], list[AgentSignal]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    scripted = [{"lines": response.lines, "exit": response.exit_code} for response in responses]
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        _FAKE_ENV_CALLS: str(calls),
        _FAKE_ENV_RESPONSES: json.dumps(scripted),
        **(spec_environment or {}),
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    turn_dir = tmp_path / "turn"
    if turn_prompt is not None:
        turn_dir.mkdir()
        write_private_text(turn_dir / PROMPT_FILENAME, turn_prompt)
    spec = TurnSpec(
        rung=rung,
        role="implementer",
        prompt=_SPEC_PROMPT,
        cwd=repo,
        run_dir=tmp_path / "run",
        turn_dir=turn_dir,
        add_dirs=(),
        env=env,
        session_id=session_id,
        usage=None,
    )
    clock = FakeClock(_TEST_NOW)
    sequence = MuseTurnSequence(
        args=_EXTRA_ARGS,
        base_environment=base_environment if base_environment is not None else dict(os.environ),
        clock=clock,
        reset_time=ResetTimeParser(clock),
        compact_at_pct=compact_at_pct,
    )
    turn = await StreamingTurn.start(spec, SubprocessRunner(), sequence)
    async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
        signals = [signal async for signal in turn.signals()]
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    return recorded, signals


def _expected_argv(tmp_path: Path, flags: tuple[str, ...]) -> list[str]:
    return [
        EXEC_SUBCOMMAND,
        JSON_FLAG,
        *flags,
        SOFT_THRESHOLD_FLAG,
        _SOFT_THRESHOLD,
        HARD_THRESHOLD_FLAG,
        _HARD_THRESHOLD,
        *_EXTRA_ARGS,
        PROMPT_FILE_FLAG,
        str(tmp_path / "turn" / PROMPT_FILENAME),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rung", "session_id", "flags"),
    [
        (Rung(agent=_AGENT, model=RUNTIME_DEFAULT_MODEL), None, ()),
        (
            Rung(agent=_AGENT, model=_MODEL, effort=_EFFORT),
            _SESSION_ID,
            (SESSION_ID_FLAG, _SESSION_ID, MODEL_FLAG, _MODEL, REASONING_EFFORT_FLAG, _EFFORT),
        ),
    ],
)
async def test_turn_builds_exec_json_argv_from_session_model_and_effort(
    tmp_path: Path, rung: Rung, session_id: str | None, flags: tuple[str, ...]
) -> None:
    calls, _ = await _run_turn(tmp_path, [_success()], rung=rung, session_id=session_id)

    assert calls[0]["argv"] == _expected_argv(tmp_path, flags)


@pytest.mark.asyncio
async def test_turn_forces_auto_update_off_over_base_and_turn_environment(tmp_path: Path) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        base_environment={**os.environ, NO_AUTO_UPDATE_ENV_VAR: "0"},
        spec_environment={NO_AUTO_UPDATE_ENV_VAR: "0"},
    )

    assert calls[0]["auto_update"] == NO_AUTO_UPDATE_VALUE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "expected_calls", "expects_failure"),
    [
        ([_context_failure(), _success()], 2, False),
        ([_auth_failure()], 1, True),
        ([_context_failure(), _context_failure()], 2, True),
    ],
)
async def test_turn_retries_once_only_after_a_context_failure(
    tmp_path: Path,
    responses: list[_Response],
    expected_calls: int,
    expects_failure: bool,
) -> None:
    calls, signals = await _run_turn(tmp_path, responses, rung=Rung(agent=_AGENT, model=_MODEL))

    assert len(calls) == expected_calls
    assert any(isinstance(signal, TurnFailed) for signal in signals) is expects_failure


@pytest.mark.asyncio
async def test_context_retry_resumes_the_session_the_failed_process_started(tmp_path: Path) -> None:
    calls, _ = await _run_turn(
        tmp_path, [_context_failure(), _success()], rung=Rung(agent=_AGENT, model=_MODEL)
    )

    assert calls[0]["argv"] == _expected_argv(tmp_path, (MODEL_FLAG, _MODEL))
    assert calls[1]["argv"] == _expected_argv(
        tmp_path, (SESSION_ID_FLAG, _SESSION_ID, MODEL_FLAG, _MODEL)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_prompt", "expected_prompt"),
    [(None, _SPEC_PROMPT), ("engine prompt", "engine prompt")],
)
async def test_turn_prompt_file_is_private_and_keeps_an_engine_written_prompt(
    tmp_path: Path, engine_prompt: str | None, expected_prompt: str
) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        turn_prompt=engine_prompt,
    )

    assert calls[0]["prompt"] == expected_prompt
    assert calls[0]["prompt_mode"] == PRIVATE_FILE_MODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compact_at_pct", "soft", "hard"),
    [
        (0, "0.0", "0.005"),
        (DEFAULT_COMPACT_AT_PCT, _SOFT_THRESHOLD, _HARD_THRESHOLD),
        (MAX_COMPACT_AT_PCT, "0.99", "0.995"),
    ],
)
async def test_compaction_thresholds_keep_hard_above_soft_across_the_supported_range(
    tmp_path: Path, compact_at_pct: float, soft: str, hard: str
) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        compact_at_pct=compact_at_pct,
    )

    argv = calls[0]["argv"]
    assert argv[argv.index(SOFT_THRESHOLD_FLAG) + 1] == soft
    assert argv[argv.index(HARD_THRESHOLD_FLAG) + 1] == hard
