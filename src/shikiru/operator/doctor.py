"""Environment and configured agent diagnostics for the operator CLI."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from shikiru.agents.contracts import (
    DOCTOR_TIMEOUT_SECONDS,
    CheckResult,
    ModelCatalog,
    assess_model_choice,
)
from shikiru.config.loader import load_settings
from shikiru.domain.caches import (
    CacheEntryEffect,
    detected_caches,
    ineffective_cache_entry,
    uncached_detections,
)
from shikiru.domain.versions import Version, version_shortfall
from shikiru.errors import ConfigError, ShikiruError
from shikiru.operator.detectors import not_repository_root
from shikiru.paths import engine_output_path, project_config_path, state_db_path
from shikiru.store.database import Database
from shikiru.tmux.client import Tmux, private_socket_name
from shikiru.tmux.session import DETACHED_SIZE
from shikiru.workspace.caches import (
    CACHE_COW_UNSUPPORTED_WARNING,
    CacheCloner,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.operator.plugin_checks import PluginChecks
    from shikiru.operator.probe import AgentProbe
    from shikiru.process import ProcessResult, SubprocessRunner
    from shikiru.workspace.git import Git


SESSION_SUFFIX_LENGTH = 8
TMUX_VERSION_ARGS = ("tmux", "-V")
TMUX_MINIMUM_VERSION = (3, 5)
TMUX_SESSION_PREFIX = "shikiru-doctor-"
TMUX_SESSION_NAME = "probe"
_NO_TMUX_CONFIG = Path("/dev/null")
GIT_COMMAND = "git"
GIT_VERSION_ARGS = (GIT_COMMAND, "--version")
GIT_MINIMUM_VERSION = (2, 45)
GIT_CHECK_NAME = "git"
TMUX_CHECK_NAME = "tmux"
REPOSITORY_CHECK_NAME = "repository"
CONFIG_CHECK_NAME = "config"
PLUGINS_CHECK_NAME = "plugins"
PLUGIN_CONFIG_CHECK_NAME = "config.plugins"
PLUGIN_CHECK_PREFIX = "plugin"
PLUGIN_CHECK_SEPARATOR = ":"
MODEL_CHOICES_CHECK_NAME = "model_choices"
COW_CHECK_NAME = "cow"
COW_SUPPORTED_DETAIL = "copy-on-write supported"
DATABASE_CHECK_NAME = "db"
ENGINE_OUTPUT_CHECK_NAME = "engine-output"
PROBE_CHECK_PREFIX = "probe"


class DoctorCheck(BaseModel):
    """One named environment or project diagnostic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    ok: bool
    detail: str


class MissingCacheSuggestion(BaseModel):
    """Detected cache directories that the configured cache list does not name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["missing_cache"] = "missing_cache"
    paths: list[str]


class IneffectiveCacheSuggestion(BaseModel):
    """A configured cache entry that copies nothing into task worktrees."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ineffective_cache"] = "ineffective_cache"
    entry: str
    reason: CacheEntryEffect


DoctorSuggestion = Annotated[
    MissingCacheSuggestion | IneffectiveCacheSuggestion, Field(discriminator="kind")
]


