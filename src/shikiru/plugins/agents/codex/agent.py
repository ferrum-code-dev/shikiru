"""Codex `InteractiveAgent` plugin."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import CheckResult, QuotaWindow, SessionKeys, shikiru_command
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.codex.headless import CodexHeadless
from shikiru.plugins.agents.codex.options import (
    ADD_DIR_FLAG,
    APPROVE_FOR_ME_FLAG,
    AUTH_API_KEY,
    BYPASS_HOOK_TRUST_FLAG,
    CD_FLAG,
    CODEX_API_KEY_ENV,
    CODEX_COMMAND,
    CODEX_HOME_ENV_VAR,
    CONFIG_FLAG,
    DOCTOR_TIMEOUT_SECONDS,
    GIT_COMMON_DIR_TIMEOUT_SECONDS,
    HOOK_EVENTS,
    HOOK_SUBCOMMAND,
    LOGIN_STATUS_ARGV,
    MODEL_FLAG,
    OPENAI_API_KEY_ENV,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    UNATTENDED_CONFIGS,
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
from shikiru.plugins.agents.codex.turn import CodexTurnSequence

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shikiru.agents.contracts import (
        AgentContext,
        AgentSession,
        HeadlessAgent,
        LaunchSpec,
        PaneHandle,
        Turn,
        TurnSpec,
    )
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

_GIT_COMMON_DIR_ARGV = ("git", "rev-parse", "--path-format=absolute", "--git-common-dir")
_AUTH_NOT_LOGGED_IN_TEXT = "not logged in"
_AUTH_LOGGED_IN_TEXTS = ("logged in", "authenticated")
_INVALID_AUTH_STATUS_MESSAGE = "Codex login status did not return a valid result"
_HOOKS_FIELD = "hooks"
_HOOK_TYPE_FIELD = "type"
_HOOK_COMMAND_FIELD = "command"
_HOOK_TYPE_COMMAND = "command"
_TRUST_LEVEL = "trusted"
STARTUP_TIMEOUT_SECONDS = 45.0
STARTUP_POLL_SECONDS = 0.1
STARTUP_STABILITY_COUNT = 10
READY_MARKER = "Ask Codex to do anything"
TRUST_PROMPT_MARKERS = (
    "Do you trust the contents of this directory?",
    "Quick safety check: Is this a project you created or one you trust?",
    "Trusting will apply to the repository root:",
    "Trust this folder?",
)
ACCEPT_TRUST_KEYS = ("1", "Enter")


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
        self._codex_home = codex_home()
        self._quota_probe = CodexQuotaProbe(self._codex_home, clock, self._reset_time)

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one Codex process-per-turn exchange."""
        common_dir = await self._git_common_dir_for_turn(spec)
        sequence = self.process_sequence(spec, git_common_dir=common_dir)
        return await StreamingTurn.start(spec, self._runner, sequence)

    def process_sequence(
        self, spec: TurnSpec, *, git_common_dir: Path | None = None
    ) -> CodexTurnSequence:
        """Build the Codex prompt and context-failure retry sequence for `spec`."""
        home = Path(spec.env.get(CODEX_HOME_ENV_VAR, str(self._codex_home)))
        return CodexTurnSequence(
            spec,
            args=_headless_args(self._options.args),
            environment=self._turn_environment,
            clock=self._clock,
            reset_time=self._reset_time,
            home=home,
            git_common_dir=git_common_dir,
            compact_at_pct=self._options.compact_at_pct,
        )

    def interactive_command(self, session_id: str, cwd: Path) -> tuple[str, ...]:
        """Return Codex's interactive resume command for a headless session."""
        del cwd
        return (CODEX_COMMAND, RESUME_SUBCOMMAND, session_id, *self._options.args)

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Launch a Codex session in `pane` and return once its input prompt is ready."""
        hook_offset = await asyncio.to_thread(hook_file_size, spec.run_dir, spec.role)
        await pane.respawn(
            await self._argv(spec),
            env=self._pane_environment(spec.env),
            cwd=spec.cwd,
        )
        await self._await_ready(pane)
        return self._session(spec, hook_offset)

    async def _await_ready(self, pane: PaneHandle) -> None:
        deadline = self._clock.now() + timedelta(seconds=STARTUP_TIMEOUT_SECONDS)
        trust_observations = 0
        ready_observations = 0
        while self._clock.now() < deadline and await pane.is_alive():
            visible = await pane.capture_visible()
            trust_observations = (
                trust_observations + 1
                if any(marker in visible for marker in TRUST_PROMPT_MARKERS)
                else 0
            )
            ready_observations = ready_observations + 1 if READY_MARKER in visible else 0
            if ready_observations >= STARTUP_STABILITY_COUNT:
                return
            if trust_observations >= STARTUP_STABILITY_COUNT:
                await pane.send_keys(*ACCEPT_TRUST_KEYS)
                trust_observations = 0
            await self._clock.sleep(STARTUP_POLL_SECONDS)

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
        for config in UNATTENDED_CONFIGS:
            argv.extend([CONFIG_FLAG, config])
        argv.extend(self._hook_config_args(shikiru_command(HOOK_SUBCOMMAND)))
        argv.extend([CONFIG_FLAG, self._trust_config(spec.cwd), CD_FLAG, str(spec.cwd)])
        directories = [spec.run_dir, *spec.add_dirs, *(await self._git_common_dirs(spec))]
        for directory in _unique_paths(directories):
            argv.extend([ADD_DIR_FLAG, str(directory)])
        argv.extend(self._options.args)
        return tuple(argv)

    def _hook_config_args(self, hook_command: str) -> list[str]:
        encoded = (
            f'[{{{_HOOKS_FIELD}=[{{{_HOOK_TYPE_FIELD}="{_HOOK_TYPE_COMMAND}",'
            f"{_HOOK_COMMAND_FIELD}={json.dumps(hook_command)}}}]}}]"
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
        environment[CODEX_HOME_ENV_VAR] = str(self._codex_home)
        if self._options.auth == AUTH_API_KEY:
            environment[CODEX_API_KEY_ENV] = self._api_key()
            environment[OPENAI_API_KEY_ENV] = None
        else:
            environment[CODEX_API_KEY_ENV] = None
            environment[OPENAI_API_KEY_ENV] = None
        return environment

    def _subprocess_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment[CODEX_HOME_ENV_VAR] = str(self._codex_home)
        return self._authenticated_environment(environment)

    def _turn_environment(self, spec: TurnSpec) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(spec.env)
        environment.setdefault(CODEX_HOME_ENV_VAR, str(self._codex_home))
        return self._authenticated_environment(environment)

    def _authenticated_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        environment = dict(base)
        if self._options.auth == AUTH_API_KEY:
            environment[CODEX_API_KEY_ENV] = self._api_key(environment)
            environment.pop(OPENAI_API_KEY_ENV, None)
        else:
            environment.pop(CODEX_API_KEY_ENV, None)
            environment.pop(OPENAI_API_KEY_ENV, None)
        return environment

    def _api_key(self, environment: Mapping[str, str] | None = None) -> str:
        source = environment if environment is not None else os.environ
        value = source.get(self._options.api_key_env) or os.environ.get(self._options.api_key_env)
        if not value:
            message = f"Codex API key environment variable {self._options.api_key_env!r} is unset"
            raise ConfigError(message)
        return value

    async def _git_common_dir_for_turn(self, spec: TurnSpec) -> Path:
        result = await self._runner.run(
            _GIT_COMMON_DIR_ARGV,
            timeout=GIT_COMMON_DIR_TIMEOUT_SECONDS,
            cwd=spec.cwd,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            message = (
                "could not resolve the Codex workspace git common directory: "
                f"{result.stderr.strip()}"
            )
            raise ConfigError(message)
        return Path(value)

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


def _headless_args(args: list[str]) -> tuple[str, ...]:
    return tuple(argument for argument in args if argument != APPROVE_FOR_ME_FLAG)


def create(context: AgentContext) -> CodexAgent:
    """Build the Codex plugin from validated `AgentContext.options`."""
    try:
        options = CodexOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the Codex agent: {error}"
        raise ConfigError(message) from error
    return CodexAgent(context.runner, context.clock, options)
