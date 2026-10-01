"""Codex headless turn plugin."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from shikiru.agents.contracts import CheckResult
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.codex.headless import CodexHeadless
from shikiru.plugins.agents.codex.home import codex_home
from shikiru.plugins.agents.codex.options import (
    CODEX_COMMAND,
    CODEX_HOME_ENV_VAR,
    DOCTOR_TIMEOUT_SECONDS,
    GIT_DIRS_ARGV,
    GIT_DIRS_TIMEOUT_SECONDS,
    LOGIN_STATUS_ARGV,
    NO_DAEMON_FLAG,
    RESUME_SUBCOMMAND,
    VERSION_ARGV,
    CodexOptions,
)
from shikiru.plugins.agents.codex.turn import CodexTurnSequence, context_window

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import AgentContext, HeadlessAgent, Turn, TurnSpec
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

_AUTH_NOT_LOGGED_IN_TEXT = "not logged in"
_AUTH_LOGGED_IN_TEXTS = ("logged in", "authenticated")
_CODEX_CONFIG_FILENAME = "config.toml"
_MODEL_PROVIDER_FIELD = "model_provider"
_MODEL_PROVIDERS_FIELD = "model_providers"
_OPENAI_BASE_URL_FIELD = "openai_base_url"
_PROFILES_FIELD = "profiles"
_PROFILE_FIELD = "profile"
_CONTEXT_WINDOW_CHECK = "context_window"
_UNKNOWN_WINDOW_DETAIL = (
    "no known context window for {models} in Codex's model list; "
    "compaction at compact_at_pct cannot be applied"
)
_CUSTOM_PROVIDER_ACCESS_DETAIL = (
    "custom provider configured; provider credentials are not verified by doctor"
)


class CodexAgent:
    """Build Codex turn processes and health checks."""

    name = "codex"

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: CodexOptions) -> None:
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._codex_home = codex_home()

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one Codex process-per-turn exchange."""
        git_dirs = await self._git_dirs_for_turn(spec)
        sequence = self.process_sequence(spec, git_dirs=git_dirs)
        return await StreamingTurn.start(spec, self._runner, sequence)

    def process_sequence(
        self, spec: TurnSpec, *, git_dirs: Sequence[Path] | None = None
    ) -> CodexTurnSequence:
        """Build Codex's prompt and context-failure retry sequence for `spec`."""
        home = Path(spec.env.get(CODEX_HOME_ENV_VAR, str(self._codex_home)))
        return CodexTurnSequence(
            spec,
            args=self._options.args,
            environment=self._turn_environment,
            clock=self._clock,
            reset_time=self._reset_time,
            home=home,
            git_dirs=git_dirs,
            compact_at_pct=self._options.compact_at_pct,
        )

    def interactive_command(self, session_id: str, run_dir: Path) -> Sequence[str]:
        """Return Codex's interactive resume command for a headless session."""
        del run_dir
        return (
            CODEX_COMMAND,
            RESUME_SUBCOMMAND,
            NO_DAEMON_FLAG,
            session_id,
            *self._options.args,
        )

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        """Check Codex's binary, version, native access and each model's context window."""
        binary = await self._check_binary()
        if not binary.ok:
            unavailable = "binary unavailable"
            return [
                binary,
                CheckResult("version", False, unavailable),
                CheckResult("login", False, unavailable),
            ]
        checks = [binary, await self._check_version(), await self._check_login()]
        if models:
            checks.append(self._check_context_windows(models))
        return checks

    def _check_context_windows(self, models: Sequence[str]) -> CheckResult:
        windows = {model: context_window(self._codex_home, model) for model in models}
        unknown = [model for model, window in windows.items() if window is None]
        if unknown:
            return CheckResult(
                _CONTEXT_WINDOW_CHECK,
                False,
                _UNKNOWN_WINDOW_DETAIL.format(models=", ".join(unknown)),
            )
        known = ", ".join(f"{model}: {window}" for model, window in windows.items())
        return CheckResult(_CONTEXT_WINDOW_CHECK, True, known)

    def headless(self) -> HeadlessAgent | None:
        """Return Codex's schema-constrained headless client."""
        return CodexHeadless(self._runner, self._subprocess_environment())

    def _subprocess_environment(self) -> dict[str, str]:
        return dict(os.environ)

    def _turn_environment(self, spec: TurnSpec) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(spec.env)
        return environment

    async def _git_dirs_for_turn(self, spec: TurnSpec) -> tuple[Path, ...]:
        result = await self._runner.run(
            GIT_DIRS_ARGV,
            timeout=GIT_DIRS_TIMEOUT_SECONDS,
            cwd=spec.cwd,
            check=False,
        )
        values = result.stdout.split()
        if result.returncode != 0 or not values:
            message = (
                f"could not resolve the Codex workspace git directories: {result.stderr.strip()}"
            )
            raise ConfigError(message)
        return tuple(Path(value) for value in values)

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
        configured_provider = _custom_provider_check(self._codex_home)
        if configured_provider is not None:
            return configured_provider
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


def _custom_provider_check(home: Path) -> CheckResult | None:
    path = home / _CODEX_CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        config = cast("Mapping[str, object]", tomllib.loads(path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return CheckResult("login", False, f"could not inspect Codex config: {error}")
    profile_name = config.get(_PROFILE_FIELD)
    profiles_value = config.get(_PROFILES_FIELD)
    profiles: Mapping[str, object] = (
        cast("Mapping[str, object]", profiles_value) if isinstance(profiles_value, Mapping) else {}
    )
    profile_value: object | None = (
        profiles.get(profile_name) if isinstance(profile_name, str) else None
    )
    profile: Mapping[str, object] = (
        cast("Mapping[str, object]", profile_value) if isinstance(profile_value, Mapping) else {}
    )
    provider_name: object = profile.get(_MODEL_PROVIDER_FIELD, config.get(_MODEL_PROVIDER_FIELD))
    providers_value = config.get(_MODEL_PROVIDERS_FIELD)
    providers: Mapping[str, object] = (
        cast("Mapping[str, object]", providers_value)
        if isinstance(providers_value, Mapping)
        else {}
    )
    custom_provider = isinstance(provider_name, str) and isinstance(
        providers.get(provider_name), Mapping
    )
    base_url: object = profile.get(_OPENAI_BASE_URL_FIELD, config.get(_OPENAI_BASE_URL_FIELD))
    custom_base_url = isinstance(base_url, str) and bool(base_url.strip())
    if custom_provider or custom_base_url:
        return CheckResult("login", True, _CUSTOM_PROVIDER_ACCESS_DETAIL)
    return None


def create(context: AgentContext) -> CodexAgent:
    """Build the Codex plugin from validated `AgentContext.options`."""
    try:
        options = CodexOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the Codex agent: {error}"
        raise ConfigError(message) from error
    return CodexAgent(context.runner, context.clock, options)