class DoctorReport(BaseModel):
    """The complete result of one `doctor` invocation.

    Suggestions are informational: they never change `ok` or the checks.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    checks: list[DoctorCheck]
    suggestions: list[DoctorSuggestion] = Field(default_factory=list)

    @classmethod
    def from_checks(
        cls, checks: Sequence[DoctorCheck], suggestions: Sequence[DoctorSuggestion] = ()
    ) -> DoctorReport:
        """Build a report whose status reflects every diagnostic, never the suggestions."""
        values = list(checks)
        return cls(
            ok=all(check.ok for check in values),
            checks=values,
            suggestions=list(suggestions),
        )


class Doctor:
    """Run diagnostics through the same boundaries used by the application."""

    def __init__(
        self,
        project_root: Path,
        *,
        cache_cloner: CacheCloner,
        git: Git,
        runner: SubprocessRunner,
        clock: Clock,
        plugin_checks: PluginChecks,
        probe: AgentProbe,
    ) -> None:
        """Store the project and injected git, process, clock, plugin and probe boundaries."""
        self._project_root = project_root.resolve()
        self._cache_cloner = cache_cloner
        self._git = git
        self._runner = runner
        self._clock = clock
        self._plugins = plugin_checks
        self._probe = probe

    async def run(self, overrides: Sequence[str] = (), *, probe: bool = False) -> DoctorReport:
        """Run core and plugin checks, plus one live turn per distinct ladder rung with `probe`."""
        checks: list[DoctorCheck] = []
        checks.append(await self._tmux_check())
        checks.append(await self._git_check())
        repository_check = await self._repository_check()
        checks.append(repository_check)
        settings, config_check = self._settings_check(overrides)
        checks.append(config_check)
        checks.append(await self._database_check())
        checks.extend(self._engine_output_check())
        cow_check, cow_supported = await self._cow_check()
        checks.append(cow_check)
        if settings is None:
            checks.append(
                DoctorCheck(name=PLUGIN_CONFIG_CHECK_NAME, ok=False, detail="config unavailable")
            )
            return DoctorReport.from_checks(checks)
        plugin_checks = await self._plugin_checks(settings)
        checks.extend(plugin_checks)
        plugin_ok = bool(settings.agents) and all(check.ok for check in plugin_checks)
        if plugin_ok:
            plugins_detail = "all configured plugins passed"
        elif not settings.agents:
            plugins_detail = "no configured plugins"
        else:
            plugins_detail = "one or more plugins failed"
        checks.append(DoctorCheck(name=PLUGINS_CHECK_NAME, ok=plugin_ok, detail=plugins_detail))
        if probe:
            checks.extend(await self._probe_checks(settings))
        suggestions: list[DoctorSuggestion] = []
        if repository_check.ok and cow_supported:
            suggestions = await self._cache_suggestions(settings)
        return DoctorReport.from_checks(checks, suggestions)

    async def _probe_checks(self, settings: Settings) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        probed_agents: set[str] = set()
        for rung in dict.fromkeys(settings.ladder_rungs()):
            probe = self._probe.smoke if rung.agent in probed_agents else self._probe.run
            probed_agents.add(rung.agent)
            outcome = await probe(
                self._project_root, rung, settings.agents[rung.agent].options, settings.doctor
            )
            checks.append(
                DoctorCheck(name=_probe_check_name(rung), ok=outcome.ok, detail=outcome.detail)
            )
        return checks

    async def _tmux_check(self) -> DoctorCheck:
        socket_name = private_socket_name(TMUX_SESSION_PREFIX)
        tmux = Tmux(self._runner, socket=socket_name, config_file=_NO_TMUX_CONFIG)
        try:
            version = await self._runner.run(
                TMUX_VERSION_ARGS,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                check=False,
            )
            problem = _version_problem(version, TMUX_MINIMUM_VERSION)
            if problem is None:
                await tmux.new_session(
                    f"{TMUX_SESSION_NAME}-{uuid.uuid4().hex[:SESSION_SUFFIX_LENGTH]}",
                    cwd=self._project_root,
                    env={},
                    window_name=TMUX_SESSION_NAME,
                    size=DETACHED_SIZE,
                )
            check = DoctorCheck(
                name=TMUX_CHECK_NAME, ok=problem is None, detail=problem or _process_detail(version)
            )
        except (OSError, ShikiruError) as error:
            check = DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=str(error))
        try:
            await tmux.kill_server()
        except (OSError, ShikiruError) as error:
            check = DoctorCheck(
                name=TMUX_CHECK_NAME,
                ok=False,
                detail=f"{check.detail}; cleanup failed: {_error_detail(error)}",
            )
        return check

    async def _git_check(self) -> DoctorCheck:
        try:
            result = await self._runner.run(
                GIT_VERSION_ARGS,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=GIT_CHECK_NAME, ok=False, detail=str(error))
        problem = _version_problem(result, GIT_MINIMUM_VERSION)
        return DoctorCheck(
            name=GIT_CHECK_NAME,
            ok=problem is None,
            detail=problem or _process_detail(result),
        )

    async def _repository_check(self) -> DoctorCheck:
        try:
            is_root = await self._git.is_repository_root(self._project_root)
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=REPOSITORY_CHECK_NAME, ok=False, detail=str(error))
        detail = str(self._project_root) if is_root else not_repository_root(self._project_root)
        return DoctorCheck(name=REPOSITORY_CHECK_NAME, ok=is_root, detail=detail)

    def _settings_check(self, overrides: Sequence[str]) -> tuple[Settings | None, DoctorCheck]:
        config_path = project_config_path(self._project_root)
        if not config_path.is_file():
            return None, DoctorCheck(name=CONFIG_CHECK_NAME, ok=False, detail="config missing")
        try:
            settings = load_settings(self._project_root, overrides)
        except (ConfigError, OSError) as error:
            return None, DoctorCheck(name=CONFIG_CHECK_NAME, ok=False, detail=str(error))
        return settings, DoctorCheck(name=CONFIG_CHECK_NAME, ok=True, detail=str(config_path))

    async def _database_check(self) -> DoctorCheck:
        database = Database(state_db_path(self._project_root))
        try:
            version = await asyncio.to_thread(database.health)
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=DATABASE_CHECK_NAME, ok=False, detail=str(error))
        return DoctorCheck(name=DATABASE_CHECK_NAME, ok=True, detail=f"schema {version}")

    def _engine_output_check(self) -> list[DoctorCheck]:
        path = engine_output_path(self._project_root)
        if not path.is_file() or path.stat().st_size == 0:
            return []
        return [
            DoctorCheck(
                name=ENGINE_OUTPUT_CHECK_NAME,
                ok=True,
                detail=f"the last engine start printed output: {path}",
            )
        ]

    async def _cow_check(self) -> tuple[DoctorCheck, bool]:
        try:
            supported = await self._cache_cloner.check_support(self._project_root)
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=COW_CHECK_NAME, ok=False, detail=str(error)), False
        detail = COW_SUPPORTED_DETAIL if supported else CACHE_COW_UNSUPPORTED_WARNING
        return DoctorCheck(name=COW_CHECK_NAME, ok=True, detail=detail), supported

    async def _cache_suggestions(self, settings: Settings) -> list[DoctorSuggestion]:
        ignored_paths = await self._git.ignored_paths(self._project_root)
        ignored = frozenset(self._relative_path(path) for path in ignored_paths)
        matches_by_entry = {entry: self._cache_matches(entry) for entry in settings.repo.caches}
        copied = {match for matches in matches_by_entry.values() for match in matches}
        suggestions: list[DoctorSuggestion] = []
        detected = detected_caches(
            self._relative_path(path) for path in ignored_paths if path.is_dir()
        )
        missing = uncached_detections(detected, copied)
        if missing:
            suggestions.append(MissingCacheSuggestion(paths=list(missing)))
        for entry in settings.repo.caches:
            reason = ineffective_cache_entry(matches_by_entry[entry], ignored)
            if reason is not None:
                suggestions.append(IneffectiveCacheSuggestion(entry=entry, reason=reason))
        return suggestions

    def _cache_matches(self, entry: str) -> list[str]:
        try:
            return [self._relative_path(match) for match in self._project_root.glob(entry)]
        except NotImplementedError, ValueError:
            return []

    def _relative_path(self, path: Path) -> str:
        return path.relative_to(self._project_root).as_posix()

    async def _plugin_checks(self, settings: Settings) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        rungs = tuple(dict.fromkeys(settings.ladder_rungs()))
        for name, agent_config in sorted(settings.agents.items()):
            configured_rungs = tuple(rung for rung in rungs if rung.agent == name)
            result = await self._plugins.check(
                name,
                agent_config.options,
                settings.ladder_models(name),
                include_model_catalog=bool(configured_rungs),
            )
            if result.error is not None:
                checks.append(
                    DoctorCheck(name=_plugin_check_name(name), ok=False, detail=result.detail)
                )
                continue
            plugin_results = list(result.checks)
            choice_check = None
            if configured_rungs and result.catalog is not None:
                choice_check = _model_choices_check(configured_rungs, result.catalog)
                plugin_results.append(choice_check)
            checks.extend(
                DoctorCheck(
                    name=_plugin_result_name(name, plugin_result.name),
                    ok=plugin_result.ok,
                    detail=plugin_result.detail,
                )
                for plugin_result in plugin_results
            )
            plugin_ok = result.ok and (choice_check is None or choice_check.ok)
            plugin_detail = result.detail
            if choice_check is not None and not choice_check.ok:
                plugin_detail = f"{plugin_detail}; {choice_check.name}: {choice_check.detail}"
            checks.append(
                DoctorCheck(name=_plugin_check_name(name), ok=plugin_ok, detail=plugin_detail)
            )
        return checks


def _process_detail(result: ProcessResult) -> str:
    return result.stdout.strip() or result.stderr.strip() or f"exit {result.returncode}"


def _version_problem(result: ProcessResult, minimum: Version) -> str | None:
    detail = _process_detail(result)
    if result.returncode != 0:
        return detail
    return version_shortfall(detail, minimum)


def _error_detail(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _probe_check_name(rung: Rung) -> str:
    efforts = () if rung.effort is None else (rung.effort,)
    return ":".join((PROBE_CHECK_PREFIX, rung.agent, rung.model, *efforts))


def _plugin_check_name(name: str) -> str:
    return PLUGIN_CHECK_SEPARATOR.join((PLUGIN_CHECK_PREFIX, name))


def _plugin_result_name(name: str, result: str) -> str:
    return PLUGIN_CHECK_SEPARATOR.join((PLUGIN_CHECK_PREFIX, name, result))


def _model_choices_check(rungs: Sequence[Rung], catalog: ModelCatalog) -> CheckResult:
    choices = tuple(dict.fromkeys((rung.model, rung.effort) for rung in rungs))
    assessments = tuple(assess_model_choice(model, effort, catalog) for model, effort in choices)
    invalid = tuple(
        assessment.detail for assessment in assessments if assessment.supported is False
    )
    unverified = tuple(
        assessment.detail for assessment in assessments if assessment.supported is None
    )
    if invalid:
        return CheckResult(
            name=MODEL_CHOICES_CHECK_NAME,
            ok=False,
            detail="; ".join((*invalid, *unverified)),
        )
    if unverified:
        return CheckResult(
            name=MODEL_CHOICES_CHECK_NAME,
            ok=True,
            detail=f"model and effort choices could not be fully verified: {'; '.join(unverified)}",
        )
    return CheckResult(
        name=MODEL_CHOICES_CHECK_NAME,
        ok=True,
        detail="configured model and effort choices are listed in the agent catalog",
    )
