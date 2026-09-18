"""`AgentPool`: builds and caches one `InteractiveAgent` per name (04 §1-2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import AgentContext
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from shikiru.agents.contracts import InteractiveAgent
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.process import SubprocessRunner


class AgentPool:
    """Resolves a rung's agent name to its `InteractiveAgent`, built once and cached."""

    def __init__(
        self, registry: AgentRegistry, runner: SubprocessRunner, settings: Settings, clock: Clock
    ) -> None:
        """Build agents through `registry`/`runner`, from `settings.agents`, timed by `clock`."""
        self._registry = registry
        self._runner = runner
        self._settings = settings
        self._clock = clock
        self._agents: dict[str, InteractiveAgent] = {}

    def get(self, name: str) -> InteractiveAgent:
        """Return `name`'s `InteractiveAgent`, building and caching it on first use.

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
        agent = self._registry.create(name, context)
        self._agents[name] = agent
        return agent
