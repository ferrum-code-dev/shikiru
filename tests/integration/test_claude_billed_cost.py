"""A provider-reported Claude cost is billed only under the run's persisted API declaration."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    COST_BASIS_API_EQUIVALENT,
    COST_BASIS_BILLED,
    AgentContext,
    Rung,
    TurnSpec,
    UsageReported,
)
from shikiru.agents.registry import AgentRegistry
from shikiru.config.settings import AgentConfig
from shikiru.domain.tasks import Mode, Role
from shikiru.engine.agents import AgentPool
from shikiru.engine.sessions import SessionManager
from shikiru.plugins.agents.claude.agent import create
from shikiru.plugins.widgets.usage import UsagePanel
from shikiru.process import SubprocessRunner
from shikiru.store.runs import RunStore
from tests.support.cells import rendered_lines
from tests.support.clock import FakeClock
from tests.support.dashboard import WidgetHostApp, widget_context
from tests.support.engine import FAKE_AGENT_NAME, FAKE_RUNG, build_registry
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.flow import build_flow, build_flow_task, init_project_repo
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
    from shikiru.store.reader import StateReader
    from tests.support.engine import EngineEnv
    from tests.support.store import Connection

pytestmark = pytest.mark.asyncio

_MODEL = "claude-haiku-4-5-20251001"
_LIST_COST = 0.00002
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "claude" / "turn_list_cost.jsonl"
_FAKE_COST_CLI = Path(__file__).parents[1] / "support" / "fake_cost_claude.py"
_API_AGENT = "claude-api"
_SUBSCRIPTION_AGENT = "claude-subscription"
_IMPLEMENTER_BILLED_COST = 1.25
_LEADER_ESTIMATED_COST = 0.75
_FLOW_TIMEOUT_SECONDS = 60
_WAIT_SECONDS = 5
_WIDE = (100, 40)
_BILLED_TOTAL = f"${_IMPLEMENTER_BILLED_COST:.2f} API"
_ESTIMATED_TOTAL = f"est. ${_LEADER_ESTIMATED_COST:.2f} API-equivalent"


def _install_fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    program = (
        f"#!{sys.executable}\n"
        "import sys\n"
        "for line in sys.stdin.read().splitlines():\n"
        "    pass\n"
        f"for line in {lines!r}:\n"
        "    print(line)\n"
    )
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "claude"
    executable.write_text(program, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}:{os.environ['PATH']}")


def _install_fake_cost_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "claude"
    script = _FAKE_COST_CLI.read_text(encoding="utf-8")
    executable.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _settings(billing_mode: str) -> Settings:
    return make_settings(
        implementer_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig(options={"billing_mode": billing_mode})},
    )


def _session_manager(connection: Connection, settings: Settings, tmp_path: Path) -> SessionManager:
    clock = FakeClock(TASK_CREATED_AT)
    return SessionManager(
        runs=RunStore(connection, clock),
        agents=AgentPool(build_registry().create, SubprocessRunner(), settings, clock),
        project_root=tmp_path,
        settings=settings,
    )


async def _run_turn_and_record(
    store: RunStore, run: Run, *, agent_options_mode: str, tmp_path: Path, clock: FakeClock
) -> Run:
    agent = create(
        AgentContext(
            options={"billing_mode": agent_options_mode}, runner=SubprocessRunner(), clock=clock
        )
    )
    run_dir = tmp_path / "run"
    turn_dir = tmp_path / "turn"
    run_dir.mkdir(exist_ok=True)
    turn_dir.mkdir(exist_ok=True)
    spec = TurnSpec(
        rung=Rung(agent=run.agent, model=_MODEL),
        role=Role.IMPLEMENTER.value,
        prompt="test",
        cwd=tmp_path,
        run_dir=run_dir,
        turn_dir=turn_dir,
        add_dirs=(),
        env={},
        session_id=run.session_id,
        usage=run.usage,
        billing_mode=run.billing_mode,
    )
    turn = await agent.start_turn(spec)
    signals = [signal async for signal in turn.signals()]
    [usage] = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    store.record_usage(run.id, usage)
    persisted = store.for_task(run.task_id)
    return next(item for item in persisted if item.id == run.id)


@pytest.mark.parametrize(
    ("run_mode", "agent_options_mode", "expected_basis"),
    [
        (BILLING_MODE_API, BILLING_MODE_SUBSCRIPTION, COST_BASIS_BILLED),
        (BILLING_MODE_SUBSCRIPTION, BILLING_MODE_API, COST_BASIS_API_EQUIVALENT),
    ],
)
async def test_resumed_session_classification_uses_the_persisted_declaration(
    connection: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_mode: str,
    agent_options_mode: str,
    expected_basis: str,
) -> None:
    _install_fake_claude(tmp_path, monkeypatch)
    clock = FakeClock(TASK_CREATED_AT)
    task = seed_task(connection, clock)
    run = (
        _session_manager(connection, _settings(run_mode), tmp_path)
        .session_for(task, Role.IMPLEMENTER)
        .run
    )
    resumed = _session_manager(connection, _settings(agent_options_mode), tmp_path).session_for(
        task, Role.IMPLEMENTER
    )

    assert resumed.run.id == run.id
    assert resumed.run.billing_mode == run_mode

    persisted = await _run_turn_and_record(
        RunStore(connection, clock),
        resumed.run,
        agent_options_mode=agent_options_mode,
        tmp_path=tmp_path,
        clock=clock,
    )

    assert persisted.usage.cost_usd == _LIST_COST
    assert persisted.usage.cost_basis == expected_basis


def _mixed_task_settings() -> Settings:
    return make_settings(
        implementer_ladder=[Rung(_API_AGENT, _MODEL)],
        leader_ladder=[Rung(_SUBSCRIPTION_AGENT, _MODEL)],
        helper_ladder=[Rung(_SUBSCRIPTION_AGENT, _MODEL)],
        agents={
            _API_AGENT: AgentConfig(options={"billing_mode": BILLING_MODE_API}),
            _SUBSCRIPTION_AGENT: AgentConfig(options={"billing_mode": BILLING_MODE_SUBSCRIPTION}),
        },
    )


def _mixed_task_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(_API_AGENT, create)
    registry.register(_SUBSCRIPTION_AGENT, create)
    return registry


def _panel_text(panel: UsagePanel) -> str:
    return "\n".join("".join(char for char, _ in cells) for cells in rendered_lines(panel) if cells)


async def test_mixed_task_shows_api_billed_and_subscription_estimated_costs_separately(
    engine_env: EngineEnv,
    reader: StateReader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_cost_claude(tmp_path, monkeypatch)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    settings = _mixed_task_settings()
    flow = build_flow(engine_env, settings, registry=_mixed_task_registry())

    await asyncio.wait_for(flow.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)

    runs = {run.role: run for run in engine_env.runs.for_task(task.id)}
    assert runs[Role.IMPLEMENTER].billing_mode == BILLING_MODE_API
    assert runs[Role.IMPLEMENTER].usage.cost_basis == COST_BASIS_BILLED
    assert runs[Role.IMPLEMENTER].usage.cost_usd == _IMPLEMENTER_BILLED_COST
    assert runs[Role.LEADER].billing_mode == BILLING_MODE_SUBSCRIPTION
    assert runs[Role.LEADER].usage.cost_basis == COST_BASIS_API_EQUIVALENT
    assert runs[Role.LEADER].usage.cost_usd == _LEADER_ESTIMATED_COST

    panel = UsagePanel(
        widget_context(reader, engine_env.project_root, engine_env.clock, settings=settings)
    )
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: _ESTIMATED_TOTAL in _panel_text(panel), timeout=_WAIT_SECONDS)
        text = _panel_text(panel)

    assert _BILLED_TOTAL in text
    assert _ESTIMATED_TOTAL in text
    assert f"${_IMPLEMENTER_BILLED_COST + _LEADER_ESTIMATED_COST:.2f}" not in text
