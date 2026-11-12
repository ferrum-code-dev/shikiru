from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from shikiru.agents.contracts import (
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_UNKNOWN,
    RUNTIME_DEFAULT_MODEL,
    AgentContext,
    AgentSignal,
    BillingDeclaration,
    Rung,
    TurnEnded,
    TurnSpec,
)
from shikiru.agents.settings import AgentOptionsError
from shikiru.plugins.agents.muse.agent import create
from shikiru.plugins.agents.muse.options import (
    DEFAULT_ARGS,
    EXEC_SUBCOMMAND,
    JSON_FLAG,
    MODEL_FLAG,
    MUSE_COMMAND,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RESUME_SUBCOMMAND,
    SOFT_THRESHOLD_FLAG,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.process import SubprocessRunner
from tests.support.clock import LONG_AGO, FakeClock

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "turn_first.jsonl"
_FAKE_MUSE_ARGV_ENV = "FAKE_MUSE_ARGV"
_FAKE_MUSE_STREAM_ENV = "FAKE_MUSE_STREAM"
_FAKE_MUSE = """#!{python}
import json
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_MUSE_ARGV"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
sys.stdout.write(Path(os.environ["FAKE_MUSE_STREAM"]).read_text(encoding="utf-8"))
"""
_TURN_TIMEOUT_SECONDS = 10
_SESSION_ID = "00000000-0000-7000-9000-000000000003"
_MODEL = "muse-spark-1.3"
_EFFORT = "high"
_CUSTOM_ARGS = ("--disable-shell",)
_CUSTOM_COMPACT_AT_PCT = 50
_ROLE = "implementer"
_PROMPT = "continue the task"


def _context(options: dict[str, Any]) -> AgentContext:
    return AgentContext(options=options, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO))


def _argument_block(argv: list[str], *, before: str, count: int) -> list[str]:
    end = argv.index(before)
    return argv[end - count : end]


async def _run_turn(tmp_path: Path, options: dict[str, Any]) -> tuple[list[str], list[AgentSignal]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    argv_record = tmp_path / "argv.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = TurnSpec(
        rung=Rung(agent=MUSE_COMMAND, model=_MODEL),
        role=_ROLE,
        prompt=_PROMPT,
        cwd=repo,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            XDG_DATA_HOME_ENV_VAR: str(tmp_path / "data"),
            _FAKE_MUSE_ARGV_ENV: str(argv_record),
            _FAKE_MUSE_STREAM_ENV: str(_FIXTURE),
        },
        session_id=None,
        usage=None,
    )
    turn = await create(_context(options)).start_turn(spec)
    async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
        signals = [signal async for signal in turn.signals()]
    argv: list[str] = json.loads(argv_record.read_text(encoding="utf-8"))
    return argv, signals


@pytest.mark.parametrize(
    ("rung", "flags"),
    [
        (Rung(agent=MUSE_COMMAND, model=RUNTIME_DEFAULT_MODEL), ()),
        (
            Rung(agent=MUSE_COMMAND, model=_MODEL, effort=_EFFORT),
            (MODEL_FLAG, _MODEL, REASONING_EFFORT_FLAG, _EFFORT),
        ),
    ],
)
def test_take_over_resumes_the_session_on_its_rung_with_the_configured_arguments(
    tmp_path: Path, rung: Rung, flags: tuple[str, ...]
) -> None:
    command = create(_context({})).interactive_command(_SESSION_ID, tmp_path, rung)

    assert tuple(command) == (
        MUSE_COMMAND,
        RESUME_SUBCOMMAND,
        _SESSION_ID,
        *flags,
        *DEFAULT_ARGS,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected_args", "expected_soft"),
    [
        ({}, DEFAULT_ARGS, "0.8"),
        (
            {"args": list(_CUSTOM_ARGS), "compact_at_pct": _CUSTOM_COMPACT_AT_PCT},
            _CUSTOM_ARGS,
            "0.5",
        ),
    ],
)
async def test_started_turn_runs_muse_with_the_configured_arguments_and_compaction(
    tmp_path: Path,
    options: dict[str, Any],
    expected_args: tuple[str, ...],
    expected_soft: str,
) -> None:
    argv, signals = await _run_turn(tmp_path, options)

    assert argv[:2] == [EXEC_SUBCOMMAND, JSON_FLAG]
    assert argv[argv.index(SOFT_THRESHOLD_FLAG) + 1] == expected_soft
    assert _argument_block(argv, before=PROMPT_FILE_FLAG, count=len(expected_args)) == list(
        expected_args
    )
    assert argv[-1] == str(tmp_path / "turn" / PROMPT_FILENAME)
    assert any(isinstance(signal, TurnEnded) for signal in signals)


@pytest.mark.parametrize(
    ("options", "declaration"),
    [
        ({}, BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN)),
        (
            {"billing_mode": BILLING_MODE_SUBSCRIPTION},
            BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_OPERATOR_CONFIG),
        ),
    ],
)
def test_billing_declaration_comes_from_the_billing_mode_option(
    options: dict[str, Any], declaration: BillingDeclaration
) -> None:
    assert create(_context(options)).billing_declaration == declaration


@pytest.mark.parametrize(
    ("options", "rejected_key"),
    [
        ({"unknown": True}, "unknown"),
        ({"compact_at_pct": 100}, "compact_at_pct"),
    ],
)
def test_create_rejects_invalid_options_naming_the_offending_option(
    options: dict[str, Any], rejected_key: str
) -> None:
    with pytest.raises(AgentOptionsError) as rejection:
        create(_context(options))

    assert rejection.value.key == rejected_key
