from __future__ import annotations

from typing import cast

import pytest

from shikiru.agents.contracts import AgentContext, InteractiveAgent
from shikiru.agents.registry import AgentPluginError, AgentRegistry
from shikiru.clock import SystemClock
from shikiru.process import SubprocessRunner


def _context() -> AgentContext:
    return AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock())


def test_create_names_the_plugin_when_none_is_registered_or_installed() -> None:
    registry = AgentRegistry()

    with pytest.raises(AgentPluginError, match="no-such-agent"):
        registry.create("no-such-agent", _context())


def test_create_names_the_plugin_when_its_factory_raises() -> None:
    registry = AgentRegistry()

    def _broken_factory(_context: AgentContext) -> InteractiveAgent:
        message = "bad option"
        raise RuntimeError(message)

    registry.register("broken", _broken_factory)

    with pytest.raises(AgentPluginError, match="broken") as excinfo:
        registry.create("broken", _context())

    assert "bad option" in str(excinfo.value)


def test_directly_registered_factory_takes_precedence_over_an_entry_point() -> None:
    registry = AgentRegistry()
    sentinel = cast("InteractiveAgent", object())

    def _sentinel_factory(_context: AgentContext) -> InteractiveAgent:
        return sentinel

    registry.register("sentinel", _sentinel_factory)

    assert registry.create("sentinel", _context()) is sentinel
