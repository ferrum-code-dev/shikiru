"""Codex `InteractiveAgent` plugin."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import ValidationError

from shikiru.agents.contracts import CheckResult, QuotaWindow, SessionKeys
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.probe import PROBE_ENV_VALUE, PROBE_ENV_VAR
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.codex.headless import CodexHeadless
from shikiru.plugins.agents.codex.options import (
    ADD_DIR_FLAG,
    AUTH_API_KEY,
    BYPASS_HOOK_TRUST_FLAG,
    CD_FLAG,
    CHECK_FOR_UPDATE_CONFIG,
    CODEX_API_KEY_ENV,
    CODEX_COMMAND,
    CODEX_HOME_ENV_VAR,
    CONFIG_FLAG,
    DOCTOR_TIMEOUT_SECONDS,
    GIT_COMMON_DIR_TIMEOUT_SECONDS,
    HOME_ENV_VAR,
    HOOK_EVENTS,
    LOGIN_STATUS_ARGV,
    MODEL_FLAG,
    OPENAI_API_KEY_ENV,
    PATH_ENV_VAR,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    SHELL_ENV_VAR,
    VERSION_ARGV,
    CodexOptions,
)
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from shikiru.plugins.agents.codex.session import (
    CodexSession,
    codex_home,
    hook_file_size,
    restored_session,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shikiru.agents.contracts import (
        AgentContext,
        AgentSession,
        HeadlessAgent,
        LaunchSpec,
        PaneHandle,
    )
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

PRICES_PATH = Path(__file__).parent / "prices.toml"
_GIT_COMMON_DIR_ARGV = ("git", "rev-parse", "--path-format=absolute", "--git-common-dir")
_AUTH_NOT_LOGGED_IN_TEXT = "not logged in"
_AUTH_LOGGED_IN_TEXTS = ("logged in", "authenticated")
_INVALID_AUTH_STATUS_MESSAGE = "Codex login status did not return a valid result"
_HOOKS_FIELD = "hooks"
_HOOK_TYPE_FIELD = "type"
_HOOK_COMMAND_FIELD = "command"
_HOOK_TYPE_COMMAND = "command"
_TRUST_LEVEL = "trusted"
PROBE_CODEX_HOME_NAME = "codex-home"
PROBE_HOME_NAME = "home"
PROBE_HOOK_BIN_NAME = ".local/bin"
PROBE_SHELL = "/bin/sh"
STARTUP_TIMEOUT_SECONDS = 45.0
STARTUP_POLL_SECONDS = 0.1
STARTUP_STABILITY_COUNT = 10
READY_MARKER = "Ask Codex to do anything"
TRUST_PROMPT_MARKERS = (
    "Do you trust the contents of this directory?",
    "Quick safety check: Is this a project you created or one you trust?",
    "Trusting will apply to the repository root:",
)
STARTUP_TIMEOUT_MESSAGE = "Codex trust prompt or session startup did not complete"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


@runtime_checkable
class _StartupPane(Protocol):
    async def capture_visible(self) -> str: ...

    async def is_alive(self) -> bool: ...

    async def send_keys(self, *keys: str) -> None: ...


class CodexAgent:
    """A Codex plugin that normalizes hooks, rollouts, quotas and compaction."""

    name = "codex"
    session_start_requires_prompt = True
    keys = SessionKeys(
        interrupt=("Escape",),
        exit_input="/quit",
        safe=("Enter", "Escape", "Down", "1", "2", "y"),
    )

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: CodexOptions) -> None:
        """Hold validated options and injected process and clock boundaries."""
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._prices = PriceTable.from_toml(PRICES_PATH)
        self._codex_home = codex_home()
        self._quota_probe = CodexQuotaProbe(self._codex_home, clock, self._reset_time)

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Launch a new or resumable Codex session in `pane`."""
        hook_offset = await asyncio.to_thread(hook_file_size, spec.run_dir, spec.role)
        probe_home = self._probe_codex_home(spec) if _is_probe(spec) else None
        argv = await self._argv(spec)
        await pane.respawn(
            argv,
            env=self._pane_environment(spec.env, codex_home=probe_home),
            cwd=spec.cwd,
        )
        if not _is_probe(spec):
            await self._prepare_startup(pane)
        return self._session(spec, hook_offset, codex_home_path=probe_home)

    async def _prepare_startup(self, pane: PaneHandle) -> None:
        if not isinstance(pane, _StartupPane):
            return
        deadline = self._clock.now() + timedelta(seconds=STARTUP_TIMEOUT_SECONDS)
        prompt_observations = 0
        ready_observations = 0
        while self._clock.now() < deadline:
            if not await pane.is_alive():
                return
            visible = await pane.capture_visible()
            trust_prompt = any(marker in visible for marker in TRUST_PROMPT_MARKERS)
            if trust_prompt:
                prompt_observations += 1
                if prompt_observations >= STARTUP_STABILITY_COUNT:
                    await pane.send_keys("1", "Enter")
                    prompt_observations = 0
                    await self._clock.sleep(STARTUP_POLL_SECONDS)
                    continue
            else:
                prompt_observations = 0
            if READY_MARKER in visible:
                ready_observations += 1
                if ready_observations >= STARTUP_STABILITY_COUNT:
                    return
            else:
                ready_observations = 0
            await self._clock.sleep(STARTUP_POLL_SECONDS)
        raise TimeoutError(STARTUP_TIMEOUT_MESSAGE)

    async def prepare_probe(self, spec: LaunchSpec, pane: PaneHandle) -> bool:
        """Accept Codex's first-use trust prompt while a doctor probe starts."""
        del spec
        await self._prepare_startup(pane)
        return True

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, object]
    ) -> AgentSession:
        """Attach to a running Codex pane using persisted file offsets."""
        del pane
        return restored_session(
            spec,
            state=state,
            clock=self._clock,
            reset_time=self._reset_time,
            prices=self._prices,
            api_key_auth=self._options.auth == AUTH_API_KEY,
            compact_below_free_pct=self._options.compact_below_free_pct,
            codex_home=self._codex_home,
        )

    async def quota(self) -> list[QuotaWindow]:
        """Return the latest quota windows recorded by Codex, or no windows."""
        return self._quota_probe.get()

    async def doctor(self) -> list[CheckResult]:
        """Check Codex's binary, version and authentication."""
        binary = await self._check_binary()
        if not binary.ok:
            unavailable = "binary unavailable"
            return [
                binary,
                CheckResult("version", False, unavailable),
                CheckResult("login", False, unavailable),
            ]
        version = await self._check_version()
        login = await self._check_login()
        return [binary, version, login]

    def resume_command(self, session_id: str, cwd: Path) -> str | None:
        """Return the command that resumes a Codex conversation by id."""
        del cwd
        return f"{CODEX_COMMAND} {RESUME_SUBCOMMAND} {session_id}"

    def headless(self) -> HeadlessAgent | None:
        """Return Codex's schema-constrained headless client."""
        return CodexHeadless(self._runner, self._subprocess_environment())

    def _session(
        self, spec: LaunchSpec, hook_offset: int, *, codex_home_path: Path | None = None
    ) -> CodexSession:
        return CodexSession(
            spec.run_dir,
            spec.role,
            spec.resume_session_id,
            hook_offset,
            None,
            clock=self._clock,
            reset_time=self._reset_time,
            prices=self._prices,
            model=spec.rung.model,
            api_key_auth=self._options.auth == AUTH_API_KEY,
            compact_below_free_pct=self._options.compact_below_free_pct,
            codex_home=codex_home_path or self._codex_home,
        )

    async def _argv(self, spec: LaunchSpec) -> tuple[str, ...]:
        argv = [CODEX_COMMAND]
        if spec.resume_session_id is not None:
            argv.extend([RESUME_SUBCOMMAND, spec.resume_session_id])
        argv.extend([MODEL_FLAG, spec.rung.model])
        if spec.rung.effort is not None:
            argv.extend([CONFIG_FLAG, f'{REASONING_EFFORT_CONFIG}="{spec.rung.effort}"'])
        argv.append(BYPASS_HOOK_TRUST_FLAG)
        if _is_probe(spec):
            argv.extend([CONFIG_FLAG, CHECK_FOR_UPDATE_CONFIG])
        argv.extend(self._hook_config_args(self._hook_command()))
        argv.extend([CONFIG_FLAG, self._trust_config(spec.cwd), CD_FLAG, str(spec.cwd)])
        directories = [spec.run_dir, *spec.add_dirs, *(await self._git_common_dirs(spec))]
        for directory in _unique_paths(directories):
            argv.extend([ADD_DIR_FLAG, str(directory)])
        argv.extend(self._options.args)
        return tuple(argv)

    def _hook_config_args(self, hook_command: str) -> list[str]:
        encoded = (
            f'[{{{_HOOKS_FIELD}=[{{{_HOOK_TYPE_FIELD}="{_HOOK_TYPE_COMMAND}",'
            f'{_HOOK_COMMAND_FIELD}="{hook_command}"}}]}}]'
        )
        return [item for event in HOOK_EVENTS for item in (CONFIG_FLAG, f"hooks.{event}={encoded}")]

    def _hook_command(self) -> str:
        executable = Path(sys.executable).with_name("shikiru")
        if not executable.is_file():
            message = f"Shikiru executable is missing at {executable}"
            raise ConfigError(message)
        return f"{shlex.quote(str(executable))} hook"

    def _trust_config(self, cwd: Path) -> str:
        project = json.dumps(str(cwd), ensure_ascii=False)
        return f"projects.{project}.trust_level={json.dumps(_TRUST_LEVEL)}"

    async def _git_common_dirs(self, spec: LaunchSpec) -> list[Path]:
        common_dirs: list[Path] = []
        for candidate in (spec.cwd, *spec.add_dirs):
            result = await self._runner.run(
                _GIT_COMMON_DIR_ARGV,
                timeout=GIT_COMMON_DIR_TIMEOUT_SECONDS,
                cwd=candidate,
                check=False,
            )
            if result.returncode != 0:
                continue
            value = result.stdout.strip()
            if value:
                common_dirs.append(Path(value))
        return common_dirs

    def _pane_environment(
        self, base: Mapping[str, str], *, codex_home: Path | None = None
    ) -> dict[str, str | None]:
        environment: dict[str, str | None] = dict(base)
        environment[CODEX_HOME_ENV_VAR] = str(codex_home or self._codex_home)
        if codex_home is not None:
            probe_home = codex_home.parent / PROBE_HOME_NAME
            probe_home.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
            environment[HOME_ENV_VAR] = str(probe_home)
            environment[SHELL_ENV_VAR] = PROBE_SHELL
            hook_bin = probe_home / PROBE_HOOK_BIN_NAME
            hook_bin.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
            hook_executable = hook_bin / "shikiru"
            executable = Path(sys.executable).with_name("shikiru")
            if not executable.is_file():
                message = f"Shikiru executable is missing at {executable}"
                raise ConfigError(message)
            hook_executable.symlink_to(executable, target_is_directory=False)
            codex_executable = shutil.which(CODEX_COMMAND)
            if codex_executable is None:
                message = f"Codex executable is missing: {CODEX_COMMAND}"
                raise ConfigError(message)
            environment[PATH_ENV_VAR] = os.pathsep.join(
                (str(hook_bin), str(Path(codex_executable).parent), os.defpath)
            )
        if self._options.auth == AUTH_API_KEY:
            environment[CODEX_API_KEY_ENV] = self._api_key()
            environment[OPENAI_API_KEY_ENV] = None
        else:
            environment[CODEX_API_KEY_ENV] = None
            environment[OPENAI_API_KEY_ENV] = None
        return environment

    def _probe_codex_home(self, spec: LaunchSpec) -> Path:
        probe_home = spec.run_dir / PROBE_CODEX_HOME_NAME
        probe_home.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
        source_auth = self._codex_home / "auth.json"
        if not source_auth.is_file():
            message = f"Codex probe auth file is missing at {source_auth}"
            raise ConfigError(message)
        target_auth = probe_home / "auth.json"
        shutil.copyfile(source_auth, target_auth)
        target_auth.chmod(PRIVATE_FILE_MODE)
        project = json.dumps(str(spec.cwd), ensure_ascii=False)
        config = probe_home / "config.toml"
        config.write_text(
            "check_for_update_on_startup = false\n\n"
            f'[projects.{project}]\ntrust_level = "{_TRUST_LEVEL}"\n'
        )
        config.chmod(PRIVATE_FILE_MODE)
        return probe_home

    def _subprocess_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self._options.auth == AUTH_API_KEY:
            environment[CODEX_API_KEY_ENV] = self._api_key()
            environment.pop(OPENAI_API_KEY_ENV, None)
        else:
            environment.pop(CODEX_API_KEY_ENV, None)
            environment.pop(OPENAI_API_KEY_ENV, None)
        return environment

    def _api_key(self) -> str:
        value = os.environ.get(self._options.api_key_env)
        if not value:
            message = f"Codex API key environment variable {self._options.api_key_env!r} is unset"
            raise ConfigError(message)
        return value

    async def _check_binary(self) -> CheckResult:
        try:
            result = await self._runner.run(
                VERSION_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, CommandFailed, ShikiruError) as error:
            return CheckResult("binary", False, str(error))
        if result.returncode != 0:
            return CheckResult("binary", False, result.stderr.strip())
        return CheckResult("binary", True, CODEX_COMMAND)

    async def _check_version(self) -> CheckResult:
        try:
            result = await self._runner.run(
                VERSION_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult("version", False, str(error))
        detail = result.stdout.strip() or result.stderr.strip()
        return CheckResult("version", result.returncode == 0, detail)

    async def _check_login(self) -> CheckResult:
        try:
            result = await self._runner.run(
                LOGIN_STATUS_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult("login", False, str(error))
        lowered = f"{result.stdout}\n{result.stderr}".lower()
        logged_in = (
            result.returncode == 0
            and _AUTH_NOT_LOGGED_IN_TEXT not in lowered
            and any(text in lowered for text in _AUTH_LOGGED_IN_TEXTS)
        )
        detail = "authenticated" if logged_in else "not authenticated"
        return CheckResult("login", logged_in, detail)


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        normalized = path.resolve()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path)
    return tuple(unique)


def create(context: AgentContext) -> CodexAgent:
    """Build the Codex plugin from validated `AgentContext.options`."""
    try:
        options = CodexOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the Codex agent: {error}"
        raise ConfigError(message) from error
    return CodexAgent(context.runner, context.clock, options)


def _is_probe(spec: LaunchSpec) -> bool:
    return spec.env.get(PROBE_ENV_VAR) == PROBE_ENV_VALUE
