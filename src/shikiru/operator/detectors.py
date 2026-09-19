"""Project detection shared by non-interactive init and the future wizard (06 §3)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, cast

from shikiru.agents.contracts import AgentContext, AgentFactory
from shikiru.agents.registry import AGENT_ENTRY_POINT_GROUP, load_unique_entry_point
from shikiru.config.loader import SHIKIRU_DIR_NAME
from shikiru.errors import ConfigError, ShikiruError
from shikiru.workspace.repo_paths import repo_dir

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner
    from shikiru.workspace.git import Git

MAX_SCAN_DEPTH = 4
TRACKER_DIRECTORY = "tasks"
TRACKER_NAMES = ("plan", "todo")
CACHE_NAMES = (
    ".venv",
    "node_modules",
    "target",
    ".gradle",
    ".pytest_cache",
)
EXCLUDE_LINE = f"/{SHIKIRU_DIR_NAME}/"
DEFAULT_MODEL = "default"
EXCLUDED_DIRECTORIES = frozenset({SHIKIRU_DIR_NAME, ".git"})


@dataclass(frozen=True, slots=True)
class DetectedRepo:
    """A repository found under a project root, with its current base branch."""

    path: str
    base: str
    caches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DetectedProject:
    """All filesystem and plugin facts used to write a default project config."""

    name: str
    repos: tuple[DetectedRepo, ...]
    trackers: tuple[str, ...]
    agents: tuple[str, ...]


class ProjectDetectors:
    """Detect repositories, trackers, installed agents and caches for `init`."""

    def __init__(self, root: Path, *, git: Git, runner: SubprocessRunner, clock: Clock) -> None:
        """Inspect `root` through injected git, process and clock boundaries."""
        self._root = root.resolve()
        self._runner = runner
        self._git = git
        self._clock = clock

    async def detect(self) -> DetectedProject:
        """Return deterministic project facts, or an actionable config error."""
        repos = await self._repositories()
        if not repos:
            message = f"no git repository found under {self._root}"
            raise ConfigError(message)
        agents = await self._agents()
        if not agents:
            message = "no shikiru.agents plugin is installed"
            raise ConfigError(message)
        return DetectedProject(
            name=self._root.name,
            repos=tuple(repos),
            trackers=tuple(self._trackers()),
            agents=agents,
        )

    async def initialize(self) -> Path:
        """Detect facts, write config and exclude `.shikiru/` in each repository."""
        config_path = self._root / SHIKIRU_DIR_NAME / "config.toml"
        if await asyncio.to_thread(config_path.is_file):
            return config_path
        project = await self.detect()
        await asyncio.to_thread(_write_config, config_path, render_config(project))
        for repo in project.repos:
            await self._git.append_to_exclude(repo_dir(self._root, repo.path), EXCLUDE_LINE)
        return config_path

    async def _repositories(self) -> list[DetectedRepo]:
        if _is_git_root(self._root):
            base = await self._git.current_branch(self._root)
            return [
                DetectedRepo(path=".", base=base, caches=tuple(await self._caches_for(self._root)))
            ]
        candidates = [
            path for path in _directories_at_depth(self._root, MAX_SCAN_DEPTH) if _is_git_root(path)
        ]
        candidates.sort(key=lambda path: path.relative_to(self._root).as_posix())
        return [
            DetectedRepo(
                path=path.relative_to(self._root).as_posix(),
                base=await self._git.current_branch(path),
                caches=tuple(await self._caches_for(path)),
            )
            for path in candidates
        ]

    def _trackers(self) -> list[str]:
        candidates: set[str] = set()
        for path in _files_at_depth(self._root, MAX_SCAN_DEPTH):
            relative = path.relative_to(self._root).as_posix()
            name = path.stem.lower()
            if (
                path.parent.name == TRACKER_DIRECTORY
                or name.startswith(TRACKER_NAMES)
                or name.endswith(TRACKER_NAMES)
            ) and path.suffix.lower() == ".md":
                candidates.add(relative)
        return sorted(candidates)

    async def _caches_for(self, root: Path) -> list[str]:
        ignored = await self._git.ignored_paths(root)
        return sorted(path.name for path in ignored if path.name in CACHE_NAMES)

    async def _agents(self) -> tuple[str, ...]:
        names: list[str] = []
        for entry in sorted(entry_points(group="shikiru.agents"), key=lambda item: item.name):
            try:
                factory = load_unique_entry_point(
                    AGENT_ENTRY_POINT_GROUP, entry.name, ConfigError, "agent plugin"
                )
                agent_factory = cast("AgentFactory", factory)
                agent = agent_factory(
                    AgentContext(options={}, runner=self._runner, clock=self._clock)
                )
                checks = await agent.doctor()
            except ShikiruError, OSError:
                continue
            if all(check.ok for check in checks):
                names.append(entry.name)
        return tuple(names)


def render_config(project: DetectedProject) -> str:
    """Render a complete default config from detected project facts."""
    agent = project.agents[0]
    lines = [
        "[project]",
        f"name = {json.dumps(project.name)}",
        f"trackers = {json.dumps(list(project.trackers))}",
        "",
    ]
    for repo in project.repos:
        lines.extend(
            (
                "[[repos]]",
                f"path = {json.dumps(repo.path)}",
                f"base = {json.dumps(repo.base)}",
                f"caches = {json.dumps(list(repo.caches))}",
                'remote = "origin"',
                "",
            )
        )
    for name in project.agents:
        lines.extend(
            (
                f"[agents.{name}]",
                "",
            )
        )
    rung = f"{{ agent = {json.dumps(agent)}, model = {json.dumps(DEFAULT_MODEL)} }}"
    lines.extend(
        (
            "[roles.leader]",
            f"ladder = [{rung}]",
            "",
            "[roles.implementer]",
            f"ladder = [{rung}]",
            "",
            "[roles.helper]",
            f"ladder = [{rung}]",
            "",
        )
    )
    return "\n".join(lines)


async def initialize_project(detectors: ProjectDetectors) -> Path:
    """Detect, write config and exclude `.shikiru/` in each detected repository."""
    return await detectors.initialize()


def _write_config(config_path: Path, contents: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(contents)


def _is_git_root(path: Path) -> bool:
    return (path / ".git").exists()


def _directories_at_depth(root: Path, max_depth: int) -> list[Path]:
    directories, _ = _walk_at_depth(root, max_depth)
    return directories


def _files_at_depth(root: Path, max_depth: int) -> list[Path]:
    _, files = _walk_at_depth(root, max_depth)
    return files


def _walk_at_depth(root: Path, max_depth: int) -> tuple[list[Path], list[Path]]:
    directories = [root]
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
                    directories.append(path)
                    pending.append((path, entry_depth))
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
    return directories, files
