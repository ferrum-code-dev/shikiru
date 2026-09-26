from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import Agent, AgentContext, CheckResult
from shikiru.agents.registry import AgentRegistry
from shikiru.errors import ConfigError
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class _BrokenAgent:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        if self._error is not None:
            raise self._error
        return [CheckResult(name="binary", ok=False, detail="binary is missing")]


def _registry_for(agent: _BrokenAgent) -> AgentRegistry:
    registry = AgentRegistry()

    def _create(_context: AgentContext) -> Agent:
        return cast("Agent", agent)

    registry.register("broken", _create)
    return registry


async def test_init_reports_named_plugin_failures_when_no_agent_passes_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_entry_points(*, group: str) -> list[object]:
        del group
        return []

    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    runner = SubprocessRunner()
    await init_repo(runner, tmp_path)
    await commit_all(runner, tmp_path, "initial", allow_empty=True)
    checks = PluginChecks(_registry_for(_BrokenAgent()), runner, FakeClock(TASK_CREATED_AT))
    detectors = ProjectDetectors(tmp_path, git=Git(runner), plugin_checks=checks)

    with pytest.raises(ConfigError, match="broken: binary: binary is missing"):
        await detectors.detect()


async def test_unexpected_plugin_doctor_errors_are_not_hidden() -> None:
    runner = SubprocessRunner()
    checks = PluginChecks(
        _registry_for(_BrokenAgent(RuntimeError("plugin implementation bug"))),
        runner,
        FakeClock(TASK_CREATED_AT),
    )

    with pytest.raises(RuntimeError, match="plugin implementation bug"):
        await checks.check("broken", {})
