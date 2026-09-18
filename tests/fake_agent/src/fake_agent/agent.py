"""The fake `InteractiveAgent`: respawns a pane's process into the scripted REPL (04 §1, §3)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fake_agent.headless import FakeHeadlessAgent
from fake_agent.session import FakeSession
from shikiru.agents.contracts import CheckResult, SessionKeys
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shikiru.agents.contracts import (
        AgentContext,
        AgentSession,
        HeadlessAgent,
        LaunchSpec,
        PaneHandle,
        QuotaWindow,
    )
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

REPL_COMMAND = "fake-agent-repl"
RUN_DIR_ENV_VAR = "SHIKIRU_RUN_DIR"
ROLE_ENV_VAR = "SHIKIRU_ROLE"
SESSION_ID_ENV_VAR = "SHIKIRU_FAKE_SESSION_ID"

_INTERRUPT_KEYS = ("C-c",)
_EXIT_INPUT = "/exit"
_SAFE_KEYS = ("Enter",)

PRICES_PATH = Path(__file__).parent / "prices.toml"
_DOCTOR_CHECK_NAME = "binary"


class FakeOptions(BaseModel):
    """`agents.fake.options`: canned answers for `headless().ask()` (04 §3, 07 §3)."""

    model_config = ConfigDict(extra="forbid")

    asks: list[dict[str, Any]] = Field(default_factory=list)


class FakeAgent:
    """A scripted `InteractiveAgent`, used only by tests, driven by `SHIKIRU_FAKE_SCRIPT`."""

    name = "fake"
    keys = SessionKeys(interrupt=_INTERRUPT_KEYS, exit_input=_EXIT_INPUT, safe=_SAFE_KEYS)

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: FakeOptions) -> None:
        """Hold `options` for `headless()`, pricing usage under this plugin's `prices.toml`."""
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._prices = PriceTable.from_toml(PRICES_PATH)

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Respawn `pane` into a fresh REPL under a newly generated session id."""
        session_id = str(uuid.uuid4())
        await self._spawn(spec, pane, session_id)
        return self._session(spec, session_id)

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, Any]
    ) -> AgentSession:
        """Respawn `pane` into the REPL again, under `state`'s recorded session id."""
        session_id = state["session_id"]
        await self._spawn(spec, pane, session_id)
        return self._session(spec, session_id)

    async def quota(self) -> list[QuotaWindow]:
        """Return no quota windows: the fake agent has no quota source."""
        return []

    async def doctor(self) -> list[CheckResult]:
        """Report the REPL command as always present: nothing external to check."""
        return [CheckResult(name=_DOCTOR_CHECK_NAME, ok=True, detail=REPL_COMMAND)]

    def resume_command(self, session_id: str, cwd: Path) -> str | None:
        """Return the shell command that relaunches the REPL under `session_id`."""
        del cwd
        return f"{SESSION_ID_ENV_VAR}={session_id} {REPL_COMMAND}"

    def headless(self) -> HeadlessAgent | None:
        """Return a `FakeHeadlessAgent` serving this agent's configured `asks`."""
        return FakeHeadlessAgent(self._options.asks)

    async def _spawn(self, spec: LaunchSpec, pane: PaneHandle, session_id: str) -> None:
        env: dict[str, str | None] = dict(spec.env)
        env[RUN_DIR_ENV_VAR] = str(spec.run_dir)
        env[ROLE_ENV_VAR] = spec.role
        env[SESSION_ID_ENV_VAR] = session_id
        await pane.respawn([REPL_COMMAND], env=env, cwd=spec.cwd)

    def _session(self, spec: LaunchSpec, session_id: str) -> FakeSession:
        return FakeSession(
            spec.run_dir,
            spec.role,
            session_id,
            clock=self._clock,
            reset_time=self._reset_time,
            prices=self._prices,
            model=spec.rung.model,
        )


def create(context: AgentContext) -> FakeAgent:
    """Build the `fake` agent from `context.options`, raising `ConfigError` on bad options."""
    try:
        options = FakeOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the fake agent: {error}"
        raise ConfigError(message) from error
    return FakeAgent(context.runner, context.clock, options)
