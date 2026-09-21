"""Claude Code `InteractiveAgent` plugin."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from pydantic import ValidationError

from shikiru.agents.contracts import CheckResult, QuotaWindow, SessionKeys
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.probe import PROBE_ENV_VALUE, PROBE_ENV_VAR
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.errors import CommandFailed, ConfigError, ShikiruError
from shikiru.plugins.agents.claude.headless import ClaudeHeadless
from shikiru.plugins.agents.claude.options import (
    ADD_DIR_FLAG,
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_AUTH_TOKEN_ENV,
    AUTH_API_KEY,
    CLAUDE_COMMAND,
    EFFORT_FLAG,
    HOOK_COMMAND,
    MODEL_FLAG,
    PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_FLAG,
    RESUME_FLAG,
    SESSION_ID_FLAG,
    SETTINGS_FLAG,
    ClaudeOptions,
)
from shikiru.plugins.agents.claude.quota import ClaudeQuotaProbe
from shikiru.plugins.agents.claude.session import ClaudeSession, hook_file_size, restored_session

if TYPE_CHECKING:
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
CLAUDE_VERSION_ARGV = (CLAUDE_COMMAND, "--version")
AUTH_STATUS_ARGV = (CLAUDE_COMMAND, "auth", "status", "--json")
DOCTOR_TIMEOUT_SECONDS = 60.0
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "StopFailure",
    "Notification",
    "PostCompact",
    "SessionEnd",
)
_HOOKS_FIELD = "hooks"
_HOOKS_LIST_FIELD = "hooks"
_HOOK_TYPE_FIELD = "type"
_HOOK_COMMAND_FIELD = "command"
_HOOK_TYPE_COMMAND = "command"
_AUTH_STATUS_LOGGED_IN_FIELD = "loggedIn"
_INVALID_AUTH_STATUS_MESSAGE = "Claude auth status did not return valid JSON"
PROBE_PREPARE_TIMEOUT_SECONDS = 45.0
PROBE_PREPARE_POLL_SECONDS = 0.1
PROBE_PROMPT_STABILITY_COUNT = 10
TRUST_PROMPT_MARKER = "Yes, I trust this folder"
TRUST_CONFIRM_MARKER = "Enter to confirm"
BYPASS_PERMISSION_MARKER = "WARNING: Claude Code running in Bypass Permissions mode"
BYPASS_CONFIRM_MARKER = "Yes, I accept"
PROBE_STARTUP_TIMEOUT_MESSAGE = "Claude trust prompt or session startup did not complete"


@runtime_checkable
class _StartupPane(Protocol):
    async def capture_visible(self) -> str: ...

    async def is_alive(self) -> bool: ...

    async def send_keys(self, *keys: str) -> None: ...


class ClaudeAgent:
    """A Claude Code plugin that normalizes hooks, transcripts and quota."""

    name = "claude"
    keys = SessionKeys(
        interrupt=("Escape",),
        exit_input="/exit",
        safe=("Enter", "Escape", "Down", "1", "2"),
    )

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: ClaudeOptions) -> None:
        """Hold validated options and injected process and clock boundaries."""
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._prices = PriceTable.from_toml(PRICES_PATH)
        self._quota_probe = ClaudeQuotaProbe(
            runner,
            clock,
            self._reset_time,
            self._subprocess_environment,
        )

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Launch a new or resumable Claude Code session in `pane`."""
        session_id = spec.resume_session_id or str(uuid.uuid4())
        hook_offset = await asyncio.to_thread(hook_file_size, spec.run_dir, spec.role)
        await pane.respawn(
            self._argv(spec, session_id),
            env=self._pane_environment(spec.env),
            cwd=spec.cwd,
        )
        await self._prepare_startup(spec, pane)
        return self._session(spec, session_id, hook_offset)

    async def prepare_probe(self, spec: LaunchSpec, pane: PaneHandle) -> bool:
        """Accept Claude's first-use trust prompt while a doctor probe starts."""
        await self._prepare_startup(spec, pane)
        return False

    async def _prepare_startup(self, spec: LaunchSpec, pane: PaneHandle) -> None:
        if not isinstance(pane, _StartupPane):
            return
        probe_pane = pane
        deadline = self._clock.now() + timedelta(seconds=PROBE_PREPARE_TIMEOUT_SECONDS)
        prompt_observations = 0
        while self._clock.now() < deadline:
            if not await probe_pane.is_alive():
                return
            visible = await probe_pane.capture_visible()
            trust_prompt = TRUST_PROMPT_MARKER in visible and TRUST_CONFIRM_MARKER in visible
            bypass_prompt = BYPASS_PERMISSION_MARKER in visible and BYPASS_CONFIRM_MARKER in visible
            if trust_prompt or bypass_prompt:
                prompt_observations += 1
                if prompt_observations >= PROBE_PROMPT_STABILITY_COUNT:
                    await probe_pane.send_keys("Down", "Enter")
                    prompt_observations = 0
                    await self._clock.sleep(PROBE_PREPARE_POLL_SECONDS)
                    continue
            else:
                prompt_observations = 0
            if await asyncio.to_thread(hook_file_size, spec.run_dir, spec.role) > 0:
                return
            await self._clock.sleep(PROBE_PREPARE_POLL_SECONDS)
        raise TimeoutError(PROBE_STARTUP_TIMEOUT_MESSAGE)

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, object]
    ) -> AgentSession:
        """Attach to a running Claude Code pane using persisted file offsets."""
        del pane
        return restored_session(
            spec,
            state=state,
            clock=self._clock,
            reset_time=self._reset_time,
            prices=self._prices,
            api_key_auth=self._options.auth == AUTH_API_KEY,
            invalidate_quota=self._quota_probe.invalidate,
        )

    async def quota(self) -> list[QuotaWindow]:
        """Return cached subscription quota windows, or no windows for API-key auth."""
        if self._options.auth == AUTH_API_KEY:
            return []
        return await self._quota_probe.get()

    async def doctor(self) -> list[CheckResult]:
        """Check Claude's binary, version and authentication."""
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
        """Return the command that resumes a Claude conversation by id."""
        del cwd
        return f"{CLAUDE_COMMAND} --resume {session_id}"

    def headless(self) -> HeadlessAgent | None:
        """Return Claude's schema-constrained headless client."""
        return ClaudeHeadless(self._runner, self._subprocess_environment())

    def _session(self, spec: LaunchSpec, session_id: str, hook_offset: int) -> ClaudeSession:
        return ClaudeSession(
            spec.run_dir,
            spec.role,
            session_id,
            hook_offset,
            clock=self._clock,
            reset_time=self._reset_time,
            prices=self._prices,
            model=spec.rung.model,
            api_key_auth=self._options.auth == AUTH_API_KEY,
            invalidate_quota=self._quota_probe.invalidate,
        )

    def _argv(self, spec: LaunchSpec, session_id: str) -> tuple[str, ...]:
        resume = spec.resume_session_id is not None
        argv = [CLAUDE_COMMAND, RESUME_FLAG if resume else SESSION_ID_FLAG, session_id]
        argv.extend([MODEL_FLAG, spec.rung.model])
        if spec.rung.effort is not None:
            argv.extend([EFFORT_FLAG, spec.rung.effort])
        argv.extend([SETTINGS_FLAG, self._settings_json()])
        for directory in (spec.run_dir, *spec.add_dirs):
            argv.extend([ADD_DIR_FLAG, str(directory)])
        argv.extend(
            [PERMISSION_MODE_FLAG, PERMISSION_MODE_BYPASS]
            if _is_probe(spec)
            else self._options.args
        )
        return tuple(argv)

    def _settings_json(self) -> str:
        hook = {
            _HOOKS_LIST_FIELD: [
                {
                    _HOOK_TYPE_FIELD: _HOOK_TYPE_COMMAND,
                    _HOOK_COMMAND_FIELD: HOOK_COMMAND,
                }
            ]
        }
        return json.dumps(
            {_HOOKS_FIELD: {event: [hook] for event in HOOK_EVENTS}},
            separators=(",", ":"),
        )

    def _pane_environment(self, base: Mapping[str, str]) -> dict[str, str | None]:
        environment: dict[str, str | None] = dict(base)
        if self._options.auth == AUTH_API_KEY:
            environment[ANTHROPIC_API_KEY_ENV] = self._api_key()
            environment[ANTHROPIC_AUTH_TOKEN_ENV] = None
        else:
            environment[ANTHROPIC_API_KEY_ENV] = None
            environment[ANTHROPIC_AUTH_TOKEN_ENV] = None
        return environment

    def _subprocess_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self._options.auth == AUTH_API_KEY:
            environment[ANTHROPIC_API_KEY_ENV] = self._api_key()
            environment.pop(ANTHROPIC_AUTH_TOKEN_ENV, None)
        else:
            environment.pop(ANTHROPIC_API_KEY_ENV, None)
            environment.pop(ANTHROPIC_AUTH_TOKEN_ENV, None)
        return environment

    def _api_key(self) -> str:
        value = os.environ.get(self._options.api_key_env)
        if not value:
            message = f"Claude API key environment variable {self._options.api_key_env!r} is unset"
            raise ConfigError(message)
        return value

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
        logged_in = typed_payload.get(_AUTH_STATUS_LOGGED_IN_FIELD)
        authenticated = result.returncode == 0 and logged_in is True
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


def _is_probe(spec: LaunchSpec) -> bool:
    return spec.env.get(PROBE_ENV_VAR) == PROBE_ENV_VALUE
