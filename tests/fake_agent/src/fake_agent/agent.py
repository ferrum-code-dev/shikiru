"""The fake `InteractiveAgent`: respawns a pane's process into the scripted REPL (04 §1, §3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fake_agent.headless import FakeHeadlessAgent
from fake_agent.session import FakeSession
from shikiru.agents.contracts import CheckResult, QuotaWindow, SessionKeys, role_dir
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from shikiru.agents.contracts import (
        AgentContext,
        AgentSession,
        HeadlessAgent,
        LaunchSpec,
        PaneHandle,
    )
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

REPL_COMMAND = "fake-agent-repl"
SESSION_ID_ENV_VAR = "SHIKIRU_FAKE_SESSION_ID"

EXIT_INPUT = "/exit"
INTERRUPT_KEYS = ("C-c",)
SAFE_KEYS = ("Enter",)

_DOCTOR_CHECK_NAME = "binary"


class FakeQuotaWindow(BaseModel):
    """One quota snapshot returned by the configurable fake agent."""

    name: str
    used_pct: float
    resets_at: datetime | None = None
    observed_at: datetime


class FakeOptions(BaseModel):
    """`agents.fake.options`: scripted asks, sessions and quota snapshots."""

    model_config = ConfigDict(extra="forbid")

    asks: list[dict[str, Any]] = Field(default_factory=list)
    new_session_id: str | None = None
    quota: list[FakeQuotaWindow] = Field(default_factory=list)
    quota_sequence: list[list[FakeQuotaWindow]] = Field(default_factory=list)
    quota_failure: str | None = None


def _hooks_file_size(run_dir: Path, role: str) -> int:
    path = role_dir(run_dir, role) / HOOKS_FILENAME
    return path.stat().st_size if path.is_file() else 0


class FakeAgent:
    """A scripted `InteractiveAgent`, used only by tests, driven by `SHIKIRU_FAKE_SCRIPT`."""

    name = "fake"
    keys = SessionKeys(interrupt=INTERRUPT_KEYS, exit_input=EXIT_INPUT, safe=SAFE_KEYS)

    def __init__(self, runner: SubprocessRunner, clock: Clock, options: FakeOptions) -> None:
        """Hold `options` for `headless()`."""
        self._runner = runner
        self._clock = clock
        self._options = options
        self._reset_time = ResetTimeParser(clock)
        self._quota_index = 0

    async def launch(self, spec: LaunchSpec, pane: PaneHandle) -> AgentSession:
        """Respawn `pane` into a fresh REPL, reusing `spec.resume_session_id` when set."""
        session_id = spec.resume_session_id or self._options.new_session_id or str(uuid.uuid4())
        start_offset = await asyncio.to_thread(_hooks_file_size, spec.run_dir, spec.role)
        await self._spawn(spec, pane, session_id)
        return self._session(spec, session_id, start_offset)

    async def attach(
        self, spec: LaunchSpec, pane: PaneHandle, state: Mapping[str, Any]
    ) -> AgentSession:
        """Reconnect to the still-running REPL behind `pane`, from `state`'s recorded offset."""
        del pane
        return self._session(spec, state["session_id"], state["offset"])

    async def quota(self) -> list[QuotaWindow]:
        """Return the quota windows configured for this fake agent."""
        if self._options.quota_failure is not None:
            raise RuntimeError(self._options.quota_failure)
        sequence = self._options.quota_sequence
        if sequence:
            index = min(self._quota_index, len(sequence) - 1)
            windows = sequence[index]
            self._quota_index += 1
        else:
            windows = self._options.quota
        return [QuotaWindow(**window.model_dump()) for window in windows]

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
        env[SESSION_ID_ENV_VAR] = session_id
        await pane.respawn([REPL_COMMAND], env=env, cwd=spec.cwd)

    def _session(self, spec: LaunchSpec, session_id: str, start_offset: int) -> FakeSession:
        return FakeSession(
            spec.run_dir,
            spec.role,
            session_id,
            start_offset,
            clock=self._clock,
            reset_time=self._reset_time,
        )


def create(context: AgentContext) -> FakeAgent:
    """Build the `fake` agent from `context.options`, raising `ConfigError` on bad options."""
    try:
        options = FakeOptions.model_validate(context.options)
    except ValidationError as error:
        message = f"invalid options for the fake agent: {error}"
        raise ConfigError(message) from error
    return FakeAgent(context.runner, context.clock, options)
