"""The agent plugin contract test kit (04 §3): `AgentContractSuite`.

A plugin proves it satisfies `agents.contracts` by subclassing `AgentContractSuite` and
implementing its case-providing methods. The base class runs:

- four **offline** checks, always: hook/transcript signal mapping, usage and cost
  computation, error mapping, and `launch`/`attach` argv and env, all against data and
  doubles the subclass supplies, never a real pane or process;
- six **live** checks against a real tmux pane and a scratch git repository: launch,
  a full turn, usage, resume, quota and headless `ask`. These are gated by
  `SHIKIRU_E2E=1`, except for a plugin that overrides `runs_live_checks_in_default_gate`
  to declare itself cheap enough to run on every `poe check` (the fake agent does).

Checks 04 §3 marks "when provided" or "when session_id is set" call `pytest.skip` with a
reason instead of silently passing when they don't apply.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Protocol

import pytest

from shikiru.agents.contracts import PromptSubmitted, SessionStarted, TurnEnded
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT
from shikiru.clock import SystemClock
from shikiru.domain.reports import ReportKind, schema_for
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.pane import TmuxPane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from shikiru.agents.contracts import (
        AgentSignal,
        InteractiveAgent,
        LaunchSpec,
    )

E2E_ENV_VAR = "SHIKIRU_E2E"

_SCRATCH_WINDOW_PREFIX = "fake-contract-"
_GIT_INIT_TIMEOUT_SECONDS = 10.0
_SIGNAL_TIMEOUT_SECONDS = 15.0
HEADLESS_ASK_TIMEOUT = timedelta(seconds=15)

_TURNS_DIRNAME = "turns"
_TURN_NAME = "01-turn"
_CURRENT_TURN_LINK_NAME = "current_turn"
_REPORT_KIND_FILENAME = "report.kind"
_REPORT_SCHEMA_FILENAME = "report.schema.json"
_REPORT_FILENAME = "report.json"


class TmuxSocket(Protocol):
    """What the live checks need from a private tmux server (structurally, `tmux_server`)."""

    socket_name: str
    runner: SubprocessRunner


@dataclass(frozen=True)
class Case:
    """One offline assertion: `actual`, precomputed by the subclass, must equal `expected`."""

    name: str
    actual: object
    expected: object


@dataclass(frozen=True, slots=True)
class RespawnCall:
    """One `PaneHandle.respawn()` call `RecordingPaneHandle` captured."""

    argv: tuple[str, ...]
    env: Mapping[str, str | None]
    cwd: Path


@dataclass
class RecordingPaneHandle:
    """A `PaneHandle` double that records `respawn()` calls instead of touching a real pane."""

    respawn_calls: list[RespawnCall] = field(default_factory=list)

    async def respawn(self, argv: Sequence[str], env: Mapping[str, str | None], cwd: Path) -> None:
        """Record this call instead of restarting a real process."""
        self.respawn_calls.append(RespawnCall(tuple(argv), dict(env), cwd))

    async def capture_visible(self) -> str:
        """Return an empty pane: nothing is ever rendered by this double."""
        return ""

    async def is_alive(self) -> bool:
        """Report alive: no process ever really runs behind this double."""
        return True

    def last_respawn(self) -> RespawnCall:
        """Return the most recent `respawn()` call, failing loudly if there was none."""
        if not self.respawn_calls:
            message = "no respawn() call was recorded"
            raise AssertionError(message)
        return self.respawn_calls[-1]


@dataclass(frozen=True)
class LaunchCase:
    """One offline `launch`/`attach` argv-and-env case (04 §3 offline check 4)."""

    name: str
    agent: InteractiveAgent
    spec: LaunchSpec
    check: Callable[[RespawnCall], None]
    resume_state: Mapping[str, Any] | None = None


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _init_scratch_repo(runner: SubprocessRunner, path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await runner.run(["git", "init"], timeout=_GIT_INIT_TIMEOUT_SECONDS, cwd=path, check=True)


def _prepare_turn(run_dir: Path, role: str, kind: ReportKind) -> None:
    turn_dir = run_dir / _TURNS_DIRNAME / _TURN_NAME
    turn_dir.mkdir(parents=True, exist_ok=True)
    (turn_dir / _REPORT_KIND_FILENAME).write_text(kind.value)
    (turn_dir / _REPORT_SCHEMA_FILENAME).write_text(json.dumps(schema_for(kind)))
    (turn_dir / _REPORT_FILENAME).unlink(missing_ok=True)
    role_dir = run_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    link = role_dir / _CURRENT_TURN_LINK_NAME
    link.unlink(missing_ok=True)
    link.symlink_to(turn_dir)


async def _make_pane(tmux_server: TmuxSocket, cwd: Path) -> TmuxPane:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    window_name = f"{_SCRATCH_WINDOW_PREFIX}{uuid.uuid4().hex[:8]}"
    pane_id = await tmux.new_window("test", name=window_name, cwd=cwd)
    return TmuxPane(tmux, pane_id, SystemClock())


async def _next_signal(
    signals: AsyncIterator[AgentSignal],
    kind: type[AgentSignal],
    *,
    timeout: float = _SIGNAL_TIMEOUT_SECONDS,
) -> AgentSignal:
    while True:
        signal = await asyncio.wait_for(signals.__anext__(), timeout=timeout)
        if isinstance(signal, kind):
            return signal


def _check_schema_shape(schema: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    for required_field in schema.get("required", []):
        _expect(required_field in result, f"{required_field!r} missing from headless() result")


class AgentContractSuite:
    """Pytest base class for the agent plugin contract (04 §3). See module docstring."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False

    def build_agent(self) -> InteractiveAgent:
        """Build the `InteractiveAgent` under test, configured for the live checks."""
        raise NotImplementedError

    def build_live_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        """Build the `LaunchSpec` the live checks launch or resume with."""
        raise NotImplementedError

    def headless_schema(self) -> Mapping[str, Any] | None:
        """Return the schema `headless().ask()` should honour, or `None` if unsupported."""
        return None

    def headless_ask_model(self) -> str:
        """Return the model name to pass to `headless().ask()` in the live check."""
        return "test-model"

    def headless_ask_prompt(self) -> str:
        """Return the prompt to pass to `headless().ask()` in the live check."""
        return "ping"

    def live_report_kind(self) -> ReportKind:
        """Return the report kind the live turn/usage/resume checks exercise."""
        return ReportKind.IMPLEMENTATION

    def hook_signal_cases(self) -> Sequence[Case]:
        """Return one `Case` per sample hook/transcript file mapped to its expected signals."""
        raise NotImplementedError

    def usage_cases(self) -> Sequence[Case]:
        """Return one `Case` per sample mapped to its expected cumulative `Usage`."""
        raise NotImplementedError

    def error_cases(self) -> Sequence[Case]:
        """Return one `Case` per sample error entry mapped to its expected kind/`resets_at`."""
        raise NotImplementedError

    def launch_cases(self, tmp_path: Path) -> Sequence[LaunchCase]:
        """Return one `LaunchCase` per `launch`/`attach` variant (new, resume, each auth mode)."""
        raise NotImplementedError

    def _require_live(self) -> None:
        if not self.runs_live_checks_in_default_gate and os.environ.get(E2E_ENV_VAR) != "1":
            message = f"live checks need {E2E_ENV_VAR}=1"
            pytest.skip(message)

    def test_offline_hook_and_transcript_signals(self) -> None:
        for case in self.hook_signal_cases():
            _expect(case.actual == case.expected, case.name)

    def test_offline_usage_and_cost(self) -> None:
        for case in self.usage_cases():
            _expect(case.actual == case.expected, case.name)

    def test_offline_error_mapping(self) -> None:
        for case in self.error_cases():
            _expect(case.actual == case.expected, case.name)

    async def test_offline_launch_argv_and_env(self, tmp_path: Path) -> None:
        for case in self.launch_cases(tmp_path):
            pane = RecordingPaneHandle()
            if case.resume_state is None:
                await case.agent.launch(case.spec, pane)
            else:
                await case.agent.attach(case.spec, pane, case.resume_state)
            case.check(pane.last_respawn())

    async def test_live_launch_starts_session(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        self._require_live()
        agent = self.build_agent()
        spec = self.build_live_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(SubprocessRunner(), spec.cwd)
        pane = await _make_pane(tmux_server, spec.cwd)
        session = await agent.launch(spec, pane)
        signal = await _next_signal(session.signals(), SessionStarted)
        _expect(isinstance(signal, SessionStarted), "launch() did not yield SessionStarted")

    async def test_live_turn_completes(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        self._require_live()
        agent = self.build_agent()
        spec = self.build_live_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(SubprocessRunner(), spec.cwd)
        _prepare_turn(spec.run_dir, spec.role, self.live_report_kind())
        pane = await _make_pane(tmux_server, spec.cwd)
        session = await agent.launch(spec, pane)
        signals = session.signals()
        await _next_signal(signals, SessionStarted)
        await pane.paste_line("do the thing")
        await _next_signal(signals, PromptSubmitted)
        await _next_signal(signals, TurnEnded)

    async def test_live_usage_reports_tokens(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        self._require_live()
        agent = self.build_agent()
        spec = self.build_live_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(SubprocessRunner(), spec.cwd)
        _prepare_turn(spec.run_dir, spec.role, self.live_report_kind())
        pane = await _make_pane(tmux_server, spec.cwd)
        session = await agent.launch(spec, pane)
        signals = session.signals()
        await _next_signal(signals, SessionStarted)
        await pane.paste_line("do the thing")
        await _next_signal(signals, TurnEnded)
        usage = await session.usage()
        _expect(usage.input_tokens + usage.output_tokens > 0, "usage() reported no tokens")

    async def test_live_resume_recalls_context(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        self._require_live()
        agent = self.build_agent()
        spec = self.build_live_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(SubprocessRunner(), spec.cwd)
        _prepare_turn(spec.run_dir, spec.role, self.live_report_kind())
        pane = await _make_pane(tmux_server, spec.cwd)
        session = await agent.launch(spec, pane)
        signals = session.signals()
        started = await _next_signal(signals, SessionStarted)
        if not isinstance(started, SessionStarted):
            message = "launch() did not yield SessionStarted"
            raise TypeError(message)
        if started.session_id is None:
            pytest.skip("this agent's sessions can't be resumed (session_id is None)")
        nonce = uuid.uuid4().hex
        await pane.paste_line(RESUME_NONCE_PROMPT.format(nonce=nonce))
        await _next_signal(signals, TurnEnded)
        snapshot = session.snapshot()
        resume_spec = self.build_live_spec(tmp_path, resume_session_id=started.session_id)
        resumed = await agent.attach(resume_spec, pane, snapshot)
        resumed_signals = resumed.signals()
        await _next_signal(resumed_signals, SessionStarted)
        await pane.paste_line(RESUME_RECALL_PROMPT)
        ended = await _next_signal(resumed_signals, TurnEnded)
        if not isinstance(ended, TurnEnded):
            message = "attach() did not yield TurnEnded"
            raise TypeError(message)
        _expect(nonce in ended.last_message, "resumed turn did not recall the nonce")

    async def test_live_quota_reports_without_error(self) -> None:
        self._require_live()
        agent = self.build_agent()
        await agent.quota()

    async def test_live_headless_ask_matches_schema(self, tmp_path: Path) -> None:
        self._require_live()
        agent = self.build_agent()
        headless = agent.headless()
        if headless is None:
            pytest.skip("this agent has no headless support")
        schema = self.headless_schema()
        if schema is None:
            message = "headless_schema() must be set when headless() is provided"
            raise AssertionError(message)
        prompt = self.headless_ask_prompt()
        result = await headless.ask(
            prompt=prompt,
            schema=schema,
            model=self.headless_ask_model(),
            effort=None,
            cwd=tmp_path,
            timeout=HEADLESS_ASK_TIMEOUT,
        )
        _check_schema_shape(schema, result)
