"""Headless agent contract checks (04 §3), imported only by tests (02 §2)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from shikiru.agents.contracts import (
    AgentContext,
    AgentSignal,
    Compacted,
    FailureKind,
    Message,
    Rung,
    SessionStarted,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.turn import (
    StreamingTurn,
    TurnParser,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
)
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT
from shikiru.clock import SystemClock
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    model_for,
    schema_for,
)
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from pydantic import BaseModel

    from shikiru.agents.contracts import Agent, AgentFactory

E2E_ENV_VAR = "SHIKIRU_E2E"
_GIT_INIT_TIMEOUT_SECONDS = 10.0
_TURN_TIMEOUT_SECONDS = 60.0
_LIVE_REPORT_PROMPT = "Submit a completed implementation report using shikiru report."
_REPORT_PROMPT_FILENAME = "contract-prompt.md"
_SYNTHETIC_SESSION_ID = "synthetic-session"


def _empty_session_logs() -> Mapping[str, Path]:
    return {}


@dataclass(frozen=True)
class RecordedStream:
    """One committed stdout/stderr capture and its normalized expected signals."""

    name: str
    stdout: Path
    stderr: Path
    exit_code: int
    expected_signals: Sequence[AgentSignal]
    session_id: str | None
    session_logs: Mapping[str, Path] = field(default_factory=_empty_session_logs)


@dataclass(frozen=True)
class InvocationCase:
    """One offline command and environment sequence for an agent turn."""

    name: str
    options: Mapping[str, Any]
    spec: TurnSpec
    expected_argv: Sequence[tuple[str, ...]]
    expected_env: Sequence[Mapping[str, str]]
    outcome_streams: Sequence[RecordedStream] = ()
    expected_final_signals: Sequence[AgentSignal] = ()
    interactive_session_id: str | None = None
    expected_interactive_command: tuple[str, ...] | None = None


class AgentContractSuite:
    """Pytest base class for the process-per-turn agent contract in 04 §3."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False

    def agent_factory(self) -> AgentFactory:
        """Return the plugin's `AgentFactory` entry point."""
        raise NotImplementedError

    def agent_options(self) -> Mapping[str, Any]:
        """Return the `agents.<name>.options` used by the live checks."""
        return {}

    def build_spec(
        self,
        tmp_path: Path,
        *,
        resume_session_id: str | None,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> TurnSpec:
        """Build a new or resumed `TurnSpec` for the plugin under test."""
        raise NotImplementedError

    def parser_for(self, sample: RecordedStream) -> TurnParser:
        """Build a fresh parser for a recorded stream and its session-log excerpts."""
        raise NotImplementedError

    def recorded_streams(self) -> Sequence[RecordedStream]:
        """Return committed stream captures and expected normalized signals."""
        raise NotImplementedError

    def error_streams(self) -> Sequence[RecordedStream]:
        """Return recorded structured and unrecognized errors, including an `UNKNOWN` case."""
        raise NotImplementedError

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        """Return new, resume, model-change, auth and compaction argv/env cases."""
        raise NotImplementedError

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> TurnProcessSequence:
        """Return the same process sequence used by `agent.start_turn(spec)`."""
        raise NotImplementedError

    def headless_ask_schema_model(self) -> type[BaseModel] | None:
        """Return the result model for `headless().ask()`, when the plugin provides it."""
        return None

    def headless_ask_model(self) -> str:
        """Return the model name passed to the live `headless().ask()` check."""
        return "test-model"

    def headless_ask_prompt(self) -> str:
        """Return the prompt passed to the live `headless().ask()` check."""
        return "ping"

    def test_offline_replays_recorded_streams_and_obligations(self) -> None:
        for sample in self.recorded_streams():
            signals = _replay(self.parser_for(sample), sample)
            _expect(signals == list(sample.expected_signals), sample.name)
            assert_agent_obligations(signals, sample.session_id)

    def test_offline_maps_errors_and_preserves_unknown_failures(self) -> None:
        samples = self.error_streams()
        _expect(bool(samples), "no error samples were provided")
        unknown_found = False
        for sample in samples:
            signals = _replay(self.parser_for(sample), sample)
            _expect(signals == list(sample.expected_signals), sample.name)
            assert_agent_obligations(signals, sample.session_id)
            failure = next(signal for signal in signals if isinstance(signal, TurnFailed))
            unknown_found |= failure.kind is FailureKind.UNKNOWN
        _expect(unknown_found, "unmapped error text did not produce UNKNOWN")

    @pytest.mark.asyncio
    async def test_offline_compaction_replays_and_retries_once(self, tmp_path: Path) -> None:
        samples = self.recorded_streams()
        _expect(
            any(
                isinstance(signal, Compacted)
                for sample in samples
                for signal in sample.expected_signals
            ),
            "recorded streams contain no Compacted signal",
        )
        success = await _run_synthetic_compaction(tmp_path, second_failure=False)
        _expect(
            sum(isinstance(signal, SessionStarted) for signal in success) == 1,
            "session start count",
        )
        _expect(sum(isinstance(signal, Compacted) for signal in success) == 1, "compaction count")
        _expect(sum(isinstance(signal, TurnEnded) for signal in success) == 1, "turn end count")
        _expect(
            not any(isinstance(signal, TurnFailed) for signal in success),
            "successful compact-and-retry failed",
        )
        failure = await _run_synthetic_compaction(tmp_path / "failure", second_failure=True)
        terminal = [signal for signal in failure if isinstance(signal, (TurnEnded, TurnFailed))]
        _expect(
            terminal == [TurnFailed(FailureKind.CONTEXT, "second context failure", None)],
            "a second context failure did not end the turn",
        )

    def test_offline_builds_expected_argv_environment_and_interactive_commands(
        self, tmp_path: Path
    ) -> None:
        for case in self.invocation_cases(tmp_path):
            agent = self.agent_factory()(
                AgentContext(
                    options=case.options,
                    runner=SubprocessRunner(),
                    clock=SystemClock(),
                )
            )
            sequence = self.process_sequence(agent, case.spec)
            command = sequence.start(case.spec)
            commands = [command]
            outcomes = _process_outcomes(self.parser_for, case.outcome_streams)
            for outcome in outcomes:
                command = sequence.advance(case.spec, outcome)
                if command is None:
                    break
                commands.append(command)
            _expect([item.argv for item in commands] == list(case.expected_argv), case.name)
            _expect(len(commands) == len(case.expected_env), case.name)
            for command, expected in zip(commands, case.expected_env, strict=True):
                _expect(
                    all(command.env.get(key) == value for key, value in expected.items()), case.name
                )
            if case.expected_final_signals:
                _expect(bool(outcomes), case.name)
                final_outcome = outcomes[-1]
                final_terminals = [
                    signal
                    for signal in final_outcome.signals
                    if isinstance(signal, (TurnEnded, TurnFailed))
                ]
                expected_terminals = [
                    signal
                    for signal in case.expected_final_signals
                    if isinstance(signal, (TurnEnded, TurnFailed))
                ]
                _expect(final_terminals == expected_terminals, case.name)
                assert_agent_obligations(case.expected_final_signals, case.spec.session_id)
            if case.interactive_session_id is not None:
                _expect(case.expected_interactive_command is not None, case.name)
                _expect(
                    tuple(agent.interactive_command(case.interactive_session_id, case.spec.cwd))
                    == case.expected_interactive_command,
                    case.name,
                )

    @pytest.mark.asyncio
    async def test_live_first_turn_writes_a_valid_report(self, tmp_path: Path) -> None:
        self._require_live()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(spec.cwd)
        _prepare_report_turn(spec)
        await asyncio.to_thread(
            (spec.cwd / _REPORT_PROMPT_FILENAME).write_text,
            _LIVE_REPORT_PROMPT,
        )
        spec = _with_prompt(spec, str(spec.cwd / _REPORT_PROMPT_FILENAME))
        signals = await self._run_turn(spec)
        _expect(isinstance(signals[0], SessionStarted), "live turn omitted SessionStarted")
        _expect(isinstance(signals[-1], TurnEnded), "live turn omitted TurnEnded")
        _expect((spec.turn_dir / REPORT_FILENAME).is_file(), "live turn wrote no report")
        await asyncio.to_thread(_validate_written_report, spec.turn_dir)

    @pytest.mark.asyncio
    async def test_live_usage_reports_session_tokens(self, tmp_path: Path) -> None:
        self._require_live()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(spec.cwd)
        signals = await self._run_turn(spec)
        usage = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
        _expect(bool(usage), "live turn reported no usage")
        _expect(usage[-1].input_tokens + usage[-1].output_tokens > 0, "live token usage was zero")

    @pytest.mark.asyncio
    async def test_live_model_change_resumes_and_recalls_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        first = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(first.cwd)
        nonce = uuid.uuid4().hex
        first_signals = await self._run_turn(
            _with_prompt(first, RESUME_NONCE_PROMPT.format(nonce=nonce))
        )
        session_id, usage = _latest_usage(first_signals)
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=session_id,
            usage=usage,
            model=f"{first.rung.model}-changed",
        )
        signals = await self._run_turn(_with_prompt(resumed, RESUME_RECALL_PROMPT))
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        _expect(nonce in message, "resumed turn did not recall the nonce")

    @pytest.mark.asyncio
    async def test_live_stop_leaves_session_resumable(self, tmp_path: Path) -> None:
        self._require_live()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(spec.cwd)
        agent = self._build_agent()
        turn = await agent.start_turn(_with_prompt(spec, "wait for the next instruction"))
        signals = turn.signals()
        started = await asyncio.wait_for(signals.__anext__(), _TURN_TIMEOUT_SECONDS)
        if not isinstance(started, SessionStarted) or started.session_id is None:
            message = "live turn did not report a resumable session id"
            raise AssertionError(message)
        await turn.stop()
        resumed = self.build_spec(tmp_path, resume_session_id=started.session_id)
        resumed_signals = await self._run_turn(resumed)
        _expect(isinstance(resumed_signals[-1], TurnEnded), "resumed turn did not end")

    @pytest.mark.asyncio
    async def test_live_headless_ask_returns_schema_valid_json(self, tmp_path: Path) -> None:
        self._require_live()
        schema_model = self.headless_ask_schema_model()
        if schema_model is None:
            pytest.skip("plugin does not provide HeadlessAgent")
        spec = self.build_spec(tmp_path, resume_session_id=None)
        agent = self._build_agent()
        headless = agent.headless()
        if headless is None:
            pytest.skip("plugin does not provide HeadlessAgent")
        result = await headless.ask(
            self.headless_ask_prompt(),
            schema_model.model_json_schema(),
            self.headless_ask_model(),
            spec.rung.effort,
            spec.cwd,
            timedelta(seconds=_TURN_TIMEOUT_SECONDS),
        )
        schema_model.model_validate(result)

    @pytest.mark.asyncio
    async def test_live_low_compaction_threshold_preserves_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        first = self.build_spec(tmp_path, resume_session_id=None)
        await _init_scratch_repo(first.cwd)
        nonce = uuid.uuid4().hex
        started, usage = _latest_usage(
            await self._run_turn(
                _with_prompt(first, f"Remember this nonce for later in this session: {nonce}")
            )
        )
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=started,
            usage=usage,
        )
        signals = await self._run_turn(_with_prompt(resumed, RESUME_RECALL_PROMPT))
        _expect(
            any(isinstance(signal, Compacted) for signal in signals), "resumed turn did not compact"
        )
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        _expect(nonce in message, "compacted resumed turn forgot the nonce")

    def _require_live(self) -> None:
        if not self.runs_live_checks_in_default_gate and not os.environ.get(E2E_ENV_VAR):
            pytest.skip(f"set {E2E_ENV_VAR}=1 to run live agent contract checks")

    def _build_agent(self) -> Agent:
        return self.agent_factory()(
            AgentContext(
                options=self.agent_options(),
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )

    async def _run_turn(self, spec: TurnSpec) -> list[AgentSignal]:
        turn = await self._build_agent().start_turn(spec)
        async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
            return [signal async for signal in turn.signals()]


def _replay(parser: TurnParser, sample: RecordedStream) -> list[AgentSignal]:
    signals: list[AgentSignal] = []
    for line in sample.stdout.read_text().splitlines():
        signals.extend(parser.feed(line))
    signals.extend(parser.finish(sample.exit_code, sample.stderr.read_text()))
    return signals


def _process_outcomes(
    parser_for: Callable[[RecordedStream], TurnParser], samples: Sequence[RecordedStream]
) -> list[TurnProcessResult]:
    outcomes: list[TurnProcessResult] = []
    for sample in samples:
        signals = _replay(parser_for(sample), sample)
        outcomes.append(
            TurnProcessResult(
                signals=tuple(signals),
                exit_code=sample.exit_code,
                stderr=sample.stderr.read_text(),
            )
        )
    return outcomes


def assert_agent_obligations(signals: Sequence[AgentSignal], session_id: str | None) -> None:
    """Validate the stream-order, usage and tool-call obligations from 04 §1."""
    _validate_session_start(signals, session_id)
    _validate_terminal(signals)
    _validate_tool_sequences(signals)
    _validate_usage_sequences(signals)


def _validate_session_start(signals: Sequence[AgentSignal], session_id: str | None) -> None:
    starts = [
        (index, signal)
        for index, signal in enumerate(signals)
        if isinstance(signal, SessionStarted)
    ]
    _expect(len(starts) == 1, "turn must emit one SessionStarted")
    _expect(starts[0][0] == 0, "SessionStarted must be the first signal")
    started = starts[0][1]
    _expect(bool(started.session_id), "SessionStarted must carry a session id")
    if session_id is not None:
        _expect(started.session_id == session_id, "resumed turn changed its session id")


def _validate_terminal(signals: Sequence[AgentSignal]) -> None:
    terminals = [signal for signal in signals if isinstance(signal, (TurnEnded, TurnFailed))]
    _expect(len(terminals) == 1, "turn must emit one terminal signal")
    _expect(signals[-1] == terminals[0], "terminal signal must be last")


def _validate_tool_sequences(signals: Sequence[AgentSignal]) -> None:
    running_tools: set[str] = set()
    for signal in signals:
        if isinstance(signal, ToolStarted):
            _expect(signal.name not in running_tools, "tool started while already running")
            running_tools.add(signal.name)
        elif isinstance(signal, ToolFinished):
            _expect(signal.name in running_tools, "tool finished without a matching start")
            running_tools.remove(signal.name)
    _expect(not running_tools, "turn ended while a tool was still running")


def _validate_usage_sequences(signals: Sequence[AgentSignal]) -> None:
    last_usage: Usage | None = None
    compacted_since_usage = False
    for signal in signals:
        if isinstance(signal, Compacted):
            compacted_since_usage = True
        elif isinstance(signal, UsageReported):
            if last_usage is not None:
                _expect(
                    signal.usage.input_tokens >= last_usage.input_tokens, "input tokens decreased"
                )
                _expect(
                    signal.usage.output_tokens >= last_usage.output_tokens,
                    "output tokens decreased",
                )
                _expect(
                    signal.usage.cache_read_tokens >= last_usage.cache_read_tokens,
                    "cache-read tokens decreased",
                )
                _expect(
                    signal.usage.cache_write_tokens >= last_usage.cache_write_tokens,
                    "cache-write tokens decreased",
                )
                if (
                    compacted_since_usage
                    and last_usage.context_used_pct is not None
                    and signal.usage.context_used_pct is not None
                ):
                    _expect(
                        signal.usage.context_used_pct < last_usage.context_used_pct,
                        "context use did not drop after compaction",
                    )
            if signal.usage.cost_usd is None:
                _expect(signal.usage.cost_basis == "unknown", "unpriced usage must say unknown")
            else:
                _expect(signal.usage.cost_basis != "unknown", "priced usage needs a cost basis")
            last_usage = signal.usage
            compacted_since_usage = False


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _init_scratch_repo(path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await SubprocessRunner().run(
        ["git", "init"], timeout=_GIT_INIT_TIMEOUT_SECONDS, cwd=path, check=True
    )


def _prepare_report_turn(spec: TurnSpec) -> None:
    spec.turn_dir.mkdir(parents=True, exist_ok=True)
    (spec.turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
    (spec.turn_dir / REPORT_SCHEMA_FILENAME).write_text(
        json.dumps(schema_for(ReportKind.IMPLEMENTATION))
    )
    role_path = spec.run_dir / spec.role
    role_path.mkdir(parents=True, exist_ok=True)
    link = role_path / CURRENT_TURN_LINK_NAME
    link.unlink(missing_ok=True)
    link.symlink_to(spec.turn_dir)


def _validate_written_report(turn_dir: Path) -> None:
    kind = ReportKind((turn_dir / REPORT_KIND_FILENAME).read_text().strip())
    model_for(kind).model_validate_json((turn_dir / REPORT_FILENAME).read_text())


def _with_prompt(spec: TurnSpec, prompt: str) -> TurnSpec:
    return TurnSpec(
        rung=spec.rung,
        role=spec.role,
        prompt=prompt,
        cwd=spec.cwd,
        run_dir=spec.run_dir,
        turn_dir=spec.turn_dir,
        add_dirs=spec.add_dirs,
        env=spec.env,
        session_id=spec.session_id,
        usage=spec.usage,
    )


def _latest_usage(signals: Sequence[AgentSignal]) -> tuple[str, Usage]:
    session = next(signal for signal in signals if isinstance(signal, SessionStarted))
    usage = [signal.usage for signal in signals if isinstance(signal, UsageReported)]
    if session.session_id is None:
        message = "live turn returned no session id"
        raise AssertionError(message)
    _expect(bool(usage), "live turn reported no usage")
    return session.session_id, usage[-1]


@dataclass
class _SyntheticParser:
    def feed(self, line: str) -> list[AgentSignal]:
        entry = json.loads(line)
        event = entry["event"]
        if event == "session":
            return [SessionStarted(entry["session_id"])]
        if event == "compacted":
            return [Compacted(2000, 400)]
        if event == "message":
            return [Message(entry["text"])]
        if event == "ended":
            return [TurnEnded(entry["text"])]
        if event == "context_failure":
            return [TurnFailed(FailureKind.CONTEXT, entry["text"], None)]
        message = f"unrecognized synthetic event {event!r}"
        raise ValueError(message)

    def finish(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        del exit_code, stderr
        return []


class _SyntheticSequence:
    def __init__(self, *, second_failure: bool) -> None:
        self._second_failure = second_failure
        self._step = 0

    def start(self, spec: TurnSpec) -> TurnProcess:
        return self._process(spec, "session", "context_failure")

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        if self._step == 0:
            self._step += 1
            return self._process(spec, "compacted", "ended", text="compaction finished")
        if self._step == 1:
            self._step += 1
            event = "context_failure" if self._second_failure else "ended"
            text = "second context failure" if self._second_failure else "completed"
            return self._process(spec, "session", event, text=text)
        del previous
        return None

    @staticmethod
    def _process(spec: TurnSpec, *events: str, text: str = "first context failure") -> TurnProcess:
        lines: list[str] = []
        for event in events:
            entry = {"event": event, "session_id": _SYNTHETIC_SESSION_ID, "text": text}
            lines.append(json.dumps(entry))
        script = "\n".join(f"print({line!r})" for line in lines)
        return TurnProcess(
            argv=(sys.executable, "-c", script),
            cwd=spec.cwd,
            env=dict(spec.env),
            parser=_SyntheticParser(),
        )


async def _run_synthetic_compaction(tmp_path: Path, *, second_failure: bool) -> list[AgentSignal]:
    await asyncio.to_thread(tmp_path.mkdir, parents=True, exist_ok=True)
    spec = TurnSpec(
        rung=Rung(agent="synthetic", model="synthetic"),
        role="implementer",
        prompt="synthetic prompt",
        cwd=tmp_path,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={},
        session_id=None,
        usage=None,
    )
    turn = await StreamingTurn.start(
        spec, SubprocessRunner(), _SyntheticSequence(second_failure=second_failure)
    )
    return [signal async for signal in turn.signals()]
