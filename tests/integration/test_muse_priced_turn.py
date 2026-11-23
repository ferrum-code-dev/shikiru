"""Muse's captured usage is priced through the agent pool and a real child process."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from shikiru.agents.contracts import Rung, TurnSpec, UsageReported
from shikiru.agents.registry import AgentRegistry
from shikiru.config.settings import AgentConfig
from shikiru.engine.agents import AgentPool
from shikiru.plugins.agents.muse.agent import create
from shikiru.plugins.agents.muse.options import (
    CACHED_TOKENS_KEY,
    EVENT_KIND_KEY,
    INPUT_TOKENS_KEY,
    MODEL_COMPLETED_EVENT,
    MODEL_KEY,
    MUSE_HOME_DIRECTORY,
    OUTPUT_TOKENS_KEY,
    PAYLOAD_EVENT_KEY,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    SESSION_LOG_FILENAME,
    SESSION_PAYLOAD_TYPE,
    SESSIONS_DIRECTORY,
    USAGE_KEY,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.process import SubprocessRunner
from tests.integration.fake_muse import install_fake_exec, install_fake_muse
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings

pytestmark = pytest.mark.asyncio

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "turn_first.jsonl"
_SESSION_ID = "00000000-0000-7000-9000-000000000002"
_SESSION_LOG_DATE = ("2026", "11", "12")
_MODEL = "muse-ember-1.0"
_MATCHING_INPUT_TOKENS = 32_358
_MATCHING_OUTPUT_TOKENS = 18
_TRAILING_INPUT_TOKENS = 99_999
_TRAILING_OUTPUT_TOKENS = 99_999


def _session_log_path(data_home: Path) -> Path:
    return data_home.joinpath(
        MUSE_HOME_DIRECTORY,
        SESSIONS_DIRECTORY,
        *_SESSION_LOG_DATE,
        _SESSION_ID,
        SESSION_LOG_FILENAME,
    )


def _model_line(input_tokens: int, output_tokens: int) -> str:
    return json.dumps(
        {
            RECORD_PAYLOAD_TYPE_KEY: SESSION_PAYLOAD_TYPE,
            RECORD_PAYLOAD_KEY: {
                PAYLOAD_EVENT_KEY: {
                    EVENT_KIND_KEY: MODEL_COMPLETED_EVENT,
                    MODEL_KEY: _MODEL,
                    USAGE_KEY: {
                        INPUT_TOKENS_KEY: input_tokens,
                        OUTPUT_TOKENS_KEY: output_tokens,
                        CACHED_TOKENS_KEY: 0,
                    },
                }
            },
        }
    )


async def test_muse_turn_uses_saved_prices_and_runtime_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/agents/muse/turn_first.jsonl"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "muse"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"if 'exec' in sys.argv:\n    print('\\n'.join({lines!r}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "project"
    run_dir = root / ".shikiru/runs/T-1"
    turn_dir = run_dir / "turns/01"
    turn_dir.mkdir(parents=True)
    prices = {
        "data": [
            {
                "id": "meta/muse-ember",
                "pricing": {
                    "prompt": "1",
                    "completion": "2",
                    "input_cache_read": "3",
                    "input_cache_write": "4",
                },
            }
        ]
    }
    (root / ".shikiru/openrouter-models.json").write_text(
        json.dumps({"saved_at": TASK_CREATED_AT.isoformat(), "response": json.dumps(prices)}),
        encoding="utf-8",
    )
    clock = FakeClock(TASK_CREATED_AT)
    registry = AgentRegistry()
    registry.register("muse", create)
    settings = make_settings(agents={"muse": AgentConfig(options={})})
    agent = AgentPool(registry.create, SubprocessRunner(), settings, clock).get("muse")
    spec = TurnSpec(
        rung=Rung("muse", "default"),
        role="implementer",
        prompt="test",
        cwd=root,
        run_dir=run_dir,
        turn_dir=turn_dir,
        add_dirs=(),
        env={},
        session_id=None,
        usage=None,
    )
    turn = await agent.start_turn(spec)
    signals = [signal async for signal in turn.signals()]
    usage = next(signal.usage for signal in signals if isinstance(signal, UsageReported))
    assert usage.cost_basis == "api_equivalent"
    assert usage.cost_usd == 32358 + 18 * 2


async def test_muse_turn_prices_only_stream_counted_calls_with_trailing_session_completions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_muse(tmp_path, monkeypatch)
    data_home = tmp_path / "data"
    install_fake_exec(
        monkeypatch,
        session_log=_session_log_path(data_home),
        session_lines=[
            _model_line(_MATCHING_INPUT_TOKENS, _MATCHING_OUTPUT_TOKENS),
            _model_line(_TRAILING_INPUT_TOKENS, _TRAILING_OUTPUT_TOKENS),
        ],
        stream_lines=_FIXTURE.read_text(encoding="utf-8").splitlines(),
    )
    root = tmp_path / "project"
    run_dir = root / ".shikiru/runs/T-1"
    turn_dir = run_dir / "turns/01"
    turn_dir.mkdir(parents=True)
    prices = {
        "data": [
            {
                "id": "meta/muse-ember",
                "pricing": {
                    "prompt": "1",
                    "completion": "2",
                    "input_cache_read": "3",
                    "input_cache_write": "4",
                },
            }
        ]
    }
    (root / ".shikiru/openrouter-models.json").write_text(
        json.dumps({"saved_at": TASK_CREATED_AT.isoformat(), "response": json.dumps(prices)}),
        encoding="utf-8",
    )
    clock = FakeClock(TASK_CREATED_AT)
    registry = AgentRegistry()
    registry.register("muse", create)
    settings = make_settings(agents={"muse": AgentConfig(options={})})
    agent = AgentPool(registry.create, SubprocessRunner(), settings, clock).get("muse")
    spec = TurnSpec(
        rung=Rung("muse", "default"),
        role="implementer",
        prompt="test",
        cwd=root,
        run_dir=run_dir,
        turn_dir=turn_dir,
        add_dirs=(),
        env={XDG_DATA_HOME_ENV_VAR: str(data_home)},
        session_id=None,
        usage=None,
    )
    turn = await agent.start_turn(spec)
    signals = [signal async for signal in turn.signals()]
    usage = next(signal.usage for signal in signals if isinstance(signal, UsageReported))
    assert usage.cost_basis == "api_equivalent"
    assert usage.cost_usd == 32358 + 18 * 2
