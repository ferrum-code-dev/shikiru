"""Muse setup during `init`: the offered agent, the written config and the doctor result."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fake_agent.agent import create as create_fake_agent

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import RUNTIME_DEFAULT_MODEL, Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.config.loader import load_settings
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.doctor import Doctor
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.plugins.agents.muse.agent import create as create_muse
from shikiru.plugins.agents.muse.options import API_KEY_ENV_VAR
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.integration.dashboard_wizard_support import (
    git_project,
    no_entry_points,
    private_doctor_runtime,
)
from tests.integration.fake_muse import install_fake_muse, remove_muse_from_path
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from shikiru.agents.contracts import AgentFactory

pytestmark = pytest.mark.asyncio

_SYNTHETIC_API_KEY = "synthetic-test-key"
_FAKE = "fake"
_MUSE = "muse"
_CONFIG_DIRECTORY = ".shikiru"
_CONFIG_FILENAME = "config.toml"
_MUSE_TABLE = "[agents.muse]"


@pytest.fixture(autouse=True)
def _only_registered_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)


def _registry(agents: Mapping[str, AgentFactory]) -> AgentRegistry:
    registry = AgentRegistry()
    for name, factory in agents.items():
        registry.register(name, factory)
    return registry


def _detectors(root: Path, agents: Mapping[str, AgentFactory]) -> ProjectDetectors:
    runner = SubprocessRunner()
    clock = FakeClock(LONG_AGO)
    return ProjectDetectors(
        root,
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        plugin_checks=PluginChecks(_registry(agents), runner, clock),
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
    )


def _doctor(root: Path, agents: Mapping[str, AgentFactory]) -> Doctor:
    runner = SubprocessRunner()
    clock = FakeClock(LONG_AGO)
    registry = _registry(agents)
    return Doctor(
        root,
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        runner=runner,
        clock=clock,
        plugin_checks=PluginChecks(registry, runner, clock),
        probe=AgentProbe(registry, runner, clock),
    )


async def test_muse_is_offered_when_installed_and_logged_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await git_project(tmp_path)
    install_fake_muse(tmp_path, monkeypatch)

    project = await _detectors(tmp_path, {_MUSE: create_muse}).detect()

    assert project.agents == (_MUSE,)


async def test_muse_is_offered_when_only_the_api_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await git_project(tmp_path)
    install_fake_muse(tmp_path, monkeypatch, logged_in=False)
    monkeypatch.setenv(API_KEY_ENV_VAR, _SYNTHETIC_API_KEY)

    project = await _detectors(tmp_path, {_MUSE: create_muse}).detect()

    assert project.agents == (_MUSE,)


@pytest.mark.parametrize("scenario", ["missing", "logged_out"])
async def test_muse_is_absent_when_missing_or_logged_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    await git_project(tmp_path)
    if scenario == "missing":
        remove_muse_from_path(monkeypatch)
    else:
        install_fake_muse(tmp_path, monkeypatch, logged_in=False)

    project = await _detectors(tmp_path, {_MUSE: create_muse, _FAKE: create_fake_agent}).detect()

    assert project.agents == (_FAKE,)


async def test_initialized_config_declares_muse_on_the_runtime_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await git_project(tmp_path)
    install_fake_muse(tmp_path, monkeypatch)

    config_path = await _detectors(tmp_path, {_MUSE: create_muse}).initialize()

    assert _MUSE_TABLE in config_path.read_text(encoding="utf-8")
    assert config_path == tmp_path / _CONFIG_DIRECTORY / _CONFIG_FILENAME
    settings = load_settings(tmp_path)
    assert Rung(agent=_MUSE, model=RUNTIME_DEFAULT_MODEL) in settings.ladder_rungs()


async def test_doctor_accepts_the_runtime_default_for_muse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await git_project(tmp_path)
    install_fake_muse(tmp_path, monkeypatch)
    await _detectors(tmp_path, {_MUSE: create_muse}).initialize()
    private_doctor_runtime(monkeypatch, tmp_path)

    report = await _doctor(tmp_path, {_MUSE: create_muse}).run()

    muse_checks = [check for check in report.checks if _MUSE in check.name]
    assert muse_checks
    assert all(check.ok for check in muse_checks), [
        (check.name, check.detail) for check in muse_checks if not check.ok
    ]
