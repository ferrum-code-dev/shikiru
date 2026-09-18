"""`AgentPool`: builds and caches agents, and rejects an unconfigured one (04 §1-2)."""

from __future__ import annotations

import pytest

from shikiru.clock import SystemClock
from shikiru.engine.agents import AgentPool
from shikiru.errors import ConfigError
from shikiru.process import SubprocessRunner
from tests.support.engine import build_registry, fake_agent_settings
from tests.support.factories import make_settings


def test_get_raises_for_an_agent_with_no_configured_table() -> None:
    pool = AgentPool(build_registry(), SubprocessRunner(), make_settings(), SystemClock())

    with pytest.raises(ConfigError):
        pool.get("fake")


def test_get_caches_the_built_agent() -> None:
    settings = make_settings(agents=fake_agent_settings())
    pool = AgentPool(build_registry(), SubprocessRunner(), settings, SystemClock())

    first = pool.get("fake")
    second = pool.get("fake")

    assert first is second
