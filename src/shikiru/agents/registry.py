"""Resolves agent names to plugins, by entry point or direct registration (02 §2, §4)."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

    from shikiru.agents.contracts import AgentContext, AgentFactory, InteractiveAgent

AGENT_ENTRY_POINT_GROUP = "shikiru.agents"


class AgentPluginError(ConfigError):
    """A named agent plugin is missing, failed to load, or rejected its options."""


class AgentRegistry:
    """Builds an `InteractiveAgent` by name, from a registered factory or entry point."""

    def __init__(self) -> None:
        """Start with no directly registered factories; entry points are resolved lazily."""
        self._factories: dict[str, AgentFactory] = {}

    def register(self, name: str, factory: AgentFactory) -> None:
        """Register `factory` under `name`, taking precedence over any entry point of that name."""
        self._factories[name] = factory

    def create(self, name: str, context: AgentContext) -> InteractiveAgent:
        """Build the `name` agent from `context`, naming it in any `AgentPluginError` raised."""
        factory = self._factories.get(name)
        if factory is None:
            factory = self._load_entry_point(name)
        try:
            return factory(context)
        except Exception as error:
            message = f"agent plugin {name!r} failed: {error}"
            raise AgentPluginError(message) from error

    def _load_entry_point(self, name: str) -> AgentFactory:
        matches = [ep for ep in entry_points(group=AGENT_ENTRY_POINT_GROUP) if ep.name == name]
        if not matches:
            message = f"no agent plugin named {name!r} is registered or installed"
            raise AgentPluginError(message)
        if len(matches) > 1:
            distributions = ", ".join(self._distribution_name(match) for match in matches)
            message = (
                f"agent plugin {name!r} is registered by more than one distribution: "
                f"{distributions}"
            )
            raise AgentPluginError(message)
        try:
            return matches[0].load()
        except Exception as error:
            message = f"agent plugin {name!r} failed to load: {error}"
            raise AgentPluginError(message) from error

    @staticmethod
    def _distribution_name(entry_point: EntryPoint) -> str:
        return entry_point.dist.name if entry_point.dist is not None else "unknown"
