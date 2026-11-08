from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fake_agent.agent import create as create_fake_agent

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_PROVIDER_REPORTED,
    BILLING_SOURCE_UNKNOWN,
    Agent,
    AgentContext,
    BillingDeclaration,
    Rung,
    billing_declaration_for,
)
from shikiru.agents.registry import AgentRegistry
from shikiru.config.settings import AgentConfig
from shikiru.domain.tasks import Role
from shikiru.engine.agents import AgentPool
from shikiru.engine.sessions import SessionManager
from shikiru.errors import ConfigError
from shikiru.plugins.agents.claude.agent import create as create_claude_agent
from shikiru.plugins.agents.codex.agent import create as create_codex_agent
from shikiru.process import SubprocessRunner
from shikiru.workspace.roots import run_dir
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.engine import FAKE_RUNG
from tests.support.factories import make_settings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.agents.contracts import AgentFactory, CheckResult, HeadlessAgent, Turn, TurnSpec
    from tests.support.engine import EngineEnv

_DECLARATION_FACTORIES: tuple[AgentFactory, ...] = (
    create_claude_agent,
    create_codex_agent,
    create_fake_agent,
)
_EXPECTED_OPTIONS: tuple[tuple[dict[str, str], BillingDeclaration], ...] = (
    ({}, BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN)),
    (
        {"billing_mode": BILLING_MODE_API},
        BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG),
    ),
    (
        {"billing_mode": BILLING_MODE_SUBSCRIPTION},
        BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_OPERATOR_CONFIG),
    ),
    (
        {"billing_mode": BILLING_MODE_UNKNOWN},
        BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_OPERATOR_CONFIG),
    ),
)
_INVALID_BILLING_MODE = "enterprise"
_TEST_AGENT_NAME = "legacy"
_DECLARATION_BOUNDARY_CASES: tuple[tuple[BillingDeclaration, BillingDeclaration | None], ...] = (
    (
        BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG),
        BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_OPERATOR_CONFIG),
    ),
    (
        BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_PROVIDER_REPORTED),
        BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_PROVIDER_REPORTED),
    ),
    (
        BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN),
        BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN),
    ),
    (
        BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_OPERATOR_CONFIG),
        BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_OPERATOR_CONFIG),
    ),
    (BillingDeclaration(BILLING_MODE_API, BILLING_SOURCE_UNKNOWN), None),
    (BillingDeclaration(BILLING_MODE_SUBSCRIPTION, BILLING_SOURCE_UNKNOWN), None),
)


class _FakeAgentAdapter:
    def __init__(self, name: str, delegate: Agent) -> None:
        self.name = name
        self._delegate = delegate

    async def start_turn(self, spec: TurnSpec) -> Turn:
        return await self._delegate.start_turn(spec)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        return self._delegate.interactive_command(session_id, run_dir, rung)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        return await self._delegate.doctor(models)

    def headless(self) -> HeadlessAgent | None:
        return self._delegate.headless()


class _DeclaredFakeAgent(_FakeAgentAdapter):
    def __init__(self, name: str, delegate: Agent, declaration: BillingDeclaration) -> None:
        super().__init__(name, delegate)
        self._declaration = declaration

    @property
    def billing_declaration(self) -> BillingDeclaration:
        return self._declaration


def _create_legacy_fake_agent(context: AgentContext) -> Agent:
    return _FakeAgentAdapter(_TEST_AGENT_NAME, create_fake_agent(context))


@pytest.mark.parametrize("factory", _DECLARATION_FACTORIES)
def test_real_agent_factories_validate_and_normalize_billing_options(
    factory: AgentFactory,
) -> None:
    for options, expected in _EXPECTED_OPTIONS:
        agent = factory(AgentContext(options, SubprocessRunner(), FakeClock(LONG_AGO)))
        assert billing_declaration_for(agent) == expected

    with pytest.raises(ConfigError):
        factory(
            AgentContext(
                {"billing_mode": _INVALID_BILLING_MODE},
                SubprocessRunner(),
                FakeClock(LONG_AGO),
            )
        )


@pytest.mark.parametrize(("declaration", "expected"), _DECLARATION_BOUNDARY_CASES)
def test_registry_boundary_accepts_sourced_declarations_and_rejects_unattributed_modes(
    declaration: BillingDeclaration,
    expected: BillingDeclaration | None,
) -> None:
    registry = AgentRegistry()

    def create_configured_agent(context: AgentContext) -> Agent:
        return _DeclaredFakeAgent(
            _TEST_AGENT_NAME,
            create_fake_agent(context),
            declaration,
        )

    registry.register(_TEST_AGENT_NAME, create_configured_agent)
    settings = make_settings(agents={_TEST_AGENT_NAME: AgentConfig()})
    agents = AgentPool(
        registry.create,
        SubprocessRunner(),
        settings,
        FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )

    if expected is None:
        with pytest.raises(ConfigError):
            agents.billing_declaration(_TEST_AGENT_NAME)
        return

    actual = agents.billing_declaration(_TEST_AGENT_NAME)

    assert actual == expected


async def test_legacy_agent_session_keeps_unknown_metadata_and_fake_behavior(
    engine_env: EngineEnv,
) -> None:
    registry = AgentRegistry()
    registry.register(_TEST_AGENT_NAME, _create_legacy_fake_agent)
    rung = Rung(agent=_TEST_AGENT_NAME, model=FAKE_RUNG.model, effort=FAKE_RUNG.effort)
    settings = make_settings(
        implementer_ladder=[rung],
        agents={_TEST_AGENT_NAME: AgentConfig()},
    )
    agents = AgentPool(
        registry.create,
        SubprocessRunner(),
        settings,
        engine_env.clock,
    )
    sessions = SessionManager(
        runs=engine_env.runs,
        agents=agents,
        project_root=engine_env.project_root,
        settings=settings,
    )
    task = await engine_env.build_task()
    selection = sessions.session_for(task, Role.IMPLEMENTER)
    sessions.record_session_started(
        run_id=selection.run.id,
        agent=agents.get(_TEST_AGENT_NAME),
        session_id="legacy-session",
        run_dir=run_dir(engine_env.project_root, task.id),
        rung=selection.rung,
    )
    [stored] = engine_env.runs.for_task(task.id)

    assert stored.billing_mode == BILLING_MODE_UNKNOWN
    assert stored.billing_source == BILLING_SOURCE_UNKNOWN
    assert stored.session_id == "legacy-session"
    assert stored.resume_command is not None
