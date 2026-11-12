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
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.clock import from_local
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.options import (
    API_KEY_ENV_VAR,
    MODEL_FLAG,
    MUSE_COMMAND,
    MUSE_SETTINGS,
    REASONING_EFFORT_FLAG,
    RESUME_SUBCOMMAND,
    TESTED_MUSE_VERSION,
    USAGE_ARGV,
    VERSION_ARGV,
    MuseOptions,
)
from shikiru.plugins.agents.muse.quota import parse_usage
from shikiru.plugins.agents.muse.turn import MuseTurnSequence
from shikiru.plugins.agents.shared_settings import options_error

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import AgentContext, QuotaReading, Rung, Turn, TurnSpec
    from shikiru.agents.settings import AgentSetting
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
_UNAVAILABLE_DETAIL = "binary unavailable"
_AUTHENTICATED_DETAIL = "authenticated"
_CREDENTIALS_UNVERIFIED_DETAIL = f"{API_KEY_ENV_VAR} set; credentials not verified by doctor"
_NO_DIAGNOSTIC_DETAIL = "no diagnostic output"
_UNTESTED_VERSION_DETAIL = "{version} (warning: verified with {tested})"


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

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the explicit plugin option or the unknown declaration."""
        return self._billing_declaration

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
        """Check Muse's binary, version and login."""
        del models
        binary = await self._check_binary()
        if not binary.ok:
            return [
                binary,
                CheckResult("version", False, _UNAVAILABLE_DETAIL),
                CheckResult("login", False, _UNAVAILABLE_DETAIL),
            ]
        return [binary, await self._check_version(), await self._check_login()]

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

    def _subprocess_environment(self) -> dict[str, str]:
        return dict(os.environ)

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


def _is_tested_version(output: str) -> bool:
    match = _VERSION_PATTERN.search(output)
    return match is not None and match.group() == TESTED_MUSE_VERSION


def create(context: AgentContext) -> MuseAgent:
    """Build the Muse plugin from validated `AgentContext.options`."""
    try:
        options = MuseOptions.model_validate(context.options)
    except ValidationError as error:
        raise options_error(MUSE_COMMAND, error) from error
    return MuseAgent(context.runner, context.clock, options, Path.home())
