"""Environment and configured agent diagnostics for the operator CLI."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    AgentContext,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
)
from shikiru.config.loader import PROJECT_CONFIG_RELATIVE_PATH, SHIKIRU_DIR_NAME, load_settings
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    schema_for,
)
from shikiru.errors import ConfigError, ShikiruError
from shikiru.store.database import Database
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.pane import TmuxPane
from shikiru.workspace.caches import CacheCloner, default_cow_args

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from shikiru.agents.contracts import AgentSignal, InteractiveAgent
    from shikiru.agents.registry import AgentRegistry
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.process import ProcessResult, SubprocessRunner


DOCTOR_TIMEOUT_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 90.0
TMUX_COMMAND = "tmux"
TMUX_VERSION_ARGS = (TMUX_COMMAND, "-V")
TMUX_SESSION_PREFIX = "shikiru-doctor-"
TMUX_SESSION_NAME = "probe"
GIT_COMMAND = "git"
GIT_VERSION_ARGS = (GIT_COMMAND, "--version")
GIT_CHECK_NAME = "git"
TMUX_CHECK_NAME = "tmux"
CONFIG_CHECK_NAME = "config"
PLUGINS_CHECK_NAME = "plugins"
PLUGIN_CONFIG_CHECK_NAME = "config.plugins"
COW_CHECK_NAME = "cow"
DATABASE_CHECK_NAME = "db"
STATE_DB_FILENAME = "state.db"
PROBE_CHECK_SUFFIX = "probe"
PROBE_ROLE = "implementer"
PATH_ENV_VAR = "PATH"
PROBE_TURN_NAME = "01-doctor"
PROBE_PROMPT = (
    "This is a Shikiru live contract probe. Read the current turn report.schema.json, "
    "write a valid completed implementation report with shikiru report, then answer "
    "exactly SHIKIRU_PROBE_OK. Do not change files outside the current temporary project."
)
PROBE_MARKER = "SHIKIRU_PROBE_OK"
PROBE_REMAIN_ON_EXIT_OPTION = "remain-on-exit"
PROBE_REMAIN_ON_EXIT_VALUE = "on"
PROBE_REPO_README = "Shikiru doctor probe\n"
PROBE_REPO_BRANCH = "doctor-probe"
PROBE_GIT_INIT_ARGS = (GIT_COMMAND, "init", "-q", "-b", PROBE_REPO_BRANCH)
PROBE_REPORT_DIRECTORY = "turns"
_TMUX_KILL_ARGS = (TMUX_COMMAND, "-L")
PROBE_REPORT_MISSING_MESSAGE = "probe turn ended without a report"
PROBE_MARKER_MISSING_MESSAGE = "probe turn did not return its marker"
PROBE_PANE_MISSING_MESSAGE = "probe tmux session did not return a pane"


class DoctorProbeError(ShikiruError):
    """A live agent probe did not satisfy its contract."""


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
        runner: SubprocessRunner,
        clock: Clock,
        registry: AgentRegistry,
    ) -> None:
        """Store the project and injected process, clock and plugin boundaries."""
        self._project_root = project_root.resolve()
        self._runner = runner
        self._clock = clock
        self._registry = registry

    async def run(self, overrides: Sequence[str] = (), *, probe: bool = False) -> DoctorReport:
        """Run core checks, configured plugin checks and optional live contract probes."""
        checks: list[DoctorCheck] = []
        checks.append(await self._tmux_check())
        checks.append(await self._git_check())
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
                return DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=_process_detail(version))
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
            if started.returncode != 0 or started.stderr.strip():
                return DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=_process_detail(started))
            return DoctorCheck(name=TMUX_CHECK_NAME, ok=True, detail=version.stdout.strip())
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=TMUX_CHECK_NAME, ok=False, detail=str(error))
        finally:
            await _kill_tmux_server(self._runner, socket_name)

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
                ok=False,
                detail="no copy-on-write command for this platform",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="shikiru-doctor-cow-") as directory:
                result = await CacheCloner(self._runner, cow_args=cow_args).clone(
                    Path(directory), Path(directory), (), ()
                )
        except (OSError, ShikiruError) as error:
            return DoctorCheck(name=COW_CHECK_NAME, ok=False, detail=str(error))
        if result.warning is not None:
            return DoctorCheck(name=COW_CHECK_NAME, ok=False, detail=result.warning)
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
                OSError,
                ShikiruError,
                RuntimeError,
                TypeError,
                ValueError,
                TimeoutError,
            ) as error:
                checks.append(
                    DoctorCheck(name=_plugin_check_name(name), ok=False, detail=str(error))
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
        for name, agent_config in sorted(settings.agents.items()):
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
                    agent,
                    self._runner,
                    self._clock,
                    _probe_rung(settings, name),
                ).run()
            except (
                OSError,
                ShikiruError,
                RuntimeError,
                TypeError,
                ValueError,
                TimeoutError,
            ) as error:
                checks.append(
                    DoctorCheck(name=_probe_check_name(name), ok=False, detail=str(error))
                )
            else:
                checks.append(
                    DoctorCheck(
                        name=_probe_check_name(name), ok=True, detail="session and turn passed"
                    )
                )
        return checks


class AgentProbe:
    """Exercise the launch and first-turn portions of an agent contract in scratch state."""

    def __init__(
        self,
        agent: InteractiveAgent,
        runner: SubprocessRunner,
        clock: Clock,
        rung: Rung,
    ) -> None:
        """Store probe inputs and injected process boundaries."""
        self._agent = agent
        self._runner = runner
        self._clock = clock
        self._rung = rung

    async def run(self) -> None:
        """Launch a temporary session, submit a report turn and require its completion."""
        with tempfile.TemporaryDirectory(prefix="shikiru-doctor-probe-") as directory:
            root = Path(directory)
            repo = root / "repo"
            run_dir = root / "run"
            await self._prepare_repository(repo)
            self._prepare_turn(run_dir)
            socket_name = f"{TMUX_SESSION_PREFIX}{uuid.uuid4().hex}"
            tmux = Tmux(self._runner, socket=socket_name)
            session_name = f"{TMUX_SESSION_NAME}-{uuid.uuid4().hex[:8]}"
            try:
                pane_id = await self._new_probe_session(
                    socket_name,
                    session_name,
                    repo,
                    run_dir,
                )
                if not pane_id:
                    raise DoctorProbeError(PROBE_PANE_MISSING_MESSAGE)
                await tmux.set_window_option(
                    f"{exact_target(session_name)}:{TMUX_SESSION_NAME}",
                    PROBE_REMAIN_ON_EXIT_OPTION,
                    PROBE_REMAIN_ON_EXIT_VALUE,
                )
                pane = TmuxPane(tmux, pane_id, self._clock)
                session = await self._agent.launch(
                    LaunchSpec(
                        rung=self._rung,
                        role=PROBE_ROLE,
                        cwd=repo,
                        run_dir=run_dir,
                        add_dirs=(),
                        env=_probe_environment(run_dir),
                        resume_session_id=None,
                    ),
                    pane,
                )
                signals = session.signals()
                await self._await_signal(signals, SessionStarted)
                await pane.paste_line(PROBE_PROMPT)
                await self._await_signal(signals, PromptSubmitted)
                signal = await self._await_turn(signals)
                if isinstance(signal, TurnFailed):
                    message = f"probe turn failed: {signal.message}"
                    raise DoctorProbeError(message)
                report = role_dir(run_dir, PROBE_ROLE) / CURRENT_TURN_LINK_NAME / REPORT_FILENAME
                if not report.is_file():
                    raise DoctorProbeError(PROBE_REPORT_MISSING_MESSAGE)
                if PROBE_MARKER not in signal.last_message:
                    raise DoctorProbeError(PROBE_MARKER_MISSING_MESSAGE)
            finally:
                await _kill_tmux_server(self._runner, socket_name)

    async def _new_probe_session(
        self, socket_name: str, session_name: str, repo: Path, run_dir: Path
    ) -> str:
        environment = _probe_environment(run_dir)
        result = await self._runner.run(
            [
                TMUX_COMMAND,
                "-L",
                socket_name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                session_name,
                "-c",
                str(repo),
                "-n",
                TMUX_SESSION_NAME,
            ],
            timeout=DOCTOR_TIMEOUT_SECONDS,
            env=environment,
            check=True,
        )
        for target_args in (("-g",), ("-t", exact_target(session_name))):
            await self._runner.run(
                [
                    TMUX_COMMAND,
                    "-L",
                    socket_name,
                    "set-environment",
                    *target_args,
                    PATH_ENV_VAR,
                    environment[PATH_ENV_VAR],
                ],
                timeout=DOCTOR_TIMEOUT_SECONDS,
                check=True,
            )
        return result.stdout.strip()

    async def _prepare_repository(self, repo: Path) -> None:
        await asyncio.to_thread(repo.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((repo / "README.md").write_text, PROBE_REPO_README)
        await self._runner.run(
            PROBE_GIT_INIT_ARGS,
            timeout=DOCTOR_TIMEOUT_SECONDS,
            cwd=repo,
            check=True,
        )

    def _prepare_turn(self, run_dir: Path) -> None:
        turn_dir = run_dir / PROBE_REPORT_DIRECTORY / PROBE_TURN_NAME
        turn_dir.mkdir(parents=True, exist_ok=True)
        (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
        (turn_dir / REPORT_SCHEMA_FILENAME).write_text(
            json.dumps(schema_for(ReportKind.IMPLEMENTATION))
        )
        role_path = role_dir(run_dir, PROBE_ROLE)
        role_path.mkdir(parents=True, exist_ok=True)
        link = role_path / CURRENT_TURN_LINK_NAME
        link.unlink(missing_ok=True)
        link.symlink_to(turn_dir)

    async def _await_signal(
        self, signals: AsyncIterator[AgentSignal], signal_type: type[AgentSignal]
    ) -> AgentSignal:
        while True:
            signal = await asyncio.wait_for(signals.__anext__(), timeout=PROBE_TIMEOUT_SECONDS)
            if isinstance(signal, signal_type):
                return signal

    async def _await_turn(self, signals: AsyncIterator[AgentSignal]) -> TurnEnded | TurnFailed:
        while True:
            signal = await asyncio.wait_for(signals.__anext__(), timeout=PROBE_TIMEOUT_SECONDS)
            if isinstance(signal, (TurnEnded, TurnFailed)):
                return signal


async def _kill_tmux_server(runner: SubprocessRunner, socket_name: str) -> None:
    try:
        await runner.run(
            [*_TMUX_KILL_ARGS, socket_name, "kill-server"],
            timeout=DOCTOR_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, ShikiruError:
        return


def _process_detail(result: ProcessResult) -> str:
    return result.stdout.strip() or result.stderr.strip() or f"exit {result.returncode}"


def _plugin_check_name(name: str) -> str:
    return f"plugin:{name}"


def _plugin_result_name(name: str, result: str) -> str:
    return f"plugin:{name}:{result}"


def _probe_check_name(name: str) -> str:
    return f"plugin:{name}:{PROBE_CHECK_SUFFIX}"


def _probe_rung(settings: Settings, agent_name: str) -> Rung:
    ladders = (
        settings.roles.leader.ladder,
        settings.roles.implementer.ladder,
        settings.roles.helper.ladder,
    )
    for ladder in ladders:
        for rung in ladder:
            if rung.agent == agent_name:
                return rung
    message = f"no configured rung for agent {agent_name!r}"
    raise ConfigError(message)


def _probe_environment(run_dir: Path) -> dict[str, str]:
    executable_directory = Path(sys.executable).parent
    current_path = os.environ.get(PATH_ENV_VAR, "")
    path_parts = [str(executable_directory)]
    if current_path:
        path_parts.append(current_path)
    path = os.pathsep.join(path_parts)
    return {
        RUN_DIR_ENV_VAR: str(run_dir),
        ROLE_ENV_VAR: PROBE_ROLE,
        PATH_ENV_VAR: path,
    }
