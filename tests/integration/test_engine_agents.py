"""`AgentPool`: rejects an unconfigured agent (04 §1-2)."""

from __future__ import annotations

import pytest

from shikiru.clock import SystemClock
from shikiru.engine.agents import AgentPool
from shikiru.errors import ConfigError
from shikiru.process import SubprocessRunner
from tests.support.engine import build_registry
from tests.support.factories import make_settings


def test_get_raises_for_an_agent_with_no_configured_table() -> None:
    pool = AgentPool(build_registry().create, SubprocessRunner(), make_settings(), SystemClock())

    with pytest.raises(ConfigError):
        pool.get("fake")
