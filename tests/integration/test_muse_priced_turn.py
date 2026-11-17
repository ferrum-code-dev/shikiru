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
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings

pytestmark = pytest.mark.asyncio


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
