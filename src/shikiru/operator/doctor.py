"""Environment and configured agent diagnostics for the operator CLI."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from shikiru.agents.contracts import AgentContext, Rung
from shikiru.config.loader import PROJECT_CONFIG_RELATIVE_PATH, SHIKIRU_DIR_NAME, load_settings
from shikiru.errors import ConfigError, ShikiruError
from shikiru.operator.detectors import not_repository_root
from shikiru.operator.probe import AgentProbe, kill_tmux_server
from shikiru.store.database import Database
from shikiru.workspace.caches import (
    CACHE_COW_UNSUPPORTED_WARNING,
    CacheCloner,
    default_cow_args,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.process import ProcessResult, SubprocessRunner
    from shikiru.workspace.git import Git


DOCTOR_TIMEOUT_SECONDS = 60.0
TMUX_COMMAND = "tmux"
TMUX_VERSION_ARGS = (TMUX_COMMAND, "-V")
TMUX_SESSION_PREFIX = "shikiru-doctor-"
TMUX_SESSION_NAME = "probe"
GIT_COMMAND = "git"
GIT_VERSION_ARGS = (GIT_COMMAND, "--version")
GIT_CHECK_NAME = "git"
TMUX_CHECK_NAME = "tmux"
REPOSITORY_CHECK_NAME = "repository"
CONFIG_CHECK_NAME = "config"
PLUGINS_CHECK_NAME = "plugins"
PLUGIN_CONFIG_CHECK_NAME = "config.plugins"
COW_CHECK_NAME = "cow"
DATABASE_CHECK_NAME = "db"
STATE_DB_FILENAME = "state.db"
PROBE_CHECK_SUFFIX = "probe"


class DoctorCheck(BaseModel):
    """One named environment or project diagnostic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    ok: bool
    detail: str


