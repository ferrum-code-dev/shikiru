"""Codex headless turn plugin."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from shikiru.agents.contracts import (
    BILLING_SOURCE_OPERATOR_CONFIG,
    DOCTOR_TIMEOUT_SECONDS,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    CheckResult,
    ModelCatalog,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.clock import from_local
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.codex.headless import CodexHeadless
from shikiru.plugins.agents.codex.home import codex_home
from shikiru.plugins.agents.codex.model_catalog import (
    parse_bundled_context_windows,
    parse_catalog,
)
from shikiru.plugins.agents.codex.options import (
    CODEX_BUNDLED_MODELS_ARGV,
    CODEX_COMMAND,
    CODEX_HOME_ENV_VAR,
    CODEX_MODEL_CATALOG_ARGV,
    CONFIG_FLAG,
    CONFIG_LONG_FLAG,
    EFFORT_LONG_FLAG,
    GIT_DIRS_ARGV,
    GIT_DIRS_TIMEOUT_SECONDS,
    LOGIN_STATUS_ARGV,
    MODEL_CATALOG_ARGS_DETAIL,
    MODEL_CONFIG_OVERRIDE,
    MODEL_FLAG,
    MODEL_LONG_FLAG,
    MODEL_OVERRIDE,
    NO_DAEMON_FLAG,
    PROFILE_FLAG,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    TESTED_CODEX_VERSION,
    VERSION_ARGV,
    CodexOptions,
)
from shikiru.plugins.agents.codex.quota import CodexQuotaProbe
from shikiru.plugins.agents.codex.sandbox_probe import CodexSandboxProbe
from shikiru.plugins.agents.codex.turn import CodexTurnSequence, context_window

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import (
        AgentContext,
        HeadlessAgent,
        QuotaReading,
        Rung,
        Turn,
        TurnSpec,
    )
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
_PARTITION_VALUE_INDEX = 2
_CONTEXT_WINDOW_CHECK = "context_window"
_UNTESTED_VERSION_DETAIL = "{version} (warning: verified with {tested}; re-check the sandbox probe)"
_UNKNOWN_WINDOW_DETAIL = (
    "no positive context window for {models} in models_cache.json or Codex CLI's bundled "
    "model catalog; compaction at compact_at_pct cannot be applied"
)
_MODELS_CACHE_SOURCE = "models_cache.json"
_BUNDLED_CATALOG_SOURCE = "Codex CLI bundled model catalog"
_CURRENT_CATALOG_SOURCE = "Codex CLI current model catalog"
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
        self._reset_time = ResetTimeParser(clock, resolve_local=from_local)
        self._billing_declaration = (
            BillingDeclaration(options.billing_mode, BILLING_SOURCE_OPERATOR_CONFIG)
            if options.billing_mode is not None
            else UNKNOWN_BILLING_DECLARATION
        )
        self._codex_home = codex_home()
        self._quota_probe = CodexQuotaProbe(self._codex_home, clock, self._reset_time)
        self._sandbox_probe = CodexSandboxProbe(runner)
        self._catalog_context_windows: dict[str, int] = {}
        self._bundled_context_windows: dict[str, int] = {}
        self._cached_model_catalog: ModelCatalog | None = None

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the explicit plugin option or the unknown declaration."""
        return self._billing_declaration

    def read_quota(self, since: datetime) -> QuotaReading | None:
        """Return the newest Codex-bucket reading in this account's rollouts after `since`."""
        return self._quota_probe.latest_reading(since)

    async def model_catalog(self) -> ModelCatalog:
        """Return choices from Codex CLI's refreshed current model catalog."""
        if self._cached_model_catalog is None:
            self._cached_model_catalog = await self._read_model_catalog()
        return self._cached_model_catalog

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one Codex process-per-turn exchange."""
        model_context_windows = await self._context_windows_for_turn(spec)
        git_dirs = await self._git_dirs_for_turn(spec)
        sequence = self.process_sequence(
            spec, git_dirs=git_dirs, model_context_windows=model_context_windows
        )
        return await StreamingTurn.start(spec, self._runner, sequence)

    def process_sequence(
        self,
        spec: TurnSpec,
        *,
        git_dirs: Sequence[Path],
        model_context_windows: Mapping[str, int] | None = None,
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
            model_context_windows=(
                self._bundled_context_windows
                if model_context_windows is None
                else model_context_windows
            ),
        )

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return Codex's interactive resume command for a headless session and rung."""
        del run_dir
        argv = [
            CODEX_COMMAND,
            RESUME_SUBCOMMAND,
            NO_DAEMON_FLAG,
            session_id,
            MODEL_FLAG,
            rung.model,
        ]
        if rung.effort is not None:
            argv.extend((CONFIG_FLAG, f'{REASONING_EFFORT_CONFIG}="{rung.effort}"'))
        argv.extend(self._options.args)
        return tuple(argv)

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
        checks = [
            binary,
            await self._check_version(),
            await self._check_login(),
            await self._sandbox_probe.check(),
        ]
        if models:
            checks.append(await self._check_context_windows(models))
        return checks

    async def _check_context_windows(self, models: Sequence[str]) -> CheckResult:
        cached_windows = {model: context_window(self._codex_home, model) for model in models}
        missing_from_catalog = [
            model
            for model, window in cached_windows.items()
            if window is None and model not in self._catalog_context_windows
        ]
        if missing_from_catalog:
            catalog_result = await self._read_bundled_model_catalog(missing_from_catalog)
            if isinstance(catalog_result, CheckResult):
                return catalog_result
            self._bundled_context_windows = catalog_result
            self._catalog_context_windows.update(catalog_result)
        windows = {
            model: cached_windows[model]
            if cached_windows[model] is not None
            else self._catalog_context_windows.get(model)
            for model in models
        }
        unknown = [model for model, window in windows.items() if window is None]
        if unknown:
            return CheckResult(
                _CONTEXT_WINDOW_CHECK,
                False,
                _UNKNOWN_WINDOW_DETAIL.format(models=", ".join(unknown)),
            )
        known_parts: list[str] = []
        for model, window in windows.items():
            if cached_windows[model] is not None:
                source = _MODELS_CACHE_SOURCE
            elif model in self._bundled_context_windows:
                source = _BUNDLED_CATALOG_SOURCE
            else:
                source = _CURRENT_CATALOG_SOURCE
            known_parts.append(f"{model}: {window} ({source})")
        known = ", ".join(known_parts)
        return CheckResult(_CONTEXT_WINDOW_CHECK, True, known)

    async def _read_model_catalog(self) -> ModelCatalog:
        if _model_catalog_args_are_ambiguous(self._options.args):
            return ModelCatalog(error=MODEL_CATALOG_ARGS_DETAIL)
        return await self._query_model_catalog()

    async def _query_model_catalog(self) -> ModelCatalog:
        try:
            result = await self._runner.run(
                CODEX_MODEL_CATALOG_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            detail = str(error) or type(error).__name__
            return ModelCatalog(error=f"could not read Codex CLI's current model catalog: {detail}")
        if result.returncode != 0:
            detail = result.stderr.strip() or "no diagnostic output"
            return ModelCatalog(
                error=(
                    f"Codex CLI's current model catalog command failed with exit code "
                    f"{result.returncode}: {detail}"
                )
            )
        try:
            choices, windows = parse_catalog(result.stdout)
        except TypeError as error:
            return ModelCatalog(error=f"could not parse Codex CLI's current model catalog: {error}")
        self._catalog_context_windows.update(windows)
        return ModelCatalog(models=choices, complete=True)

    async def _read_bundled_model_catalog(
        self,
        models: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, int] | CheckResult:
        model_names = ", ".join(models)
        try:
            result = await self._runner.run(
                CODEX_BUNDLED_MODELS_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=(self._subprocess_environment() if environment is None else environment),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult(
                _CONTEXT_WINDOW_CHECK,
                False,
                f"could not read Codex CLI's bundled model catalog for {model_names}: {error}",
            )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            return CheckResult(
                _CONTEXT_WINDOW_CHECK,
                False,
                f"Codex CLI's bundled model catalog command failed for {model_names} with exit "
                f"code {result.returncode}: {detail or 'no diagnostic output'}",
            )
        try:
            return parse_bundled_context_windows(result.stdout)
        except TypeError as error:
            return CheckResult(
                _CONTEXT_WINDOW_CHECK,
                False,
                f"could not parse Codex CLI's bundled model catalog for {model_names}: {error}",
            )

    async def _context_windows_for_turn(self, spec: TurnSpec) -> Mapping[str, int]:
        home = Path(spec.env.get(CODEX_HOME_ENV_VAR, str(self._codex_home)))
        model = spec.rung.model
        if context_window(home, model, spec.session_id) is not None:
            return self._bundled_context_windows
        if model in self._bundled_context_windows:
            return self._bundled_context_windows
        catalog = await self._read_bundled_model_catalog(
            (model,), environment=self._turn_environment(spec)
        )
        if isinstance(catalog, CheckResult):
            raise ConfigError(catalog.detail)
        self._bundled_context_windows = catalog
        if model not in catalog:
            raise ConfigError(_UNKNOWN_WINDOW_DETAIL.format(models=model))
        return catalog

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
        if result.returncode == 0 and TESTED_CODEX_VERSION not in detail:
            detail = _UNTESTED_VERSION_DETAIL.format(version=detail, tested=TESTED_CODEX_VERSION)
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


def _model_catalog_args_are_ambiguous(args: Sequence[str]) -> bool:
    return any(
        _catalog_argument_is_ambiguous(args, index, argument) for index, argument in enumerate(args)
    )


def _catalog_argument_is_ambiguous(args: Sequence[str], index: int, argument: str) -> bool:
    selector_flags = (MODEL_FLAG, MODEL_LONG_FLAG, EFFORT_LONG_FLAG, PROFILE_FLAG)
    selector_prefixes = (f"{MODEL_LONG_FLAG}=", f"{PROFILE_FLAG}=", f"{EFFORT_LONG_FLAG}=")
    if argument in selector_flags or argument.startswith(selector_prefixes):
        return True
    if argument.startswith(MODEL_FLAG) and argument != MODEL_FLAG:
        return True
    if argument in (CONFIG_FLAG, CONFIG_LONG_FLAG):
        return _config_override_is_ambiguous(args, index + 1)
    if argument.startswith(f"{CONFIG_LONG_FLAG}="):
        return _config_value_is_ambiguous(argument.partition("=")[_PARTITION_VALUE_INDEX])
    if argument.startswith(CONFIG_FLAG) and argument != CONFIG_FLAG:
        return _config_value_is_ambiguous(argument[len(CONFIG_FLAG) :])
    return False


def _config_override_is_ambiguous(args: Sequence[str], index: int) -> bool:
    return index >= len(args) or _config_value_is_ambiguous(args[index])


def _config_value_is_ambiguous(value: str) -> bool:
    key = value.partition("=")[0].strip()
    return key in (MODEL_CONFIG_OVERRIDE, MODEL_OVERRIDE, REASONING_EFFORT_CONFIG)


def create(context: AgentContext) -> CodexAgent:
    """Build the Codex plugin from validated `AgentContext.options`."""
    try:
        options = CodexOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the Codex agent: {error}"
        raise ConfigError(message) from error
    return CodexAgent(context.runner, context.clock, options)
