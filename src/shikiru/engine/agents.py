"""`AgentPool`: builds and caches one headless `Agent` per name (04 §1-2).

Builds through an injected `create` callable rather than an `AgentRegistry` directly, so the
engine imports only `agents.contracts` (`AGENTS.md`'s boundary rule): callers pass
`AgentRegistry.create` bound, but the engine itself never imports `agents.registry`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import AgentContext, BillingDeclaration, billing_declaration_for
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.agents.contracts import Agent
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.process import SubprocessRunner


class AgentPool:
    """Resolves a rung's agent name to its `Agent`, built once and cached."""

    def __init__(
        self,
        create: Callable[[str, AgentContext], Agent],
        runner: SubprocessRunner,
        settings: Settings,
        clock: Clock,
    ) -> None:
        """Build agents by calling `create(name, context)`, timed by `clock`."""
        self._create = create
        self._runner = runner
        self._settings = settings
        self._clock = clock
        self._agents: dict[str, Agent] = {}

    def get(self, name: str) -> Agent:
        """Return `name`'s headless `Agent`, building and caching it on first use.

        Raises `ConfigError` if `name` has no `[agents.<name>]` table: a rung naming an agent
        the project never configured is a configuration mistake, not a default to paper over.
        """
        cached = self._agents.get(name)
        if cached is not None:
            return cached
        config = self._settings.agents.get(name)
        if config is None:
            message = f"agent {name!r} is used by a rung but has no [agents.{name}] table"
            raise ConfigError(message)
        context = AgentContext(options=config.options, runner=self._runner, clock=self._clock)
        agent = self._create(name, context)
        self._agents[name] = agent
        return agent

    def billing_declaration(self, name: str) -> BillingDeclaration:
        """Return the configured plugin's optional declaration through the contract boundary."""
        return billing_declaration_for(self.get(name))