class DoctorReport(BaseModel):
    """The complete result of one `doctor` invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    checks: list[DoctorCheck]

    @classmethod
    def from_checks(cls, checks: Sequence[DoctorCheck]) -> DoctorReport:
        """Build a report whose status reflects every diagnostic."""
        values = list(checks)
        return cls(ok=all(check.ok for check in values), checks=values)


class Doctor:
    """Run diagnostics through the same boundaries used by the application."""

    def __init__(
        self,
        project_root: Path,
        *,
        git: Git,
        runner: SubprocessRunner,
        clock: Clock,
        registry: AgentRegistry,
    ) -> None:
        """Store the project and injected git, process, clock and plugin boundaries."""
        self._project_root = project_root.resolve()
        self._git = git
        self._runner = runner
        self._clock = clock
        self._registry = registry

    async def run(self, overrides: Sequence[str] = (), *, probe: bool = False) -> DoctorReport:
        """Run core checks, configured plugin checks and optional live contract probes."""
        checks: list[DoctorCheck] = []
        checks.append(await self._tmux_check())
        checks.append(await self._git_check())
        checks.append(await self._repository_check())
        settings, config_check = self._settings_check(overrides)
        checks.append(config_check)
        checks.append(await self._database_check())
        checks.append(await self._cow_check())
        if settings is None:
            checks.append(
                DoctorCheck(name=PLUGIN_CONFIG_CHECK_NAME, ok=False, detail="config unavailable")
            )
            return DoctorReport.from_checks(checks)
        plugin_checks = await self._plugin_checks(settings)
        checks.extend(plugin_checks)
        plugin_ok = bool(settings.agents) and all(check.ok for check in plugin_checks)
        checks.append(
            DoctorCheck(
                name=PLUGINS_CHECK_NAME,
                ok=plugin_ok,
                detail=(
                    "all configured plugins passed"
                    if plugin_ok
                    else "no configured plugins"
                    if not settings.agents
                    else "one or more plugins failed"
                ),
            )
        )
        if probe:
            checks.extend(await self._probe_checks(settings, plugin_checks))
        return DoctorReport.from_checks(checks)

    async def _tmux_check(self) -> DoctorCheck:
        socket_name = f"{TMUX_SESSION_PREFIX}{uuid.uuid4().hex}"
        session_name = f"{TMUX_SESSION_NAME}-{uuid.uuid4().hex[:8]}"
        try:
            version = await self._runner.run(
                TMUX_VERSION_ARGS,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                check=False,
            )
            if version.returncode != 0:
                check = DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=_process_detail(version))
            else:
                started = await self._runner.run(
                    [
                        TMUX_COMMAND,
                        "-L",
                        socket_name,
                        "-f",
                        "/dev/null",
                        "new-session",
                        "-d",
                        "-s",
                        session_name,
                    ],
                    timeout=DOCTOR_TIMEOUT_SECONDS,
                    check=False,
                )
                check = DoctorCheck(
                    name=TMUX_CHECK_NAME,
                    ok=started.returncode == 0 and not started.stderr.strip(),
                    detail=_process_detail(started),
                )
        except (OSError, ShikiruError) as error:
            check = DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=str(error))
        try:
            await kill_tmux_server(self._runner, socket_name)
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
        return DoctorCheck(
            name=GIT_CHECK_NAME,
            ok=result.returncode == 0,
            detail=_process_detail(result),
        )

    async def _repository_check(self) -> DoctorCheck:
        try:
            is_root = await self._git.is_repository_root(self._project_root)
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=REPOSITORY_CHECK_NAME, ok=False, detail=str(error))
        detail = str(self._project_root) if is_root else not_repository_root(self._project_root)
        return DoctorCheck(name=REPOSITORY_CHECK_NAME, ok=is_root, detail=detail)

    def _settings_check(self, overrides: Sequence[str]) -> tuple[Settings | None, DoctorCheck]:
        config_path = self._project_root / PROJECT_CONFIG_RELATIVE_PATH
        if not config_path.is_file():
            return None, DoctorCheck(name=CONFIG_CHECK_NAME, ok=False, detail="config missing")
        try:
            settings = load_settings(self._project_root, overrides)
        except (ConfigError, OSError) as error:
            return None, DoctorCheck(name=CONFIG_CHECK_NAME, ok=False, detail=str(error))
        return settings, DoctorCheck(name=CONFIG_CHECK_NAME, ok=True, detail=str(config_path))

    async def _database_check(self) -> DoctorCheck:
        database = Database(self._project_root / SHIKIRU_DIR_NAME / STATE_DB_FILENAME)
        try:
            version = await asyncio.to_thread(database.health)
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=DATABASE_CHECK_NAME, ok=False, detail=str(error))
        return DoctorCheck(name=DATABASE_CHECK_NAME, ok=True, detail=f"schema {version}")

    async def _cow_check(self) -> DoctorCheck:
        cow_args = default_cow_args()
        if cow_args is None:
            return DoctorCheck(
                name=COW_CHECK_NAME,
                ok=True,
                detail=CACHE_COW_UNSUPPORTED_WARNING,
            )
        try:
            with tempfile.TemporaryDirectory(prefix="shikiru-doctor-cow-") as directory:
                result = await CacheCloner(self._runner, cow_args=cow_args).clone(
                    Path(directory), Path(directory), (), ()
                )
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=COW_CHECK_NAME, ok=False, detail=str(error))
        if result.warning is not None:
            return DoctorCheck(name=COW_CHECK_NAME, ok=True, detail=result.warning)
        return DoctorCheck(name=COW_CHECK_NAME, ok=True, detail="copy-on-write supported")

    async def _plugin_checks(self, settings: Settings) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        for name, agent_config in sorted(settings.agents.items()):
            try:
                agent = self._registry.create(
                    name,
                    AgentContext(
                        options=agent_config.options,
                        runner=self._runner,
                        clock=self._clock,
                    ),
                )
                results = await agent.doctor()
            except (
                AttributeError,
                OSError,
                ShikiruError,
                RuntimeError,
                TypeError,
                ValueError,
                TimeoutError,
            ) as error:
                checks.append(
                    DoctorCheck(
                        name=_plugin_check_name(name), ok=False, detail=_error_detail(error)
                    )
                )
                continue
            plugin_ok = True
            for result in results:
                plugin_ok = plugin_ok and result.ok
                checks.append(
                    DoctorCheck(
                        name=_plugin_result_name(name, result.name),
                        ok=result.ok,
                        detail=result.detail,
                    )
                )
            if not results:
                plugin_ok = False
                detail = "plugin returned no checks"
            else:
                detail = "all checks passed" if plugin_ok else "one or more checks failed"
            checks.append(DoctorCheck(name=_plugin_check_name(name), ok=plugin_ok, detail=detail))
        return checks

    async def _probe_checks(
        self, settings: Settings, plugin_checks: Sequence[DoctorCheck]
    ) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        for name, rung in sorted(_probe_rungs(settings).items()):
            agent_config = settings.agents[name]
            plugin_check = next(
                (check for check in plugin_checks if check.name == _plugin_check_name(name)),
                None,
            )
            if plugin_check is None or not plugin_check.ok:
                checks.append(
                    DoctorCheck(
                        name=_probe_check_name(name),
                        ok=False,
                        detail="skipped because plugin doctor failed",
                    )
                )
                continue
            try:
                agent = self._registry.create(
                    name,
                    AgentContext(
                        options=agent_config.options,
                        runner=self._runner,
                        clock=self._clock,
                    ),
                )
                await AgentProbe(
                    self._project_root,
                    agent,
                    self._runner,
                    self._clock,
                    rung,
                ).run()
            except (
                AttributeError,
                OSError,
                ShikiruError,
                RuntimeError,
                TypeError,
                ValueError,
                TimeoutError,
            ) as error:
                checks.append(
                    DoctorCheck(name=_probe_check_name(name), ok=False, detail=_error_detail(error))
                )
            else:
                checks.append(
                    DoctorCheck(
                        name=_probe_check_name(name), ok=True, detail="session and turn passed"
                    )
                )
        return checks


def _process_detail(result: ProcessResult) -> str:
    return result.stdout.strip() or result.stderr.strip() or f"exit {result.returncode}"


def _error_detail(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _plugin_check_name(name: str) -> str:
    return f"plugin:{name}"


def _plugin_result_name(name: str, result: str) -> str:
    return f"plugin:{name}:{result}"


def _probe_check_name(name: str) -> str:
    return f"plugin:{name}:{PROBE_CHECK_SUFFIX}"


def _probe_rungs(settings: Settings) -> dict[str, Rung]:
    roles = settings.roles
    ladders = [
        roles.leader.ladder,
        roles.implementer.ladder,
        *(override.ladder for override in roles.implementer.complexity.values()),
        roles.helper.ladder,
    ]
    rungs: dict[str, Rung] = {}
    for ladder in ladders:
        for rung in ladder:
            rungs.setdefault(rung.agent, rung)
    return rungs
