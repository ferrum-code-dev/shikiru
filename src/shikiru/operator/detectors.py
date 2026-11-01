"""Project detection and config rendering shared by non-interactive init and the wizard."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import ModelCatalog, Rung, short_rung_text
from shikiru.config.loader import validate_project_config_text
from shikiru.errors import ConfigError, ProjectNotRepositoryRootError, RepositoryHasNoCommitsError
from shikiru.paths import EXCLUDE_LINE, SHIKIRU_DIR_NAME, project_config_path
from shikiru.private import create_private_text, make_private_dir

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

    from shikiru.agents.settings import AgentSettings
    from shikiru.config.settings import ComplexityName
    from shikiru.operator.doctor import DoctorReport
    from shikiru.operator.plugin_checks import AgentCapabilityResult, PluginChecks
    from shikiru.workspace.caches import CacheCloner
    from shikiru.workspace.git import Git, LineChangeStats

MAX_SCAN_DEPTH = 4
TRACKER_DIRECTORY = "tasks"
TRACKER_NAMES = ("plan", "todo")
MARKDOWN_SUFFIX = ".md"
CACHE_NAMES = (
    ".venv",
    "node_modules",
    "target",
    ".gradle",
    ".pytest_cache",
)
DEFAULT_MODEL = "default"
LEADER_ROLE = "leader"
IMPLEMENTER_ROLE = "implementer"
HELPER_ROLE = "helper"
ROLE_NAMES = (LEADER_ROLE, IMPLEMENTER_ROLE, HELPER_ROLE)
COMPLEXITY_NAMES: tuple[ComplexityName, ...] = ("low", "normal", "high")
EXCLUDED_DIRECTORIES = frozenset({SHIKIRU_DIR_NAME, ".git"})


def not_repository_root(root: Path) -> str:
    """Explain why `root` cannot be a Shikiru project: it is not a git repository root."""
    return (
        f"{root} is not a git repository root; Shikiru supports one repository per project, "
        "so run it from the repository's top-level directory"
    )


@dataclass(frozen=True, slots=True)
class DetectedProject:
    """All filesystem and plugin facts used to write a default project config."""

    name: str
    base: str
    worktree_clean: bool
    caches: tuple[str, ...]
    trackers: tuple[str, ...]
    agents: tuple[str, ...]
    agent_settings: tuple[AgentSettings, ...] = ()


@dataclass(frozen=True, slots=True)
class InitWizardResult:
    """The config path and final Doctor result returned when the wizard is complete."""

    config_path: Path
    doctor_report: DoctorReport | None
    doctor_error: str | None = None


@dataclass(frozen=True, slots=True)
class OrchestrationChoices:
    """The orchestration values the wizard writes to the project config."""

    max_parallel: int
    max_iterations: int
    integration: str
    push_interval_seconds: int
    max_quota_wait: int
    stall: int
    tool: int
    max_cpu_pct: int
    min_free_ram_gb: float


class ProjectDetectors:
    """Detect the repository base, trackers, installed agents and caches for `init`."""

    def __init__(
        self,
        root: Path,
        *,
        git: Git,
        plugin_checks: PluginChecks,
        cache_cloner: CacheCloner,
    ) -> None:
        """Inspect `root` through injected git and agent-plugin check boundaries."""
        self._root = root.resolve()
        self._git = git
        self._plugin_checks = plugin_checks
        self._cache_cloner = cache_cloner

    async def detect(self) -> DetectedProject:
        """Return deterministic project facts, or an actionable config error."""
        await self._validate_root()
        return await self._detect_project()

    async def _detect_project(self) -> DetectedProject:
        agents = await self._agents()
        if not agents:
            message = "no shikiru.agents plugin is installed"
            raise ConfigError(message)
        base = await self._git.current_branch(self._root)
        worktree_clean = not await self._git.status_porcelain(self._root)
        return DetectedProject(
            name=self._root.name,
            base=base,
            worktree_clean=worktree_clean,
            caches=tuple(await self._caches()),
            trackers=tuple(self._trackers()),
            agents=agents,
            agent_settings=tuple(self._plugin_checks.settings(name) for name in agents),
        )

    async def initialize(self) -> Path:
        """Detect facts, write config and exclude `.shikiru/` in the repository."""
        await self._validate_root()
        config_path = project_config_path(self._root)
        if await asyncio.to_thread(config_path.is_file):
            return config_path
        project = await self._detect_project()
        await self._write_and_exclude(config_path, render_config(project))
        return config_path

    async def initialize_config(
        self,
        *,
        trackers: Sequence[str],
        caches: Sequence[str],
        ladders: Mapping[str, Sequence[Rung]],
        complexity: Mapping[ComplexityName, Sequence[Rung]] | None = None,
        orchestration: OrchestrationChoices,
        agent_options: Mapping[str, Mapping[str, object]] | None = None,
    ) -> Path:
        """Write the wizard's validated configuration and exclude `.shikiru/`."""
        await self._validate_root()
        project = await self._detect_project()
        config_path = project_config_path(self._root)
        contents = render_config(
            project,
            trackers=trackers,
            caches=caches,
            ladders=ladders,
            complexity=complexity,
            orchestration=orchestration,
            agent_options=agent_options,
        )
        validate_project_config_text(contents)
        await self._write_and_exclude(config_path, contents)
        return config_path

    async def check_cow_support(self) -> bool:
        """Probe copy-on-write support using disposable files on the project filesystem."""
        return await self._cache_cloner.check_support(self._root)

    async def working_tree_line_changes(self) -> LineChangeStats:
        """Return tracked and untracked working-tree change totals for the project repository."""
        return await self._git.working_tree_line_changes(self._root)

    async def model_catalog(self, name: str, options: Mapping[str, Any]) -> ModelCatalog | None:
        """Return an agent's optional authoritative choices through the injected plugin boundary."""
        return await self._plugin_checks.model_catalog(name, options)

    def agent_options_error(self, name: str, options: Mapping[str, Any]) -> str | None:
        """Return the plugin's rejection of `options` for `name`, or `None` when accepted."""
        return self._plugin_checks.validation_error(name, options)

    def agent_capabilities(self, name: str, options: Mapping[str, Any]) -> AgentCapabilityResult:
        """Return generic optional capabilities through the injected plugin boundary."""
        return self._plugin_checks.capabilities(name, options)

    async def _validate_root(self) -> None:
        repository_root = await self._git.repository_root(self._root)
        if repository_root != self._root:
            raise ProjectNotRepositoryRootError(
                not_repository_root(self._root),
                project_root=self._root,
                repository_root=repository_root,
            )
        if not await self._git.has_commit(self._root):
            message = (
                f"{self._root} is a git repository without commits; create an initial commit, "
                "then run shikiru init"
            )
            raise RepositoryHasNoCommitsError(message, project_root=self._root)

    async def _write_and_exclude(self, config_path: Path, contents: str) -> None:
        try:
            await asyncio.to_thread(_write_config, config_path, contents)
        except FileExistsError as error:
            message = f"{config_path} already exists; refusing to overwrite it"
            raise ConfigError(message) from error
        await self._git.append_to_exclude(self._root, EXCLUDE_LINE)

    def _trackers(self) -> list[str]:
        candidates: set[str] = set()
        for path in _files_at_depth(self._root, MAX_SCAN_DEPTH):
            relative = path.relative_to(self._root).as_posix()
            name = path.stem.lower()
            if (
                path.parent.name == TRACKER_DIRECTORY
                or name.startswith(TRACKER_NAMES)
                or name.endswith(TRACKER_NAMES)
            ) and path.suffix.lower() == MARKDOWN_SUFFIX:
                candidates.add(relative)
        return sorted(candidates)

    async def _caches(self) -> list[str]:
        ignored = await self._git.ignored_paths(self._root)
        return sorted(
            path.relative_to(self._root).as_posix() for path in ignored if path.name in CACHE_NAMES
        )

    async def _agents(self) -> tuple[str, ...]:
        results = [
            await self._plugin_checks.check(name, {}) for name in self._plugin_checks.names()
        ]
        names = tuple(result.name for result in results if result.ok)
        if names:
            return names
        if not results:
            message = "no shikiru.agents plugin is installed"
        else:
            details = "; ".join(f"{result.name}: {result.detail}" for result in results)
            message = f"no installed agent plugin passed doctor(): {details}"
        raise ConfigError(message)


