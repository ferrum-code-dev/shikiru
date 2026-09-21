"""Codex `InteractiveAgent` plugin."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import CheckResult, QuotaWindow, SessionKeys
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.codex.headless import CodexHeadless
from shikiru.plugins.agents.codex.options import (
    ADD_DIR_FLAG,
    AUTH_API_KEY,
    BYPASS_HOOK_TRUST_FLAG,
    CD_FLAG,
    CODEX_API_KEY_ENV,
    CODEX_COMMAND,
    CONFIG_FLAG,
    DOCTOR_TIMEOUT_SECONDS,
    GIT_COMMON_DIR_TIMEOUT_SECONDS,
    HOOK_COMMAND,
    HOOK_EVENTS,
    LOGIN_STATUS_ARGV,
    MODEL_FLAG,
    OPENAI_API_KEY_ENV,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
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


class CodexAgent:
    """A Codex plugin that normalizes hooks, rollouts, quotas and compaction."""

    name = "codex"
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
        argv = await self._argv(spec)
        await pane.respawn(argv, env=self._pane_environment(spec.env), cwd=spec.cwd)
        return self._session(spec, hook_offset)

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

    def _session(self, spec: LaunchSpec, hook_offset: int) -> CodexSession:
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
            codex_home=self._codex_home,
        )

    async def _argv(self, spec: LaunchSpec) -> tuple[str, ...]:
        argv = [CODEX_COMMAND]
        if spec.resume_session_id is not None:
            argv.extend([RESUME_SUBCOMMAND, spec.resume_session_id])
        argv.extend([MODEL_FLAG, spec.rung.model])
        if spec.rung.effort is not None:
            argv.extend([CONFIG_FLAG, f'{REASONING_EFFORT_CONFIG}="{spec.rung.effort}"'])
        argv.append(BYPASS_HOOK_TRUST_FLAG)
        argv.extend(self._hook_config_args())
        argv.extend([CONFIG_FLAG, self._trust_config(spec.cwd), CD_FLAG, str(spec.cwd)])
        directories = [spec.run_dir, *spec.add_dirs, *(await self._git_common_dirs(spec))]
        for directory in _unique_paths(directories):
            argv.extend([ADD_DIR_FLAG, str(directory)])
        argv.extend(self._options.args)
        return tuple(argv)

    def _hook_config_args(self) -> list[str]:
        encoded = (
            f'[{{{_HOOKS_FIELD}=[{{{_HOOK_TYPE_FIELD}="{_HOOK_TYPE_COMMAND}",'
            f'{_HOOK_COMMAND_FIELD}="{HOOK_COMMAND}"}}]}}]'
        )
        return [item for event in HOOK_EVENTS for item in (CONFIG_FLAG, f"hooks.{event}={encoded}")]

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

    def _pane_environment(self, base: Mapping[str, str]) -> dict[str, str | None]:
        environment: dict[str, str | None] = dict(base)
        if self._options.auth == AUTH_API_KEY:
            environment[CODEX_API_KEY_ENV] = self._api_key()
            environment[OPENAI_API_KEY_ENV] = None
        else:
            environment[CODEX_API_KEY_ENV] = None
            environment[OPENAI_API_KEY_ENV] = None
        return environment

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
        output = f"{result.stdout}\n{result.stderr}".strip()
        lowered = output.lower()
        logged_in = (
            result.returncode == 0
            and _AUTH_NOT_LOGGED_IN_TEXT not in lowered
            and any(text in lowered for text in _AUTH_LOGGED_IN_TEXTS)
        )
        return CheckResult("login", logged_in, output or _INVALID_AUTH_STATUS_MESSAGE)


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
