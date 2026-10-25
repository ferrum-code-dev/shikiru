"""Resolves agent names to plugins, by entry point or direct registration."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, cast

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

    from shikiru.agents.contracts import Agent, AgentContext, AgentFactory

AGENT_ENTRY_POINT_GROUP = "shikiru.agents"
PLUGIN_REINSTALL_HINT = (
    "a typo in the name gives this error too; if the name is right, reinstall from the checkout: "
    "uv tool install . --reinstall (editable: uv tool install --editable . --reinstall)"
)


class AgentPluginError(ConfigError):
    """A named agent plugin is missing, failed to load, or rejected its options."""


def _distribution_name(entry_point: EntryPoint) -> str:
    return entry_point.dist.name if entry_point.dist is not None else "unknown"


def load_unique_entry_point(
    group: str, name: str, error_type: type[ConfigError], kind: str
) -> object:
    """Load the sole `name` entry point of `group`, raising `error_type` under `kind`'s label.

    Raises when no entry point named `name` exists, when more than one distribution
    registers it (naming every distribution instead of silently picking the first), or
    when loading it raises.
    """
    matches = [entry_point for entry_point in entry_points(group=group) if entry_point.name == name]
    if not matches:
        message = f"no {kind} named {name!r} is registered or installed; {PLUGIN_REINSTALL_HINT}"
        raise error_type(message)
    if len(matches) > 1:
        distributions = ", ".join(_distribution_name(match) for match in matches)
        message = f"{kind} {name!r} is registered by more than one distribution: {distributions}"
        raise error_type(message)
    try:
        return matches[0].load()
    except Exception as error:
        message = f"{kind} {name!r} failed to load: {error}"
        raise error_type(message) from error


class AgentRegistry:
    """Builds a headless `Agent` by name, from a registered factory or entry point."""

    def __init__(self) -> None:
        """Start with no directly registered factories; entry points are resolved lazily."""
        self._factories: dict[str, AgentFactory] = {}

    def register(self, name: str, factory: AgentFactory) -> None:
        """Register `factory` under `name`, taking precedence over any entry point of that name."""
        self._factories[name] = factory

    def names(self) -> tuple[str, ...]:
        """Return every installed or directly registered agent name in stable order."""
        installed = (
            entry_point.name for entry_point in entry_points(group=AGENT_ENTRY_POINT_GROUP)
        )
        return tuple(sorted(set(installed) | self._factories.keys()))

    def create(self, name: str, context: AgentContext) -> Agent:
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
        loaded = load_unique_entry_point(
            AGENT_ENTRY_POINT_GROUP, name, AgentPluginError, "agent plugin"
        )
        return cast("AgentFactory", loaded)
