from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import (
    Agent,
    AgentContext,
    CheckResult,
    ModelCatalog,
    ModelChoice,
    assess_model_choice,
)
from shikiru.agents.registry import AgentRegistry
from shikiru.errors import ConfigError
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT, commit_all, init_repo

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


class _CatalogAgent:
    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog
        self.doctored_models: tuple[str, ...] = ()

    async def model_catalog(self) -> ModelCatalog:
        return self._catalog

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        self.doctored_models = tuple(models)
        return [CheckResult(name="binary", ok=True, detail="binary is present")]


def _registry_for(agent: object, name: str = "broken") -> AgentRegistry:
    registry = AgentRegistry()

    def _create(_context: AgentContext) -> Agent:
        return cast("Agent", agent)

    registry.register(name, _create)
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
    detectors = ProjectDetectors(
        tmp_path,
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        plugin_checks=checks,
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
    )

    with pytest.raises(ConfigError):
        await detectors.detect()


async def test_unexpected_plugin_doctor_errors_are_not_hidden() -> None:
    runner = SubprocessRunner()
    checks = PluginChecks(
        _registry_for(_BrokenAgent(RuntimeError("plugin implementation bug"))),
        runner,
        FakeClock(TASK_CREATED_AT),
    )

    with pytest.raises(RuntimeError):
        await checks.check("broken", {})


async def test_plugins_without_catalog_keep_checks_and_report_unknown_choices() -> None:
    runner = SubprocessRunner()
    checks = PluginChecks(
        _registry_for(_BrokenAgent()),
        runner,
        FakeClock(TASK_CREATED_AT),
    )

    result = await checks.check("broken", {}, include_model_catalog=True)

    assert result.error is None
    assert result.catalog is not None
    assert assess_model_choice("configured-model", None, result.catalog).supported is None
    assert not result.ok
    assert result.checks[0].detail == "binary is missing"


@pytest.mark.parametrize(
    ("catalog", "expected_models"),
    [
        (
            ModelCatalog(
                models=(
                    ModelChoice(model="default", efforts=None),
                    ModelChoice(model="gpt-6-luna", efforts=None),
                ),
                complete=True,
            ),
            ("gpt-6-luna",),
        ),
        (
            ModelCatalog(
                models=(ModelChoice(model="gpt-6-luna", efforts=None),),
                complete=True,
                supports_runtime_default=True,
            ),
            ("default", "gpt-6-luna"),
        ),
        (
            ModelCatalog(models=(ModelChoice(model="gpt-6-luna", efforts=None),)),
            ("default", "gpt-6-luna"),
        ),
    ],
)
async def test_runtime_default_reaches_plugin_doctor_only_when_catalog_declares_it(
    catalog: ModelCatalog, expected_models: tuple[str, ...]
) -> None:
    agent = _CatalogAgent(catalog)
    checks = PluginChecks(
        _registry_for(agent, name="catalogued"),
        SubprocessRunner(),
        FakeClock(TASK_CREATED_AT),
    )

    result = await checks.check(
        "catalogued", {}, ("default", "gpt-6-luna"), include_model_catalog=True
    )

    assert result.ok
    assert agent.doctored_models == expected_models


@pytest.mark.parametrize(
    ("name", "expected_args"),
    [("claude", ["--permission-mode", "bypassPermissions"]), ("codex", [])],
)
def test_built_in_plugin_settings_defaults_are_accepted_by_their_own_options(
    name: str, expected_args: list[str]
) -> None:
    checks = PluginChecks(AgentRegistry(), SubprocessRunner(), FakeClock(TASK_CREATED_AT))

    offered = checks.settings(name)

    assert offered.supported
    assert {setting.key for setting in offered.settings} == {
        "compact_at_pct",
        "billing_mode",
        "args",
    }
    values = {
        setting.key: setting.parse(setting.display())
        for setting in offered.settings
        if setting.parse(setting.display()) is not None
    }
    assert values["args"] == expected_args
    assert "billing_mode" not in values
    assert checks.options_rejection(name, values) is None
    rejection = checks.options_rejection(name, {**values, "unknown_option": 1})
    assert rejection is not None
    assert rejection.key == "unknown_option"


def test_plugin_without_settings_capability_is_reported_explicitly() -> None:
    registry = AgentRegistry()
    registry.register("broken", lambda _context: cast("Agent", _BrokenAgent()))
    checks = PluginChecks(registry, SubprocessRunner(), FakeClock(TASK_CREATED_AT))

    offered = checks.settings("broken")

    assert not offered.supported
    assert offered.settings == ()
    assert offered.error is not None
    assert "does not expose editable settings" in offered.error
