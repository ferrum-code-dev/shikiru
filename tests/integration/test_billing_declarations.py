from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent

from shikiru.agents.contracts import (
    BILLING_MODE_API,
    BILLING_MODE_SUBSCRIPTION,
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_OPERATOR_CONFIG,
    BILLING_SOURCE_UNKNOWN,
    Agent,
    AgentContext,
    BillingDeclaration,
    billing_declaration_for,
)
from shikiru.agents.registry import AgentRegistry
from shikiru.clock import SystemClock
from shikiru.config.settings import AgentConfig
from shikiru.engine.agents import AgentPool
from shikiru.errors import ConfigError
from shikiru.plugins.agents.claude.agent import create as create_claude_agent
from shikiru.plugins.agents.codex.agent import create as create_codex_agent
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.factories import make_settings

if TYPE_CHECKING:
    from shikiru.agents.contracts import AgentFactory

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


@pytest.mark.parametrize("factory", _DECLARATION_FACTORIES)
def test_real_agent_factories_validate_and_normalize_billing_options(
    factory: AgentFactory,
) -> None:
    for options, expected in _EXPECTED_OPTIONS:
        agent = factory(AgentContext(options, SubprocessRunner(), SystemClock()))
        assert billing_declaration_for(agent) == expected

    with pytest.raises(ConfigError):
        factory(
            AgentContext(
                {"billing_mode": _INVALID_BILLING_MODE},
                SubprocessRunner(),
                SystemClock(),
            )
        )


def test_old_agent_without_the_optional_capability_remains_unknown() -> None:
    registry = AgentRegistry()
    old_agent = cast("Agent", object())

    def create_old_agent(_context: AgentContext) -> Agent:
        return old_agent

    registry.register(_TEST_AGENT_NAME, create_old_agent)
    settings = make_settings(agents={_TEST_AGENT_NAME: AgentConfig()})
    agents = AgentPool(
        registry.create,
        SubprocessRunner(),
        settings,
        FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )

    declaration = agents.billing_declaration(_TEST_AGENT_NAME)

    assert declaration == BillingDeclaration(BILLING_MODE_UNKNOWN, BILLING_SOURCE_UNKNOWN)
