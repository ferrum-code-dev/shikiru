"""Claude Code headless turn plugin."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from shikiru.agents.contracts import (
    BILLING_SOURCE_OPERATOR_CONFIG,
    DOCTOR_TIMEOUT_SECONDS,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
    CheckResult,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.claude.headless import ClaudeHeadless
from shikiru.plugins.agents.claude.options import (
    ADD_DIR_FLAG,
    CLAUDE_COMMAND,
    EFFORT_FLAG,
    MODEL_FLAG,
    RESUME_FLAG,
    ClaudeOptions,
)
from shikiru.plugins.agents.claude.turn import ClaudeTurnSequence

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.agents.contracts import AgentContext, HeadlessAgent, Rung, Turn, TurnSpec
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

CLAUDE_VERSION_ARGV = (CLAUDE_COMMAND, "--version")
AUTH_STATUS_ARGV = (CLAUDE_COMMAND, "auth", "status", "--json")
_AUTH_STATUS_LOGGED_IN_FIELD = "loggedIn"
_INVALID_AUTH_STATUS_MESSAGE = "Claude auth status did not return valid JSON"


class ClaudeAgent:
    """Build Claude Code turn processes and health checks."""

    name = "claude"

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: ClaudeOptions) -> None:
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._billing_declaration = (
            BillingDeclaration(options.billing_mode, BILLING_SOURCE_OPERATOR_CONFIG)
            if options.billing_mode is not None
            else UNKNOWN_BILLING_DECLARATION
        )

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the explicit plugin option or the unknown declaration (04 §1)."""
        return self._billing_declaration

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one process-per-turn Claude Code exchange."""
        return await StreamingTurn.start(spec, self._runner, self.process_sequence(spec))

    def process_sequence(self, spec: TurnSpec) -> ClaudeTurnSequence:
        """Build the compact, prompt and retry process sequence for `spec`."""
        return ClaudeTurnSequence(
            spec,
            clock=self._clock,
            reset_time=self._reset_time,
            compact_at_pct=self._options.compact_at_pct,
            args=self._options.args,
            environment=self._turn_environment,
        )

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return Claude's interactive resume command for a headless session and rung."""
        argv = [CLAUDE_COMMAND, RESUME_FLAG, session_id, MODEL_FLAG, rung.model]
        if rung.effort is not None:
            argv.extend((EFFORT_FLAG, rung.effort))
        argv.extend((ADD_DIR_FLAG, str(run_dir), *self._options.args))
        return tuple(argv)

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        """Check Claude's binary, version and authentication."""
        del models
        binary = await self._check_binary()
        if not binary.ok:
            unavailable = "binary unavailable"
            return [
                binary,
                CheckResult("version", False, unavailable),
                CheckResult("login", False, unavailable),
            ]
        return [binary, await self._check_version(), await self._check_login()]

    def headless(self) -> HeadlessAgent | None:
        """Return Claude's schema-constrained headless client."""
        return ClaudeHeadless(self._runner, self._subprocess_environment())

    def _subprocess_environment(self) -> dict[str, str]:
        return dict(os.environ)

    def _turn_environment(self, spec: TurnSpec) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(spec.env)
        return environment

    async def _check_binary(self) -> CheckResult:
        try:
            result = await self._runner.run(
                CLAUDE_VERSION_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, CommandFailed, ShikiruError) as error:
            return CheckResult("binary", False, str(error))
        if result.returncode != 0:
            return CheckResult("binary", False, result.stderr.strip())
        return CheckResult("binary", True, CLAUDE_COMMAND)

    async def _check_version(self) -> CheckResult:
        try:
            result = await self._runner.run(
                CLAUDE_VERSION_ARGV,
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
                AUTH_STATUS_ARGV,
                timeout=DOCTOR_TIMEOUT_SECONDS,
                env=self._subprocess_environment(),
                check=False,
            )
        except (FileNotFoundError, ShikiruError) as error:
            return CheckResult("login", False, str(error))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return CheckResult("login", False, _INVALID_AUTH_STATUS_MESSAGE)
        if not isinstance(payload, Mapping):
            return CheckResult("login", False, _INVALID_AUTH_STATUS_MESSAGE)
        typed_payload = cast("Mapping[str, object]", payload)
        authenticated = (
            result.returncode == 0 and typed_payload.get(_AUTH_STATUS_LOGGED_IN_FIELD) is True
        )
        detail = "authenticated" if authenticated else "not authenticated"
        return CheckResult("login", authenticated, detail)


def create(context: AgentContext) -> ClaudeAgent:
    """Build the Claude Code plugin from validated `AgentContext.options`."""
    try:
        options = ClaudeOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the Claude agent: {error}"
        raise ConfigError(message) from error
    return ClaudeAgent(context.runner, context.clock, options)
