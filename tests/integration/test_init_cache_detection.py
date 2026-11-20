from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import Agent, AgentContext, CheckResult
from shikiru.agents.registry import AgentRegistry
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.integration.dashboard_wizard_support import no_entry_points
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT, commit_all, init_repo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_AGENT = "passing"


class _PassingAgent:
    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="binary is present")]


def _create_passing_agent(_context: AgentContext) -> Agent:
    return cast("Agent", _PassingAgent())


@pytest.fixture(autouse=True)
def _only_registered_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)


async def _detected_caches(root: Path) -> tuple[str, ...]:
    runner = SubprocessRunner()
    registry = AgentRegistry()
    registry.register(_AGENT, _create_passing_agent)
    detectors = ProjectDetectors(
        root,
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        plugin_checks=PluginChecks(registry, runner, FakeClock(TASK_CREATED_AT)),
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
    )
    return (await detectors.detect()).caches


@pytest.mark.parametrize(
    ("folder", "git_ignored", "is_directory", "expected"),
    [
        (".mypy_cache", True, True, True),
        (".ruff_cache", True, True, True),
        (".tox", True, True, True),
        (".next", True, True, True),
        (".nuxt", True, True, True),
        (".dart_tool", True, True, True),
        ("vendor", True, True, True),
        (".mypy_cache", False, True, False),
        (".next", False, True, False),
        ("build", True, True, False),
        ("vendor", True, False, False),
    ],
)
async def test_init_suggests_only_git_ignored_cache_folders(
    tmp_path: Path, folder: str, git_ignored: bool, is_directory: bool, expected: bool
) -> None:
    runner = SubprocessRunner()
    await init_repo(runner, tmp_path)
    if git_ignored:
        pattern = f"{folder}/" if is_directory else folder
        (tmp_path / ".gitignore").write_text(f"{pattern}\n", encoding="utf-8")
    path = tmp_path / folder
    if is_directory:
        path.mkdir()
        (path / "entry").write_text("cached\n", encoding="utf-8")
    else:
        path.write_text("cached\n", encoding="utf-8")
    await commit_all(runner, tmp_path, "initial", allow_empty=True)

    caches = await _detected_caches(tmp_path)

    assert caches == ((folder,) if expected else ())