def render_config(
    project: DetectedProject,
    *,
    trackers: Sequence[str] | None = None,
    caches: Sequence[str] | None = None,
    ladders: Mapping[str, Sequence[Rung]] | None = None,
    complexity: Mapping[ComplexityName, Sequence[Rung]] | None = None,
    orchestration: OrchestrationChoices | None = None,
    agent_options: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """Render a complete default config from detected project facts."""
    agent = project.agents[0]
    lines = [
        "[project]",
        f"name = {json.dumps(project.name)}",
        f"trackers = {json.dumps(list(project.trackers if trackers is None else trackers))}",
        "",
    ]
    lines.extend(
        (
            "[repo]",
            f"base = {json.dumps(project.base)}",
            f"caches = {json.dumps(list(project.caches if caches is None else caches))}",
            "",
        )
    )
    for name in project.agents:
        lines.append(f"[agents.{name}]")
        options = (agent_options or {}).get(name, {})
        if options:
            lines.append("")
            lines.append(f"[agents.{name}.options]")
            lines.extend(f"{key} = {json.dumps(value)}" for key, value in options.items())
        lines.append("")
    selected_ladders = (
        ladders
        if ladders is not None
        else {role: (Rung(agent=agent, model=DEFAULT_MODEL),) for role in ROLE_NAMES}
    )
    for role in ROLE_NAMES:
        lines.append(f"[roles.{role}]")
        lines.append(f"ladder = {_render_ladder(selected_ladders[role])}")
        lines.append("")
    for name in COMPLEXITY_NAMES:
        rungs = complexity.get(name) if complexity is not None else None
        if rungs is not None:
            lines.append(f"[roles.{IMPLEMENTER_ROLE}.complexity.{name}]")
            lines.append(f"ladder = {_render_ladder(rungs)}")
            lines.append("")
    if orchestration is not None:
        lines.extend(
            (
                "[orchestration]",
                f"max_parallel = {orchestration.max_parallel}",
                f"max_iterations = {orchestration.max_iterations}",
                f"integration = {json.dumps(orchestration.integration)}",
                f"push_interval_seconds = {orchestration.push_interval_seconds}",
                "",
                "[orchestration.timeouts]",
                f"stall = {orchestration.stall}",
                f"tool = {orchestration.tool}",
                f"max_quota_wait = {orchestration.max_quota_wait}",
                "",
                "[orchestration.resources]",
                f"max_cpu_pct = {orchestration.max_cpu_pct}",
                f"min_free_ram_gb = {orchestration.min_free_ram_gb}",
                "",
            )
        )
    return "\n".join(lines)


def _render_ladder(rungs: Sequence[Rung]) -> str:
    rung_texts = [_render_rung(rung) for rung in rungs]
    return f"[{', '.join(rung_texts)}]"


def _render_rung(rung: Rung) -> str:
    short = short_rung_text(rung)
    if short is not None:
        return json.dumps(short)
    return (
        "{ "
        + ", ".join(
            value
            for value in (
                f"agent = {json.dumps(rung.agent)}",
                f"model = {json.dumps(rung.model)}",
                None if rung.effort is None else f"effort = {json.dumps(rung.effort)}",
            )
            if value is not None
        )
        + " }"
    )


def _write_config(config_path: Path, contents: str) -> None:
    make_private_dir(config_path.parent)
    create_private_text(config_path, contents)


def _files_at_depth(root: Path, max_depth: int) -> list[Path]:
    files: list[Path] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.name in EXCLUDED_DIRECTORIES:
                    continue
                entry_depth = depth + 1
                if entry_depth > max_depth:
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append((path, entry_depth))
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
    return files
