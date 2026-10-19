"""Headless agent contract checks (04 §3), imported only by tests (02 §2)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    AgentContext,
    AgentSignal,
    Compacted,
    FailureKind,
    Message,
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
from shikiru.operator.probe import (
    REPORT_COMMAND,
    REPORT_INPUT_FILENAME,
    init_scratch_repo,
    live_report_problem,
    prepare_live_report,
)
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import BaseModel

    from shikiru.agents.contracts import Agent, AgentFactory

E2E_ENV_VAR = "SHIKIRU_E2E"
_TURN_TIMEOUT_SECONDS = 60.0
_COMMAND_FLAG = "-c"
_LIVE_COMPACTION_TIMEOUT_SECONDS = 900.0
_PYTEST_TIMEOUT_MARGIN_SECONDS = 60.0
_GROWTH_FILES = 6
_GROWTH_FILE_LINES = 400
_GROWTH_WORDS_PER_LINE = 10
_SYLLABLES = ("ka", "ro", "mit", "sel", "dun", "vo", "tra", "ple", "gor", "nis", "bu", "fex")
_SYLLABLES_PER_WORD = 3
_LIVE_COMPACTION_PROMPT = (
    f"Read part1.txt through part{_GROWTH_FILES}.txt in this repository in order, each file "
    "in full and one file per tool call. After each file, state its last line number in one "
    "short sentence.\n"
    "Then use Bash to run exactly this report command once:\n"
    f'{REPORT_COMMAND} "$SHIKIRU_RUN_DIR/{REPORT_INPUT_FILENAME}"\n'
    "After the command succeeds, reply with exactly: DONE followed by the nonce."
)
_LIVE_COMPACTION_TURN_PROMPT = (
    "Remember this nonce: {nonce}. Then read {prompt_path} and follow it exactly."
)
_REPLAY_SCRIPT = """
import json, sys
from pathlib import Path
plan = json.loads(sys.argv[1])
for path, content in plan["writes"].items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)
sys.stdout.write(Path(plan["stdout"]).read_text())
sys.stderr.write(Path(plan["stderr"]).read_text())
sys.exit(plan["exit_code"])
"""


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


class CompactionCase(StrEnum):
    """The obligation-8 rule one offline compaction scenario exercises (04 §3, check 3)."""

    THRESHOLD = auto()
    CONTEXT_FAILURE = auto()
    FAILED_RECOVERY = auto()


def _no_writes() -> Mapping[Path, str]:
    return {}


@dataclass(frozen=True)
class ReplayedProcess:
    """One recorded process, plus the files its agent wrote while it ran (hook marker, log)."""

    stream: RecordedStream
    writes: Mapping[Path, str] = field(default_factory=_no_writes)


@dataclass(frozen=True)
class CompactionScenario:
    """One turn replayed through the plugin's own process sequence and parsers."""

    name: str
    case: CompactionCase
    options: Mapping[str, Any]
    spec: TurnSpec
    processes: Sequence[ReplayedProcess]
    expected_argv: Sequence[tuple[str, ...]]


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
        """Return new, resume, model-change, environment and compaction cases."""
        raise NotImplementedError

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> TurnProcessSequence:
        """Return the same process sequence used by `agent.start_turn(spec)`."""
        raise NotImplementedError

    def compaction_scenarios(self, tmp_path: Path) -> Sequence[CompactionScenario]:
        """Return recorded turns covering every `CompactionCase` (offline check 3)."""
        raise NotImplementedError

    def live_compaction_options(self) -> Mapping[str, Any]:
        """Return options whose low `compact_at_pct` live check 6 grows past in one turn."""
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
    async def test_offline_compaction_continues_within_one_turn(self, tmp_path: Path) -> None:
        _expect(
            any(
                isinstance(signal, Compacted)
                for sample in self.recorded_streams()
                for signal in sample.expected_signals
            ),
            "recorded streams contain no Compacted signal",
        )
        scenarios = self.compaction_scenarios(tmp_path)
        _expect(
            {scenario.case for scenario in scenarios} == set(CompactionCase),
            "compaction scenarios must cover every CompactionCase",
        )
        for scenario in scenarios:
            signals, argv = await self._replay_scenario(scenario)
            _expect(argv == list(scenario.expected_argv), scenario.name)
            assert_agent_obligations(signals, scenario.spec.session_id)
            _expect(_COMPACTION_RULES[scenario.case](signals), scenario.name)

    async def _replay_scenario(
        self, scenario: CompactionScenario
    ) -> tuple[list[AgentSignal], list[tuple[str, ...]]]:
        agent = self._build_agent(scenario.options)
        replay = _ReplaySequence(self.process_sequence(agent, scenario.spec), scenario.processes)
        turn = await StreamingTurn.start(scenario.spec, SubprocessRunner(), replay)
        signals = [signal async for signal in turn.signals()]
        _expect(replay.exhausted, f"{scenario.name}: the turn ended before every process ran")
        return signals, replay.argv

    def test_offline_builds_expected_argv_environment_and_interactive_commands(
        self, tmp_path: Path
    ) -> None:
        for case in self.invocation_cases(tmp_path):
            context = AgentContext(
                options=case.options, runner=SubprocessRunner(), clock=SystemClock()
            )
            agent = self.agent_factory()(context)
            sequence = self.process_sequence(agent, case.spec)
            command: TurnProcess | None = sequence.start(case.spec)
            commands: list[TurnProcess] = []
            outcomes: list[TurnProcessResult] = []
            streams = iter(case.outcome_streams)
            while command is not None:
                commands.append(command)
                sample = next(streams, None)
                if sample is None:
                    break
                outcomes.append(_process_outcome(command.parser, sample))
                command = sequence.advance(case.spec, outcomes[-1])
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
                restarted = self.agent_factory()(context)
                _expect(
                    tuple(
                        restarted.interactive_command(
                            case.interactive_session_id, case.spec.run_dir, case.spec.rung
                        )
                    )
                    == case.expected_interactive_command,
                    case.name,
                )

    @pytest.mark.asyncio
    async def test_live_first_turn_writes_a_valid_report(self, tmp_path: Path) -> None:
        self._require_live()
        spec = await self._prepare_live_report_spec(
            self.build_spec(tmp_path, resume_session_id=None)
        )
        signals = await self._run_turn(spec)
        problem = await asyncio.to_thread(live_report_problem, spec, signals)
        _expect(problem is None, problem or "")

    async def _prepare_live_report_spec(self, spec: TurnSpec) -> TurnSpec:
        prompt_path = await prepare_live_report(SubprocessRunner(), spec)
        return _with_prompt(spec, str(prompt_path))

    @pytest.mark.asyncio
    async def test_live_usage_reports_session_tokens(self, tmp_path: Path) -> None:
        self._require_live()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await init_scratch_repo(SubprocessRunner(), spec.cwd)
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
        await init_scratch_repo(SubprocessRunner(), first.cwd)
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
        await init_scratch_repo(SubprocessRunner(), spec.cwd)
        agent = self._build_agent()
        turn = await agent.start_turn(_with_prompt(spec, "wait for the next instruction"))
        signals = turn.signals()
        started = await asyncio.wait_for(signals.__anext__(), _TURN_TIMEOUT_SECONDS)
        if not isinstance(started, SessionStarted):
            message = "live turn did not report a resumable session id"
            raise TypeError(message)
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
    @pytest.mark.timeout(_LIVE_COMPACTION_TIMEOUT_SECONDS + _PYTEST_TIMEOUT_MARGIN_SECONDS)
    async def test_live_turn_compacts_as_it_grows_and_completes(self, tmp_path: Path) -> None:
        self._require_live()
        spec = await self._prepare_live_report_spec(
            self.build_spec(tmp_path, resume_session_id=None)
        )
        prompt_path = Path(spec.prompt)
        await asyncio.to_thread(_write_growth_files, spec.cwd)
        await asyncio.to_thread(prompt_path.write_text, _LIVE_COMPACTION_PROMPT)
        nonce = uuid.uuid4().hex
        spec = _with_prompt(
            spec, _LIVE_COMPACTION_TURN_PROMPT.format(nonce=nonce, prompt_path=prompt_path)
        )
        signals = await self._run_turn(
            spec, self.live_compaction_options(), _LIVE_COMPACTION_TIMEOUT_SECONDS
        )
        problem = await asyncio.to_thread(live_report_problem, spec, signals)
        _expect(problem is None, problem or "")
        _expect(
            any(isinstance(signal, Compacted) for signal in signals), "the turn never compacted"
        )
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        _expect(nonce in message, "the compacted turn forgot the nonce")

    def _require_live(self) -> None:
        if not self.runs_live_checks_in_default_gate and not os.environ.get(E2E_ENV_VAR):
            pytest.skip(f"set {E2E_ENV_VAR}=1 to run live agent contract checks")

    def _build_agent(self, options: Mapping[str, Any] | None = None) -> Agent:
        return self.agent_factory()(
            AgentContext(
                options=self.agent_options() if options is None else options,
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )

    async def _run_turn(
        self,
        spec: TurnSpec,
        options: Mapping[str, Any] | None = None,
        timeout: float = _TURN_TIMEOUT_SECONDS,
    ) -> list[AgentSignal]:
        turn = await self._build_agent(options).start_turn(spec)
        async with asyncio.timeout(timeout):
            return [signal async for signal in turn.signals()]


def _replay(parser: TurnParser, sample: RecordedStream) -> list[AgentSignal]:
    signals: list[AgentSignal] = []
    for line in sample.stdout.read_text().splitlines():
        signals.extend(parser.feed(line))
    signals.extend(parser.finish(sample.exit_code, sample.stderr.read_text()))
    return signals


def _process_outcome(parser: TurnParser, sample: RecordedStream) -> TurnProcessResult:
    return TurnProcessResult(
        signals=tuple(_replay(parser, sample)),
        exit_code=sample.exit_code,
        stderr=sample.stderr.read_text(),
    )


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
                _expect(
                    signal.usage.cost_basis == COST_BASIS_UNKNOWN, "unpriced usage must say unknown"
                )
            else:
                _expect(
                    signal.usage.cost_basis != COST_BASIS_UNKNOWN, "priced usage needs a cost basis"
                )
            last_usage = signal.usage
            compacted_since_usage = False


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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
    _expect(bool(usage), "live turn reported no usage")
    return session.session_id, usage[-1]


def _ended(signals: Sequence[AgentSignal]) -> bool:
    return isinstance(signals[-1], TurnEnded)


_COMPACTION_RULES: dict[CompactionCase, Callable[[Sequence[AgentSignal]], bool]] = {
    CompactionCase.THRESHOLD: lambda signals: (
        _ended(signals) and sum(isinstance(signal, Compacted) for signal in signals) > 1
    ),
    CompactionCase.CONTEXT_FAILURE: lambda signals: (
        _ended(signals) and any(isinstance(signal, Compacted) for signal in signals)
    ),
    CompactionCase.FAILED_RECOVERY: lambda signals: (
        isinstance(signals[-1], TurnFailed) and signals[-1].kind is FailureKind.OTHER
    ),
}


class _ReplaySequence:
    """Runs the plugin's sequence, replacing each process with its recorded output."""

    def __init__(self, inner: TurnProcessSequence, processes: Sequence[ReplayedProcess]) -> None:
        self._inner = inner
        self._processes = list(processes)
        self._next = 0
        self.argv: list[tuple[str, ...]] = []

    @property
    def exhausted(self) -> bool:
        return self._next == len(self._processes)

    def start(self, spec: TurnSpec) -> TurnProcess:
        return self._replay(self._inner.start(spec))

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        process = self._inner.advance(spec, previous)
        return None if process is None else self._replay(process)

    def _replay(self, process: TurnProcess) -> TurnProcess:
        _expect(self._next < len(self._processes), f"unrecorded process {process.argv}")
        recorded = self._processes[self._next]
        self._next += 1
        self.argv.append(process.argv)
        plan = {
            "writes": {str(path): content for path, content in recorded.writes.items()},
            "stdout": str(recorded.stream.stdout),
            "stderr": str(recorded.stream.stderr),
            "exit_code": recorded.stream.exit_code,
        }
        return TurnProcess(
            argv=(sys.executable, _COMMAND_FLAG, _REPLAY_SCRIPT, json.dumps(plan)),
            cwd=process.cwd,
            env=process.env,
            parser=process.parser,
        )


def _write_growth_files(repo: Path) -> None:
    for number in range(1, _GROWTH_FILES + 1):
        lines = (
            f"{number}-{line:04d} "
            + " ".join(_growth_word(number, line, word) for word in range(_GROWTH_WORDS_PER_LINE))
            for line in range(1, _GROWTH_FILE_LINES + 1)
        )
        (repo / f"part{number}.txt").write_text("\n".join(lines) + "\n")


def _growth_word(*key: int) -> str:
    digest = hashlib.blake2b(repr(key).encode(), digest_size=_SYLLABLES_PER_WORD).digest()
    return "".join(_SYLLABLES[byte % len(_SYLLABLES)] for byte in digest)
