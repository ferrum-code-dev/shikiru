"""Muse Code headless turn plugin."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import (
    BILLING_SOURCE_OPERATOR_CONFIG,
    DOCTOR_TIMEOUT_SECONDS,
    RUNTIME_DEFAULT_MODEL,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    CheckResult,
    ModelCatalog,
    assess_model_choice,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.clock import from_local
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.catalog import MuseCatalog, read_model_catalog
from shikiru.plugins.agents.muse.headless import MuseHeadless
from shikiru.plugins.agents.muse.options import (
    API_KEY_ENV_VAR,
    CONFIG_LONG_FLAG,
    CONFIG_SHORT_FLAG,
    MODEL_CATALOG_ARGS_DETAIL,
    MODEL_CATALOG_SELECTOR_FLAGS,
    MODEL_FLAG,
    MUSE_COMMAND,
    MUSE_SETTINGS,
    REASONING_EFFORT_FLAG,
    RESUME_SUBCOMMAND,
    STARTUP_PROMPT_ESTIMATE_TOKENS,
    TESTED_MUSE_VERSION,
    USAGE_ARGV,
    VERSION_ARGV,
    MuseOptions,
)
from shikiru.plugins.agents.muse.quota import parse_usage
from shikiru.plugins.agents.muse.turn import MuseTurnSequence, compaction_thresholds
from shikiru.plugins.agents.shared_settings import options_error

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import (
        AgentContext,
        HeadlessAgent,
        ModelChoiceAssessment,
        QuotaReading,
        Rung,
        Turn,
        TurnSpec,
    )
    from shikiru.agents.settings import AgentSetting
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
_UNAVAILABLE_DETAIL = "binary unavailable"
_AUTHENTICATED_DETAIL = "authenticated"
_CREDENTIALS_UNVERIFIED_DETAIL = f"{API_KEY_ENV_VAR} set; credentials not verified by doctor"
_NO_DIAGNOSTIC_DETAIL = "no diagnostic output"
_UNTESTED_VERSION_DETAIL = "{version} (warning: verified with {tested})"
_FLAG_VALUE_SEPARATOR = "="
_MODEL_CHECK = "model"
_COMPACTION_CHECK = "compaction"
_UNVERIFIED_CONTEXT_WINDOW_DETAIL = (
    "{model!r} context window is not in the catalog; its compaction threshold could not be "
    "fully verified"
)
_STARTUP_PROMPT_REACHED_DETAIL = (
    "hard threshold of {threshold} tokens for {model!r} is reached by the startup prompt "
    "estimate of {startup} tokens; the run would refuse to start"
)
_STARTUP_PROMPT_CLEARED_DETAIL = (
    "hard threshold of {threshold} tokens for {model!r} is above the startup prompt estimate "
    "of {startup} tokens"
)


class MuseAgent:
    """Build Muse Code turn processes and take-over commands."""

    name = "muse"

    def __init__(
        self,
        runner: SubprocessRunner,
        clock: Clock,
        options: MuseOptions,
        user_home: Path,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._options = options
        self._user_home = user_home
        self._reset_time = ResetTimeParser(clock, resolve_local=from_local)
        self._billing_declaration = (
            BillingDeclaration(options.billing_mode, BILLING_SOURCE_OPERATOR_CONFIG)
            if options.billing_mode is not None
            else UNKNOWN_BILLING_DECLARATION
        )
        self._cached_model_catalog: MuseCatalog | None = None

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the explicit plugin option or the unknown declaration."""
        return self._billing_declaration

    async def model_catalog(self) -> ModelCatalog:
        """Return Muse's model choices from `muse serve`, read once and then cached."""
        return (await self._catalog()).catalog

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one Muse process-per-turn exchange."""
        sequence = MuseTurnSequence(
            args=self._options.args,
            base_environment=dict(os.environ),
            clock=self._clock,
            reset_time=self._reset_time,
            user_home=self._user_home,
            context_window_tokens=None,
            compact_at_pct=self._options.compact_at_pct,
        )
        return await StreamingTurn.start(spec, self._runner, sequence)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return Muse's interactive resume command for a headless session and rung."""
        del run_dir
        argv = [MUSE_COMMAND, RESUME_SUBCOMMAND, session_id]
        if rung.model != RUNTIME_DEFAULT_MODEL:
            argv.extend((MODEL_FLAG, rung.model))
        if rung.effort is not None:
            argv.extend((REASONING_EFFORT_FLAG, rung.effort))
        argv.extend(self._options.args)
        return tuple(argv)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        """Check Muse's binary, version and login, then each model in `models`."""
        binary = await self._check_binary()
        if not binary.ok:
            return [
                binary,
                CheckResult("version", False, _UNAVAILABLE_DETAIL),
                CheckResult("login", False, _UNAVAILABLE_DETAIL),
            ]
        checks = [binary, await self._check_version(), await self._check_login()]
        if models:
            checks.extend(await self._check_models(models))
        return checks

    def read_quota(self, since: datetime) -> QuotaReading | None:
        """Return Muse's allowance reading observed after `since`, from `muse usage --json`.

        The engine calls this on a thread of its own, so the process is awaited there.
        """
        try:
            result = asyncio.run(
                self._runner.run(
                    USAGE_ARGV,
                    timeout=DOCTOR_TIMEOUT_SECONDS,
                    env=self._subprocess_environment(),
                    check=True,
                )
            )
        except FileNotFoundError as error:
            raise ShikiruError(str(error)) from error
        return parse_usage(result.stdout, since)

    def settings(self) -> tuple[AgentSetting, ...]:
        """Return the options worth writing to the project config."""
        return MUSE_SETTINGS

    def headless(self) -> HeadlessAgent | None:
        """Return Muse's schema-constrained headless client."""
        return MuseHeadless(self._runner, self._subprocess_environment())

    def _subprocess_environment(self) -> dict[str, str]:
        return dict(os.environ)

    async def _catalog(self) -> MuseCatalog:
        if self._cached_model_catalog is None:
            self._cached_model_catalog = await self._read_model_catalog()
        return self._cached_model_catalog

    async def _read_model_catalog(self) -> MuseCatalog:
        if _model_catalog_args_are_ambiguous(self._options.args):
            return MuseCatalog(
                catalog=ModelCatalog(error=MODEL_CATALOG_ARGS_DETAIL), context_windows={}
            )
        return await read_model_catalog(self._runner, self._subprocess_environment())

    async def _check_models(self, models: Sequence[str]) -> list[CheckResult]:
        muse_catalog = await self._catalog()
        assessments = tuple(
            assess_model_choice(model, None, muse_catalog.catalog) for model in models
        )
        checks = [_model_check(assessments)]
        concrete_models = tuple(model for model in models if model != RUNTIME_DEFAULT_MODEL)
        if concrete_models:
            checks.append(
                _compaction_check(self._options.compact_at_pct, concrete_models, muse_catalog)
            )
        return checks

    async def _check_binary(self) -> CheckResult:
        try:
            result = await self._runner.run(
                VERSION_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult("binary", False, str(error))
        if result.returncode != 0:
            return CheckResult("binary", False, result.stderr.strip())
        return CheckResult("binary", True, MUSE_COMMAND)

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
        if result.returncode == 0 and not _is_tested_version(detail):
            detail = _UNTESTED_VERSION_DETAIL.format(version=detail, tested=TESTED_MUSE_VERSION)
        return CheckResult("version", result.returncode == 0, detail)

    async def _check_login(self) -> CheckResult:
        environment = self._subprocess_environment()
        if environment.get(API_KEY_ENV_VAR):
            return CheckResult("login", True, _CREDENTIALS_UNVERIFIED_DETAIL)
        try:
            result = await self._runner.run(
                USAGE_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult("login", False, str(error))
        if result.returncode == 0:
            return CheckResult("login", True, _AUTHENTICATED_DETAIL)
        detail = result.stderr.strip() or result.stdout.strip() or _NO_DIAGNOSTIC_DETAIL
        return CheckResult("login", False, detail)


def _model_check(assessments: Sequence[ModelChoiceAssessment]) -> CheckResult:
    ok = all(assessment.supported is not False for assessment in assessments)
    return CheckResult(_MODEL_CHECK, ok, "; ".join(assessment.detail for assessment in assessments))


def _compaction_check(
    compact_at_pct: float, models: Sequence[str], muse_catalog: MuseCatalog
) -> CheckResult:
    _, hard_threshold = compaction_thresholds(compact_at_pct)
    ok = True
    details: list[str] = []
    for model in models:
        window = muse_catalog.context_windows.get(model)
        if window is None:
            details.append(_UNVERIFIED_CONTEXT_WINDOW_DETAIL.format(model=model))
            continue
        threshold = hard_threshold * window
        reached = threshold <= STARTUP_PROMPT_ESTIMATE_TOKENS
        ok = ok and not reached
        template = _STARTUP_PROMPT_REACHED_DETAIL if reached else _STARTUP_PROMPT_CLEARED_DETAIL
        details.append(
            template.format(
                threshold=round(threshold),
                model=model,
                startup=STARTUP_PROMPT_ESTIMATE_TOKENS,
            )
        )
    return CheckResult(_COMPACTION_CHECK, ok, "; ".join(details))


def _is_tested_version(output: str) -> bool:
    match = _VERSION_PATTERN.search(output)
    return match is not None and match.group() == TESTED_MUSE_VERSION


def _model_catalog_args_are_ambiguous(args: Sequence[str]) -> bool:
    return any(_is_model_catalog_selector(argument) for argument in args)


def _is_model_catalog_selector(argument: str) -> bool:
    return _flag_name(argument) in MODEL_CATALOG_SELECTOR_FLAGS or _is_config_override(argument)


def _is_config_override(argument: str) -> bool:
    return argument.startswith(CONFIG_SHORT_FLAG) or _flag_name(argument) == CONFIG_LONG_FLAG


def _flag_name(argument: str) -> str:
    return argument.partition(_FLAG_VALUE_SEPARATOR)[0]


def create(context: AgentContext) -> MuseAgent:
    """Build the Muse plugin from validated `AgentContext.options`."""
    try:
        options = MuseOptions.model_validate(context.options)
    except ValidationError as error:
        raise options_error(MUSE_COMMAND, error) from error
    return MuseAgent(context.runner, context.clock, options, Path.home())
