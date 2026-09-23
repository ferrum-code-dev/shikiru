"""The agent plugin contract test kit (04 §3): `AgentContractSuite`.

A plugin proves it satisfies `agents.contracts` by subclassing `AgentContractSuite` and
implementing its case-providing methods. The base class runs:

- four **offline** checks, always, against a `RecordingPaneHandle` and a temp `run_dir`,
  never a real pane or process: hook/transcript signal mapping and usage/cost, both by
  copying the subclass's committed sample files into the launched session's role directory
  and reading them back through the real `launch()` and `session.signals()`/`usage()`;
  error mapping, sharing that same mechanism (an error sample's expected signal sequence
  just contains `TurnFailed`); and `launch()` argv and env for new, resume and each auth
  mode, against the subclass's own `LaunchSpec`s;
- six **live** checks against a real tmux pane and a scratch git repository: launch,
  a full turn, usage, resume, quota and headless `ask`. These are gated by
  `SHIKIRU_E2E=1`, except for a plugin that overrides `runs_live_checks_in_default_gate`
  to declare itself cheap enough to run on every `poe check` (the fake agent does).

Samples are placed into the role directory *after* `launch()`, not before: `launch()` takes
the launch-time size of the role's hook/transcript files as a session's read origin, exactly
so a freshly launched session never replays another session's history in the same role
directory (04 §3 offline check 4 / live check 4). Since a sample is a capture of what a real
agent's CLI *appends* while running, placing it after `launch()` is what a live session
would actually see; placing it before would make `launch()` skip straight past it.

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

from shikiru.agents.contracts import (
    AgentContext,
    PromptSubmitted,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    role_dir,
)
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT
from shikiru.clock import SystemClock
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    schema_for,
)
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.pane import TmuxPane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from pydantic import BaseModel

    from shikiru.agents.contracts import (
        AgentFactory,
        AgentSession,
        AgentSignal,
        InteractiveAgent,
        LaunchSpec,
        Usage,
    )

E2E_ENV_VAR = "SHIKIRU_E2E"

_SCRATCH_WINDOW_PREFIX = "fake-contract-"
_GIT_INIT_TIMEOUT_SECONDS = 10.0
_SIGNAL_TIMEOUT_SECONDS = 15.0
HEADLESS_ASK_TIMEOUT = timedelta(seconds=15)

_REMAIN_ON_EXIT_OPTION = "remain-on-exit"
_REMAIN_ON_EXIT_VALUE = "on"

_TURNS_DIRNAME = "turns"
_TURN_NAME = "01-turn"


class TmuxSocket(Protocol):
    """What the live checks need from a private tmux server (structurally, `tmux_server`).

    `session` is the name of the session scratch windows are created in.
    """

    socket_name: str
    session: str
    runner: SubprocessRunner


@dataclass(frozen=True)
class SampleCase:
    """One offline sample: files to place in the launched session's role dir.

    Also carries what its signals and cumulative usage must come out as (04 §3 offline
    checks 1-3).
    """

    name: str
    files: Mapping[str, Path]
    expected_signals: Sequence[AgentSignal]
    expected_usage: Usage
    session_id: str


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
        """Report dead: no process ever really runs behind this double."""
        return False

    async def send_keys(self, *keys: str) -> None:
        """Drop `keys`: no process ever reads them behind this double."""
        del keys

    def last_respawn(self) -> RespawnCall:
        """Return the most recent `respawn()` call, failing loudly if there was none."""
        if not self.respawn_calls:
            message = "no respawn() call was recorded"
            raise AssertionError(message)
        return self.respawn_calls[-1]


@dataclass(frozen=True)
class LaunchCase:
    """One offline `launch()` argv-and-env case (04 §3 offline check 4)."""

    name: str
    spec: LaunchSpec
    expected: RespawnCall


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _init_scratch_repo(runner: SubprocessRunner, path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await runner.run(["git", "init"], timeout=_GIT_INIT_TIMEOUT_SECONDS, cwd=path, check=True)


def _prepare_turn(run_dir: Path, role: str, kind: ReportKind) -> None:
    turn_dir = run_dir / _TURNS_DIRNAME / _TURN_NAME
    turn_dir.mkdir(parents=True, exist_ok=True)
    (turn_dir / REPORT_KIND_FILENAME).write_text(kind.value)
    (turn_dir / REPORT_SCHEMA_FILENAME).write_text(json.dumps(schema_for(kind)))
    (turn_dir / REPORT_FILENAME).unlink(missing_ok=True)
    role_path = role_dir(run_dir, role)
    role_path.mkdir(parents=True, exist_ok=True)
    link = role_path / CURRENT_TURN_LINK_NAME
    link.unlink(missing_ok=True)
    link.symlink_to(turn_dir)


def _place_sample_files(role_path: Path, files: Mapping[str, Path]) -> None:
    for relative_path, source_path in files.items():
        destination = role_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_path.read_bytes())


async def _make_pane(tmux_server: TmuxSocket, cwd: Path) -> TmuxPane:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    window_name = f"{_SCRATCH_WINDOW_PREFIX}{uuid.uuid4().hex[:8]}"
    pane_id = await tmux.new_window(tmux_server.session, name=window_name, cwd=cwd)
    await tmux.set_window_option(pane_id, _REMAIN_ON_EXIT_OPTION, _REMAIN_ON_EXIT_VALUE)
    return TmuxPane(tmux, pane_id, SystemClock())


async def _next_any(
    signals: AsyncIterator[AgentSignal], *, timeout: float = _SIGNAL_TIMEOUT_SECONDS
) -> AgentSignal:
    return await asyncio.wait_for(signals.__anext__(), timeout=timeout)


async def _next_signal[S](
    signals: AsyncIterator[AgentSignal],
    kind: type[S],
    *,
    timeout: float = _SIGNAL_TIMEOUT_SECONDS,
) -> S:
    while True:
        signal = await asyncio.wait_for(signals.__anext__(), timeout=timeout)
        if isinstance(signal, kind):
            return signal


class AgentContractSuite:
    """Pytest base class for the agent plugin contract (04 §3). See module docstring."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False

    def agent_factory(self) -> AgentFactory:
        """Return the plugin's entry-point target, e.g. `fake_agent.agent.create`."""
        raise NotImplementedError

    def agent_options(self) -> Mapping[str, Any]:
        """Return the `agents.<name>.options` the factory validates and builds from."""
        return {}

    def build_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        """Build the `LaunchSpec` the offline and live checks launch or resume with."""
        raise NotImplementedError

    def headless_ask_schema_model(self) -> type[BaseModel] | None:
        """Return the pydantic model `headless().ask()`'s result must validate against."""
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

    def hook_signal_cases(self) -> Sequence[SampleCase]:
        """Return one `SampleCase` per sample hook/transcript file, asserted on its signals."""
        raise NotImplementedError

    def usage_cases(self) -> Sequence[SampleCase]:
        """Return one `SampleCase` per sample, asserted on its cumulative `Usage`."""
        raise NotImplementedError

    def error_cases(self) -> Sequence[SampleCase]:
        """Return one `SampleCase` per sample error entry, asserted on its `TurnFailed`s."""
        raise NotImplementedError

    def launch_cases(self, tmp_path: Path) -> Sequence[LaunchCase]:
        """Return one `LaunchCase` per `launch()` variant (new, resume, each auth mode)."""
        raise NotImplementedError

    def launch_case_options(self) -> Mapping[str, Any]:
        """Return the options `test_offline_launch_argv_and_env` builds its agent from.

        Defaults to `agent_options()`. A plugin that mints a random id for a brand-new
        session (as it should, for real use) overrides this to pin that id instead, so the
        offline check can assert an exact `launch()` argv/env; `agent_options()` itself stays
        realistic, so the live resume check still exercises genuine `resume_session_id` reuse.
        """
        return self.agent_options()

    def _build_agent(self, options: Mapping[str, Any] | None = None) -> InteractiveAgent:
        context = AgentContext(
            options=self.agent_options() if options is None else options,
            runner=SubprocessRunner(),
            clock=SystemClock(),
        )
        return self.agent_factory()(context)

    def _require_live(self) -> None:
        if not self.runs_live_checks_in_default_gate and os.environ.get(E2E_ENV_VAR) != "1":
            message = f"live checks need {E2E_ENV_VAR}=1"
            pytest.skip(message)

    async def _run_sample_case(
        self, case: SampleCase, tmp_path: Path
    ) -> tuple[list[AgentSignal], Usage]:
        agent = self._build_agent()
        spec = self.build_spec(tmp_path, resume_session_id=case.session_id)
        pane = RecordingPaneHandle()
        session = await agent.launch(spec, pane)
        await asyncio.to_thread(_place_sample_files, role_dir(spec.run_dir, spec.role), case.files)
        signals = session.signals()
        collected: list[AgentSignal] = []
        while len(collected) < len(case.expected_signals):
            collected.append(await _next_any(signals))
        usage = await session.usage()
        return collected, usage

    async def test_offline_hook_and_transcript_signals(self, tmp_path: Path) -> None:
        for case in self.hook_signal_cases():
            collected, _usage = await self._run_sample_case(case, tmp_path / case.name)
            _expect(collected == list(case.expected_signals), case.name)

    async def test_offline_usage_and_cost(self, tmp_path: Path) -> None:
        for case in self.usage_cases():
            _signals, usage = await self._run_sample_case(case, tmp_path / case.name)
            _expect(usage == case.expected_usage, case.name)

    async def test_offline_error_mapping(self, tmp_path: Path) -> None:
        for case in self.error_cases():
            collected, _usage = await self._run_sample_case(case, tmp_path / case.name)
            _expect(collected == list(case.expected_signals), case.name)

    async def test_offline_launch_argv_and_env(self, tmp_path: Path) -> None:
        options = self.launch_case_options()
        for case in self.launch_cases(tmp_path):
            agent = self._build_agent(options)
            pane = RecordingPaneHandle()
            await agent.launch(case.spec, pane)
            _expect(pane.last_respawn() == case.expected, case.name)

    async def _launch_live(
        self, tmux_server: TmuxSocket, tmp_path: Path, *, prepare_turn: bool
    ) -> tuple[InteractiveAgent, LaunchSpec, TmuxPane, AgentSession]:
        self._require_live()
        agent = self._build_agent()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(SubprocessRunner(), spec.cwd)
        if prepare_turn:
            _prepare_turn(spec.run_dir, spec.role, self.live_report_kind())
        pane = await _make_pane(tmux_server, spec.cwd)
        session = await agent.launch(spec, pane)
        return agent, spec, pane, session

    async def test_live_launch_starts_session(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        _agent, _spec, _pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=False
        )
        await _next_signal(session.signals(), SessionStarted)

    async def test_live_turn_completes(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        _agent, _spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=True
        )
        signals = session.signals()
        await _next_signal(signals, SessionStarted)
        await pane.paste_line("do the thing")
        await _next_signal(signals, PromptSubmitted)
        await _next_signal(signals, TurnEnded)

    async def test_live_usage_reports_tokens(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        _agent, _spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=True
        )
        signals = session.signals()
        await _next_signal(signals, SessionStarted)
        await pane.paste_line("do the thing")
        await _next_signal(signals, TurnEnded)
        usage = await session.usage()
        _expect(usage.input_tokens + usage.output_tokens > 0, "usage() reported no tokens")

    async def test_live_resume_recalls_context(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        agent, _spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=True
        )
        signals = session.signals()
        started = await _next_signal(signals, SessionStarted)
        if started.session_id is None:
            pytest.skip("this agent's sessions can't be resumed (session_id is None)")
        nonce = uuid.uuid4().hex
        await pane.paste_line(RESUME_NONCE_PROMPT.format(nonce=nonce))
        await _next_signal(signals, TurnEnded)
        await pane.paste_line(agent.keys.exit_input)
        await _next_signal(signals, SessionEnded)
        resume_spec = self.build_spec(tmp_path, resume_session_id=started.session_id)
        resumed = await agent.launch(resume_spec, pane)
        resumed_signals = resumed.signals()
        await _next_signal(resumed_signals, SessionStarted)
        await pane.paste_line(RESUME_RECALL_PROMPT)
        ended = await _next_signal(resumed_signals, TurnEnded)
        _expect(nonce in ended.last_message, "resumed turn did not recall the nonce")

    async def test_live_attach_does_not_replay_signals(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        agent, spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=True
        )
        signals = session.signals()
        await _next_signal(signals, SessionStarted)
        await pane.paste_line("first turn")
        await _next_signal(signals, TurnEnded)
        snapshot = session.snapshot()
        resumed = await agent.attach(spec, pane, snapshot)
        resumed_signals = resumed.signals()
        await pane.paste_line("second turn")
        submitted = await _next_signal(resumed_signals, PromptSubmitted)
        _expect(submitted.text == "second turn", "attach() replayed an old prompt")
        await _next_signal(resumed_signals, TurnEnded)

    async def test_live_quota_reports_without_error(self) -> None:
        self._require_live()
        agent = self._build_agent()
        await agent.quota()

    async def test_live_headless_ask_matches_schema(self, tmp_path: Path) -> None:
        self._require_live()
        agent = self._build_agent()
        headless = agent.headless()
        if headless is None:
            pytest.skip("this agent has no headless support")
        schema_model = self.headless_ask_schema_model()
        if schema_model is None:
            message = "headless_ask_schema_model() must be set when headless() is provided"
            raise AssertionError(message)
        prompt = self.headless_ask_prompt()
        schema = schema_model.model_json_schema()
        result = await headless.ask(
            prompt=prompt,
            schema=schema,
            model=self.headless_ask_model(),
            effort=None,
            cwd=tmp_path,
            timeout=HEADLESS_ASK_TIMEOUT,
        )
        schema_model.model_validate(result)
