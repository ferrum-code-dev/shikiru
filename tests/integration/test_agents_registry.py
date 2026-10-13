from __future__ import annotations

from typing import cast

import pytest

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import Agent, AgentContext
from shikiru.agents.registry import PLUGIN_REINSTALL_HINT, AgentPluginError, AgentRegistry
from shikiru.clock import SystemClock
from shikiru.process import SubprocessRunner
from tests.support.entry_points import ambiguous_entry_points


def _context() -> AgentContext:
    return AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock())


def test_create_names_the_plugin_when_none_is_registered_or_installed() -> None:
    registry = AgentRegistry()

    with pytest.raises(AgentPluginError) as excinfo:
        registry.create("no-such-agent", _context())

    assert PLUGIN_REINSTALL_HINT in str(excinfo.value)


def test_create_names_the_plugin_when_its_factory_raises() -> None:
    registry = AgentRegistry()

    def _broken_factory(_context: AgentContext) -> Agent:
        message = "bad option"
        raise RuntimeError(message)

    registry.register("broken", _broken_factory)

    with pytest.raises(AgentPluginError) as excinfo:
        registry.create("broken", _context())

    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_create_names_the_plugin_and_every_distribution_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = AgentRegistry()
    monkeypatch.setattr(
        registry_module, "entry_points", ambiguous_entry_points("dup", "dist-one", "dist-two")
    )

    with pytest.raises(AgentPluginError) as excinfo:
        registry.create("dup", _context())

    assert "dist-one" in str(excinfo.value)
    assert "dist-two" in str(excinfo.value)


def test_directly_registered_factory_takes_precedence_over_an_entry_point() -> None:
    registry = AgentRegistry()
    sentinel = cast("Agent", object())

    def _sentinel_factory(_context: AgentContext) -> Agent:
        return sentinel

    registry.register("sentinel", _sentinel_factory)

    assert registry.create("sentinel", _context()) is sentinel
