"""Exercise Codex's process-per-turn contract with recorded JSONL and live checks."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    Agent,
    AgentContext,
    AgentFactory,
    AgentSignal,
    Compacted,
    FailureKind,
    Message,
    QuotaReported,
    QuotaWindow,
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
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import stream_log_path
from shikiru.agents.testing import (
    AgentContractSuite,
    CompactionCase,
    CompactionScenario,
    InvocationCase,
    RecordedStream,
    ReplayedProcess,
)
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT
from shikiru.clock import SystemClock
from shikiru.domain.recovery import (
    QUOTA_MARGIN,
    Escalate,
    Failed,
    RecoveryPolicy,
    TurnCounters,
    WaitForQuota,
    decide,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.plugins.agents.codex.agent import CodexAgent, create
from shikiru.plugins.agents.codex.home import codex_home
from shikiru.plugins.agents.codex.options import (
    AUTO_COMPACT_LIMIT_CONFIG,
    CODEX_HOME_ENV_VAR,
    CONFIG_FLAG,
    MODEL_FLAG,
    MODELS_CACHE_FILENAME,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    SANDBOX_MODE_CONFIG,
    WORKSPACE_WRITE_SANDBOX,
    WRITABLE_ROOTS_CONFIG,
)
from shikiru.plugins.agents.codex.turn import CodexTurnParser, CodexTurnSequence, context_window
from shikiru.process import SubprocessRunner
from tests.e2e.diagnostics import SANDBOX_DENIAL_MARKERS
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "codex"
_ROLE = "implementer"
_MODEL = "gpt-5.6-sol"
_OTHER_MODEL = "gpt-5.6-terra"
_LIVE_MODEL = "gpt-5.6-luna"
_LIVE_OTHER_MODEL = "gpt-5.6-terra"
_EFFORT = "low"
_TEST_CONTEXT_WINDOW = 100_000
_ROLLOUT_CONTEXT_WINDOW = 250_000
_COMPACT_AT_PCT = 80.0
_PERCENT = 100
_TURN_TIMEOUT_SECONDS = 60.0
_LIVE_REPORT_TIMEOUT_SECONDS = 180.0
_LIVE_REPORT_PYTEST_TIMEOUT_SECONDS = 190.0
_STATUS_MESSAGE = "Codex headless contract failed"
_LIVE_ASK_PROMPT = "Return the JSON object with ok set to true."
_LIVE_STOP_PROMPT = (
    'Create checkpoint.txt containing "saved" using a shell command, then wait for instructions.'
)
_LIVE_COMMIT_PROMPT = (
    'Create first.txt containing "first" and commit it with git. Do not ask questions.'
)
_LIVE_SECOND_COMMIT_PROMPT = (
    'Create second.txt containing "second" and commit it with git. Do not ask questions.'
)
_WORKTREE_BRANCH = "shikiru-contract"
_INITIAL_FILE = "README.md"
_INITIAL_CONTENT = "contract repository\n"
_FIRST_FILE = "first.txt"
_SECOND_FILE = "second.txt"
_GIT_USER_NAME = "Shikiru Contract"
_GIT_USER_EMAIL = "shikiru-contract@example.invalid"
_FIRST_FILE_CONTENT = "first"
_SECOND_FILE_CONTENT = "second"
_EMPTY_STDERR = "turn_empty.stderr.log"
_MIDTURN_TURN = "turn_midturn_compaction_live_redacted.jsonl"
_MIDTURN_ROLLOUT = "rollout_midturn_compaction_live_redacted.jsonl"
_SESSION_MIDTURN = "session-midturn-live-redacted"
_SESSION_RECOVERY = "session-recovery"
_RERUN_ROLLOUT = "rollout_rerun_compaction.jsonl"
_MODEL_CHANGE_ROLLOUT = "rollout_model_change_turn_context_live_redacted.jsonl"
_LIVE_COMPACT_AT_PCT = 15.0
_SAMPLE_NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
_SESSION_FIRST = "session-first"
_SESSION_RESUMED = "session-resume_model_change"
_SESSION_OPENROUTER_LIVE = "session-openrouter-live"
_CONTEXT_WINDOW = 258_400
_USAGE_FIRST = Usage(55_675, 330, 52_096, 0, None, "unknown", 5.506578947368421)
_USAGE_RESUMED = Usage(129_393, 608, 105_344, 0, None, "unknown", 7.201238390092879)
_OPENROUTER_LIVE_CONTEXT_WINDOW = 997_500
_BUNDLED_CONTEXT_WINDOW = 272_000
_OPENROUTER_LIVE_INPUT_TOKENS = 12_340
_OPENROUTER_LIVE_OUTPUT_TOKENS = 5
_OPENROUTER_LIVE_CACHE_WRITE_TOKENS = 12_272
_OPENROUTER_LIVE_MESSAGE = "OK"
_CODEX_ENV_CAPTURE = "SHIKIRU_CODEX_ENV_CAPTURE"
_CODEX_BUNDLED_MODELS_JSON_ENV = "SHIKIRU_CODEX_BUNDLED_MODELS_JSON"
_CODEX_BUNDLED_MODELS_EXIT_CODE_ENV = "SHIKIRU_CODEX_BUNDLED_MODELS_EXIT_CODE"
_FAKE_TURN_SESSION_ID = "bundled-catalog-session"
_SYNTHETIC_OPENAI_KEY = "synthetic-openai-key"
_SYNTHETIC_CODEX_KEY = "synthetic-codex-key"
_SYNTHETIC_PROVIDER_KEY = "synthetic-provider-key"
_OPENROUTER_LIVE_USAGE = Usage(
    _OPENROUTER_LIVE_INPUT_TOKENS,
    _OPENROUTER_LIVE_OUTPUT_TOKENS,
    0,
    _OPENROUTER_LIVE_CACHE_WRITE_TOKENS,
    None,
    "unknown",
    _OPENROUTER_LIVE_INPUT_TOKENS / _OPENROUTER_LIVE_CONTEXT_WINDOW * 100,
)
_FIRST_SIGNALS = [
    SessionStarted(_SESSION_FIRST),
    Message("Working on the change."),
    ToolStarted("command", ""),
    ToolFinished("command"),
    ToolStarted("file_change", ""),
    ToolFinished("file_change"),
    ToolStarted("command", ""),
    ToolFinished("command"),
    Message("Change committed."),
    QuotaReported(
        (
            QuotaWindow("primary", 20.0, None, _SAMPLE_NOW),
            QuotaWindow("secondary", 29.0, None, _SAMPLE_NOW),
        )
    ),
    UsageReported(_USAGE_FIRST),
    TurnEnded("Change committed."),
]
_RESUMED_SIGNALS = [
    SessionStarted(_SESSION_RESUMED),
    Message("Working on the change."),
    ToolStarted("command", ""),
    ToolFinished("command"),
    ToolStarted("file_change", ""),
    ToolFinished("file_change"),
    ToolStarted("command", ""),
    ToolFinished("command"),
    Message("Change committed."),
    QuotaReported(
        (
            QuotaWindow("primary", 21.0, None, _SAMPLE_NOW),
            QuotaWindow("secondary", 30.0, None, _SAMPLE_NOW),
        )
    ),
    UsageReported(_USAGE_RESUMED),
    TurnEnded("Change committed."),
]
_OPENROUTER_LIVE_SIGNALS = [
    SessionStarted(_SESSION_OPENROUTER_LIVE),
    Message(_OPENROUTER_LIVE_MESSAGE),
    UsageReported(_OPENROUTER_LIVE_USAGE),
    TurnEnded(_OPENROUTER_LIVE_MESSAGE),
]

_MIDTURN_PARTS = range(1, 9)
_MIDTURN_SIGNALS = [
    SessionStarted(_SESSION_MIDTURN),
    *(
        signal
        for part in _MIDTURN_PARTS
        for signal in (
            ToolStarted("command", f"cat [redacted]/part{part}.txt"),
            ToolFinished("command"),
            Message(f"part{part}.txt ends at line 400."),
        )
    ),
    Message("DONE KESTREL-4471"),
    Compacted(36_697, 14_270),
    Compacted(37_330, 14_387),
    UsageReported(Usage(230_585, 1_145, 152_320, 0, None, "unknown", 37_348 / 258_400 * 100)),
    TurnEnded("DONE KESTREL-4471"),
]


@pytest.fixture
def operator_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "operator-codex-home"
    home.mkdir()
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))
    monkeypatch.setenv("OPENAI_API_KEY", _SYNTHETIC_OPENAI_KEY)
    monkeypatch.setenv("CODEX_API_KEY", _SYNTHETIC_CODEX_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", _SYNTHETIC_PROVIDER_KEY)
    return home


class _AskResult(BaseModel):
    ok: bool


class TestCodexHeadlessContract(AgentContractSuite):
    """Exercise Codex's process events, rollout signals and optional live checks."""

    runs_live_checks_in_default_gate = False
    _live = False
    _active_home = Path()
    _live_turn_timeout_seconds = _TURN_TIMEOUT_SECONDS

    async def _run_turn(
        self,
        spec: TurnSpec,
        options: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> list[AgentSignal]:
        turn = await self._build_agent(options).start_turn(spec)
        async with asyncio.timeout(timeout or self._live_turn_timeout_seconds):
            return [signal async for signal in turn.signals()]

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> Mapping[str, object]:
        return {"compact_at_pct": _COMPACT_AT_PCT}

    def build_spec(
        self,
        tmp_path: Path,
        *,
        resume_session_id: str | None,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> TurnSpec:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True, exist_ok=True)
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        env = {RUN_DIR_ENV_VAR: str(run_dir), ROLE_ENV_VAR: _ROLE}
        if not self._live:
            home = tmp_path / "codex-home"
            self._prepare_home(home, repo)
            env[CODEX_HOME_ENV_VAR] = str(home)
        turn_name = "01-turn" if resume_session_id is None else "02-turn"
        return TurnSpec(
            rung=Rung(agent="codex", model=model or self._model(), effort=_EFFORT),
            role=_ROLE,
            prompt="complete the requested turn",
            cwd=repo,
            run_dir=run_dir,
            turn_dir=tmp_path / "turns" / turn_name,
            add_dirs=(),
            env=env,
            session_id=resume_session_id,
            usage=usage,
        )

    def parser_for(self, sample: RecordedStream) -> CodexTurnParser:
        clock = FakeClock(_SAMPLE_NOW)
        return CodexTurnParser(
            session_id=None,
            initial_usage=None,
            home=_FIXTURES_DIR / "offline-home",
            rollout_path=sample.session_logs.get("rollout"),
            clock=clock,
            reset_time=ResetTimeParser(clock),
        )

    def recorded_streams(self) -> Sequence[RecordedStream]:
        return [
            _record(
                "turn_first.jsonl",
                "rollout_first.jsonl",
                session_id=_SESSION_FIRST,
                expected_signals=_FIRST_SIGNALS,
                stderr_name="turn_first.stderr.log",
            ),
            _record(
                "turn_resume_model_change.jsonl",
                "rollout_resume_model_change.jsonl",
                session_id=_SESSION_RESUMED,
                expected_signals=_RESUMED_SIGNALS,
                stderr_name="turn_resume_model_change.stderr.log",
            ),
            _record(
                _MIDTURN_TURN,
                _MIDTURN_ROLLOUT,
                session_id=_SESSION_MIDTURN,
                expected_signals=_MIDTURN_SIGNALS,
            ),
            _record(
                "turn_openrouter_live_redacted.jsonl",
                "rollout_openrouter_live_redacted.jsonl",
                session_id=_SESSION_OPENROUTER_LIVE,
                expected_signals=_OPENROUTER_LIVE_SIGNALS,
                stderr_name="turn_openrouter_live_redacted.stderr.log",
            ),
        ]

    def _model_change_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        windows = {_MODEL: _TEST_CONTEXT_WINDOW, _OTHER_MODEL: _ROLLOUT_CONTEXT_WINDOW}
        cases: list[InvocationCase] = []
        for selected, previous in ((_OTHER_MODEL, _MODEL), (_MODEL, _OTHER_MODEL)):
            spec = self.build_spec(
                tmp_path / f"change-to-{selected}",
                resume_session_id="session-changed",
                model=selected,
            )
            home = Path(spec.env[CODEX_HOME_ENV_VAR])
            _write_model_cache(home, list(windows.items()))
            _write_rollout_window(home, spec.session_id, windows[previous], previous)
            limit = int(windows[selected] * _COMPACT_AT_PCT / 100)
            cases.append(
                InvocationCase(
                    name=f"a model change to {selected} drops the window of {previous}",
                    options={"compact_at_pct": _COMPACT_AT_PCT},
                    spec=spec,
                    expected_argv=[
                        (
                            "codex",
                            "exec",
                            RESUME_SUBCOMMAND,
                            "session-changed",
                            "--json",
                            MODEL_FLAG,
                            selected,
                            CONFIG_FLAG,
                            f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
                            CONFIG_FLAG,
                            f"{AUTO_COMPACT_LIMIT_CONFIG}={limit}",
                            *_sandbox_args(spec),
                            spec.prompt,
                        )
                    ],
                    expected_env=[{CODEX_HOME_ENV_VAR: spec.env[CODEX_HOME_ENV_VAR]}],
                )
            )
        return cases

    def error_streams(self) -> Sequence[RecordedStream]:
        return []

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        new_spec = self.build_spec(tmp_path / "new", resume_session_id=None)
        new_argv = (
            "codex",
            "exec",
            "--json",
            MODEL_FLAG,
            _MODEL,
            CONFIG_FLAG,
            f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
            CONFIG_FLAG,
            f"{AUTO_COMPACT_LIMIT_CONFIG}=80000",
            *_sandbox_args(new_spec),
            new_spec.prompt,
        )
        resumed = self.build_spec(
            tmp_path / "resume",
            resume_session_id="session-resumed",
            model=_OTHER_MODEL,
        )
        resumed_home = Path(resumed.env[CODEX_HOME_ENV_VAR])
        _write_rollout_window(
            resumed_home, resumed.session_id, _ROLLOUT_CONTEXT_WINDOW, _OTHER_MODEL
        )
        resumed_argv = (
            "codex",
            "exec",
            RESUME_SUBCOMMAND,
            "session-resumed",
            "--json",
            MODEL_FLAG,
            _OTHER_MODEL,
            CONFIG_FLAG,
            f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
            CONFIG_FLAG,
            f"{AUTO_COMPACT_LIMIT_CONFIG}=200000",
            *_sandbox_args(resumed),
            resumed.prompt,
        )
        provider_spec = self.build_spec(
            tmp_path / "provider",
            resume_session_id=None,
        )
        provider_spec = TurnSpec(
            rung=provider_spec.rung,
            role=provider_spec.role,
            prompt=provider_spec.prompt,
            cwd=provider_spec.cwd,
            run_dir=provider_spec.run_dir,
            turn_dir=provider_spec.turn_dir,
            add_dirs=provider_spec.add_dirs,
            env={
                **provider_spec.env,
                "OPENAI_API_KEY": _SYNTHETIC_OPENAI_KEY,
                "CODEX_API_KEY": _SYNTHETIC_CODEX_KEY,
                "OPENROUTER_API_KEY": _SYNTHETIC_PROVIDER_KEY,
            },
            session_id=provider_spec.session_id,
            usage=provider_spec.usage,
        )
        return [
            *self._model_change_cases(tmp_path),
            InvocationCase(
                name="new turns use workspace-write and grant the run and git directories",
                options={"compact_at_pct": _COMPACT_AT_PCT},
                spec=new_spec,
                expected_argv=[new_argv],
                expected_env=[{CODEX_HOME_ENV_VAR: new_spec.env[CODEX_HOME_ENV_VAR]}],
            ),
            InvocationCase(
                name="a resumed session grants the same roots and the same-model rollout window",
                options={"compact_at_pct": _COMPACT_AT_PCT},
                spec=resumed,
                expected_argv=[resumed_argv],
                expected_env=[{CODEX_HOME_ENV_VAR: resumed.env[CODEX_HOME_ENV_VAR]}],
                interactive_session_id="session-resumed",
                expected_interactive_command=("codex", "resume", "--no-daemon", "session-resumed"),
            ),
            InvocationCase(
                name="turn passes TurnSpec provider environment through unchanged",
                options={"args": []},
                spec=provider_spec,
                expected_argv=[
                    (
                        "codex",
                        "exec",
                        "--json",
                        MODEL_FLAG,
                        _MODEL,
                        CONFIG_FLAG,
                        f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
                        CONFIG_FLAG,
                        f"{AUTO_COMPACT_LIMIT_CONFIG}=80000",
                        *_sandbox_args(provider_spec),
                        provider_spec.prompt,
                    )
                ],
                expected_env=[
                    {
                        CODEX_HOME_ENV_VAR: provider_spec.env[CODEX_HOME_ENV_VAR],
                        "OPENAI_API_KEY": _SYNTHETIC_OPENAI_KEY,
                        "CODEX_API_KEY": _SYNTHETIC_CODEX_KEY,
                        "OPENROUTER_API_KEY": _SYNTHETIC_PROVIDER_KEY,
                    }
                ],
            ),
        ]

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> CodexTurnSequence:
        return cast("CodexAgent", agent).process_sequence(spec, git_dirs=_fake_git_dirs(spec))

    def live_compaction_options(self) -> Mapping[str, object]:
        return {"compact_at_pct": _LIVE_COMPACT_AT_PCT}

    def compaction_scenarios(self, tmp_path: Path) -> Sequence[CompactionScenario]:
        options = {"compact_at_pct": _COMPACT_AT_PCT}
        midturn = self.build_spec(tmp_path / "midturn", resume_session_id=_SESSION_MIDTURN)
        recovery = self.build_spec(tmp_path / "recovery", resume_session_id=_SESSION_RECOVERY)
        context_failure = ReplayedProcess(
            _record(
                "turn_context_failure.jsonl",
                None,
                session_id=_SESSION_RECOVERY,
                expected_signals=(),
            )
        )
        rerun = ReplayedProcess(
            _record(
                "turn_rerun_success.jsonl", None, session_id=_SESSION_RECOVERY, expected_signals=()
            ),
            {_rollout_file(recovery): (_FIXTURES_DIR / _RERUN_ROLLOUT).read_text()},
        )
        return [
            CompactionScenario(
                name="Codex compacts twice within one running turn",
                case=CompactionCase.THRESHOLD,
                options=options,
                spec=midturn,
                processes=[
                    ReplayedProcess(
                        _record(
                            _MIDTURN_TURN, None, session_id=_SESSION_MIDTURN, expected_signals=()
                        ),
                        {_rollout_file(midturn): (_FIXTURES_DIR / _MIDTURN_ROLLOUT).read_text()},
                    )
                ],
                expected_argv=[_resume_argv(midturn)],
            ),
            CompactionScenario(
                name="a context failure reruns the prompt with the limit, compacting first",
                case=CompactionCase.CONTEXT_FAILURE,
                options=options,
                spec=recovery,
                processes=[context_failure, rerun],
                expected_argv=[_resume_argv(recovery), _resume_argv(recovery)],
            ),
            CompactionScenario(
                name="a second context failure ends the turn",
                case=CompactionCase.FAILED_RECOVERY,
                options=options,
                spec=recovery,
                processes=[context_failure, context_failure],
                expected_argv=[_resume_argv(recovery), _resume_argv(recovery)],
            ),
        ]

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def headless_ask_model(self) -> str:
        return _LIVE_MODEL

    def _model(self) -> str:
        return _LIVE_MODEL if self._live else _MODEL

    def _other_model(self) -> str:
        return _LIVE_OTHER_MODEL if self._live else _OTHER_MODEL

    def headless_ask_prompt(self) -> str:
        return _LIVE_ASK_PROMPT

    @pytest.mark.parametrize(
        ("model", "expected"),
        [(_OTHER_MODEL, _CONTEXT_WINDOW), (_LIVE_MODEL, _TEST_CONTEXT_WINDOW)],
        ids=[
            "the model of the latest turn uses the observed window",
            "another model uses its cache",
        ],
    )
    def test_model_change_rollout_window_belongs_to_the_model_that_reported_it(
        self, tmp_path: Path, model: str, expected: int
    ) -> None:
        home = tmp_path / "codex-home"
        session_dir = home / "sessions" / "2030" / "01" / "01"
        session_dir.mkdir(parents=True)
        rollout = session_dir / f"rollout-2030-01-01T00-00-00-{_SESSION_RESUMED}.jsonl"
        rollout.write_text((_FIXTURES_DIR / _MODEL_CHANGE_ROLLOUT).read_text(), encoding="utf-8")
        _write_model_cache(
            home, [(_OTHER_MODEL, _TEST_CONTEXT_WINDOW), (_LIVE_MODEL, _TEST_CONTEXT_WINDOW)]
        )

        assert context_window(home, model, _SESSION_RESUMED) == expected

    def test_turn_inherits_operator_environment_and_uses_turn_codex_home(
        self, tmp_path: Path, operator_codex_home: Path
    ) -> None:
        spec = self.build_spec(tmp_path, resume_session_id=None)
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        command = agent.process_sequence(spec, git_dirs=(tmp_path / ".git",)).start(spec)

        assert command.env[CODEX_HOME_ENV_VAR] == spec.env[CODEX_HOME_ENV_VAR]
        assert command.env[CODEX_HOME_ENV_VAR] != str(operator_codex_home)
        assert command.env["OPENAI_API_KEY"] == _SYNTHETIC_OPENAI_KEY
        assert command.env["CODEX_API_KEY"] == _SYNTHETIC_CODEX_KEY
        assert command.env["OPENROUTER_API_KEY"] == _SYNTHETIC_PROVIDER_KEY
        assert f"{AUTO_COMPACT_LIMIT_CONFIG}=80000" in command.argv

    def test_offline_maps_errors_and_preserves_unknown_failures(self) -> None:
        cases = (
            (
                {
                    "status": 429,
                    "code": "usage_limit_reached",
                    "message": "usage limit reached",
                    "resets_at": "2030-01-01T01:00:00Z",
                },
                TurnFailed(
                    FailureKind.QUOTA,
                    "usage limit reached",
                    datetime(2030, 1, 1, 1, tzinfo=UTC),
                ),
            ),
            (
                {"status": 402, "message": "payment required"},
                TurnFailed(FailureKind.AUTH, "payment required", None),
            ),
            (
                {"status": 503, "message": "temporarily unavailable"},
                TurnFailed(FailureKind.UNAVAILABLE, "temporarily unavailable", None),
            ),
            (
                {"status": 401, "message": "unauthorized"},
                TurnFailed(FailureKind.AUTH, "unauthorized", None),
            ),
            (
                {
                    "status": 400,
                    "code": "context_length_exceeded",
                    "message": "prompt exceeds context window",
                },
                TurnFailed(FailureKind.OTHER, "prompt exceeds context window", None),
            ),
            (
                {"status": 418, "message": "unrecognized response"},
                TurnFailed(FailureKind.UNKNOWN, "unrecognized response", None),
            ),
        )
        for index, (error, expected) in enumerate(cases):
            assert _failure_for(error, f"failure-{index}") == expected

    def test_payment_failure_escalates_and_rate_limit_waits_for_reported_reset(self) -> None:
        payment_failure = _failure_for(
            {"status": 402, "code": "payment_required", "message": "credits exhausted"},
            "payment-failure",
        )
        payment_action = decide(
            Failed(payment_failure.kind, payment_failure.resets_at, payment_failure.message),
            TurnCounters(),
            RecoveryPolicy(
                now=_SAMPLE_NOW,
                max_quota_wait=timedelta(hours=8),
                has_next_rung=False,
                has_next_rung_different_agent=False,
            ),
        )

        reset_at = _SAMPLE_NOW + timedelta(minutes=10)
        rate_limit_failure = _failure_for(
            {
                "status": 429,
                "code": "usage_limit_reached",
                "message": "usage limit reached",
                "resets_at": reset_at.isoformat(),
            },
            "rate-limit-failure",
        )
        rate_limit_action = decide(
            Failed(
                rate_limit_failure.kind, rate_limit_failure.resets_at, rate_limit_failure.message
            ),
            TurnCounters(),
            RecoveryPolicy(
                now=_SAMPLE_NOW,
                max_quota_wait=timedelta(hours=8),
                has_next_rung=False,
                has_next_rung_different_agent=False,
            ),
        )

        assert payment_failure.kind is FailureKind.AUTH
        assert payment_action == Escalate(EscalationReason.AGENT_AUTH)
        assert rate_limit_failure.kind is FailureKind.QUOTA
        assert rate_limit_failure.resets_at == reset_at
        assert rate_limit_action == WaitForQuota(reset_at + QUOTA_MARGIN)

    @pytest.mark.asyncio
    async def test_headless_ask_preserves_operator_provider_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator_codex_home: Path
    ) -> None:
        capture_path = tmp_path / "headless-environment.txt"
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(capture_path))
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))
        headless = agent.headless()
        assert headless is not None

        result = await headless.ask(
            "return true",
            _AskResult.model_json_schema(),
            _MODEL,
            _EFFORT,
            tmp_path,
            timedelta(seconds=10),
        )

        assert result == {"ok": True}
        assert capture_path.read_text().splitlines()[1:] == [
            str(operator_codex_home),
            _SYNTHETIC_OPENAI_KEY,
            _SYNTHETIC_CODEX_KEY,
            _SYNTHETIC_PROVIDER_KEY,
        ]

    @pytest.mark.asyncio
    async def test_doctor_does_not_apply_openai_login_status_to_custom_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator_codex_home: Path
    ) -> None:
        (operator_codex_home / "config.toml").write_text(
            'model_provider = "openrouter"\n\n'
            "[model_providers.openrouter]\n"
            'name = "OpenRouter"\n'
            'base_url = "https://provider.invalid/api/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "OPENROUTER_API_KEY"\n',
            encoding="utf-8",
        )
        capture_path = tmp_path / "doctor-environment.txt"
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(capture_path))
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        checks = await agent.doctor(())

        login = next(check for check in checks if check.name == "login")
        assert all(check.ok for check in checks)
        assert "not verified" in login.detail
        calls = capture_path.read_text().splitlines()
        invocations, environments = (
            calls[0::5],
            [calls[index : index + 5][1:] for index in range(0, len(calls), 5)],
        )
        assert [invocation.split()[0] for invocation in invocations] == [
            "--version",
            "--version",
            "sandbox",
        ]
        assert all(
            environment
            == [
                str(operator_codex_home),
                _SYNTHETIC_OPENAI_KEY,
                _SYNTHETIC_CODEX_KEY,
                _SYNTHETIC_PROVIDER_KEY,
            ]
            for environment in environments
        )

    @pytest.mark.asyncio
    async def test_doctor_fails_for_a_configured_model_without_a_known_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator_codex_home: Path
    ) -> None:
        _write_model_cache(operator_codex_home, [(_MODEL, _TEST_CONTEXT_WINDOW)])
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(tmp_path / "doctor-environment.txt"))
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        known = await agent.doctor((_MODEL,))
        unknown = await agent.doctor((_MODEL, "unlisted-model"))

        assert next(check for check in known if check.name == "context_window").ok
        window = next(check for check in unknown if check.name == "context_window")
        assert not window.ok
        assert "unlisted-model" in window.detail
        assert _MODEL not in window.detail

    @pytest.mark.asyncio
    async def test_doctor_and_turn_use_the_cli_bundled_window_when_home_cache_omits_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator_codex_home: Path
    ) -> None:
        _write_model_cache(operator_codex_home, [(_MODEL, _TEST_CONTEXT_WINDOW)])
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(tmp_path / "doctor-environment.txt"))
        monkeypatch.setenv(
            _CODEX_BUNDLED_MODELS_JSON_ENV,
            json.dumps(
                {"models": [{"slug": _LIVE_MODEL, "context_window": _BUNDLED_CONTEXT_WINDOW}]}
            ),
        )
        spec = _with_model(self.build_spec(tmp_path, resume_session_id=None), _LIVE_MODEL)
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        checks = await agent.doctor((_LIVE_MODEL,))
        context_check = next(check for check in checks if check.name == "context_window")
        command = agent.process_sequence(spec, git_dirs=_fake_git_dirs(spec)).start(spec)

        assert context_check.ok
        assert "Codex CLI bundled model catalog" in context_check.detail
        assert f"{AUTO_COMPACT_LIMIT_CONFIG}={_limit_for(_BUNDLED_CONTEXT_WINDOW)}" in command.argv

    @pytest.mark.asyncio
    async def test_new_engine_agent_resolves_bundled_window_when_starting_a_turn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        capture_path = tmp_path / "turn-environment.txt"
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(capture_path))
        monkeypatch.setenv(
            _CODEX_BUNDLED_MODELS_JSON_ENV,
            json.dumps(
                {"models": [{"slug": _LIVE_MODEL, "context_window": _BUNDLED_CONTEXT_WINDOW}]}
            ),
        )
        spec = _with_model(self.build_spec(tmp_path, resume_session_id=None), _LIVE_MODEL)
        await _init_live_repo(spec.cwd)
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        turn = await agent.start_turn(spec)
        signals = [signal async for signal in turn.signals()]
        invocations = capture_path.read_text(encoding="utf-8").splitlines()[::5]

        assert invocations[0] == "debug models --bundled"
        assert (
            f"{AUTO_COMPACT_LIMIT_CONFIG}={_limit_for(_BUNDLED_CONTEXT_WINDOW)}" in invocations[1]
        )
        assert any(isinstance(signal, TurnEnded) for signal in signals)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("catalog_output", "exit_code"),
        [("not-json", 0), ('{"models": []}', 1)],
        ids=["invalid-json", "command-failed"],
    )
    async def test_doctor_reports_bundled_catalog_errors_without_accepting_unknown_window(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        operator_codex_home: Path,
        catalog_output: str,
        exit_code: int,
    ) -> None:
        _write_model_cache(operator_codex_home, [])
        _install_fake_codex(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(tmp_path / "doctor-environment.txt"))
        monkeypatch.setenv(_CODEX_BUNDLED_MODELS_JSON_ENV, catalog_output)
        monkeypatch.setenv(_CODEX_BUNDLED_MODELS_EXIT_CODE_ENV, str(exit_code))
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        checks = await agent.doctor((_LIVE_MODEL,))
        context_check = next(check for check in checks if check.name == "context_window")

        assert not context_check.ok
        assert "bundled model catalog" in context_check.detail
        assert _LIVE_MODEL in context_check.detail

    def _require_live(self) -> None:
        super()._require_live()
        self._live = True

    @pytest.mark.asyncio
    @pytest.mark.timeout(_LIVE_REPORT_PYTEST_TIMEOUT_SECONDS)
    async def test_live_first_turn_writes_a_valid_report(self, tmp_path: Path) -> None:
        self._require_live()
        self._live_turn_timeout_seconds = _LIVE_REPORT_TIMEOUT_SECONDS
        await super().test_live_first_turn_writes_a_valid_report(tmp_path)

    @pytest.mark.asyncio
    async def test_live_stop_leaves_session_resumable(self, tmp_path: Path) -> None:
        self._require_live()
        spec = self.build_spec(tmp_path, resume_session_id=None)
        await _init_live_repo(spec.cwd)
        turn = await self._build_agent().start_turn(_with_prompt(spec, _LIVE_STOP_PROMPT))
        signals = turn.signals()
        async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
            started = await signals.__anext__()
            assert isinstance(started, SessionStarted), "live turn omitted SessionStarted"
            assert started.session_id is not None, "live turn omitted its resumable session id"
            tool_started = False
            async for signal in signals:
                if isinstance(signal, ToolStarted):
                    tool_started = True
                    break
                assert not isinstance(signal, TurnEnded | TurnFailed), (
                    "live turn ended before a tool started"
                )
            assert tool_started, "live turn emitted no tool start"
        await turn.stop()
        async for _ in signals:
            pass
        resumed = self.build_spec(tmp_path, resume_session_id=started.session_id)
        resumed_signals = await self._run_turn(resumed)
        assert isinstance(resumed_signals[-1], TurnEnded)

    def _build_agent(self, options: Mapping[str, Any] | None = None) -> Agent:
        if self._live:
            return super()._build_agent(options)
        with _codex_home_environment(self._active_home):
            return super()._build_agent(options)

    def _prepare_home(self, home: Path, repo: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        _write_model_cache(
            home, [(_MODEL, _TEST_CONTEXT_WINDOW), (_OTHER_MODEL, _TEST_CONTEXT_WINDOW)]
        )
        project = json.dumps(str(repo), ensure_ascii=False)
        (home / "config.toml").write_text(
            f"check_for_update_on_startup = false\n\n[projects.{project}]\n"
            'trust_level = "trusted"\n',
            encoding="utf-8",
        )
        self._active_home = home

    @pytest.mark.asyncio
    async def test_live_doctor_checks_binary_version_and_authentication(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        self.build_spec(tmp_path, resume_session_id=None)
        checks = await self._build_agent().doctor((self._model(), self._other_model()))
        assert all(check.ok for check in checks)

    @pytest.mark.asyncio
    async def test_live_worktree_commits_survive_exec_resume(self, e2e_project_dir: Path) -> None:
        self._require_live()
        tmp_path = e2e_project_dir
        main_repo = tmp_path / "main"
        worktree = tmp_path / "worktree"
        main_repo.mkdir()
        runner = SubprocessRunner()
        await runner.run(["git", "init", "-b", "main"], timeout=30, cwd=main_repo, check=True)
        await runner.run(
            ["git", "config", "user.name", _GIT_USER_NAME],
            timeout=30,
            cwd=main_repo,
            check=True,
        )
        await runner.run(
            ["git", "config", "user.email", _GIT_USER_EMAIL],
            timeout=30,
            cwd=main_repo,
            check=True,
        )
        (main_repo / _INITIAL_FILE).write_text(_INITIAL_CONTENT, encoding="utf-8")
        await runner.run(["git", "add", _INITIAL_FILE], timeout=30, cwd=main_repo, check=True)
        await runner.run(["git", "commit", "-m", "initial"], timeout=30, cwd=main_repo, check=True)
        await runner.run(
            ["git", "worktree", "add", "-b", _WORKTREE_BRANCH, str(worktree)],
            timeout=30,
            cwd=main_repo,
            check=True,
        )
        spec = self.build_spec(tmp_path, resume_session_id=None)
        first = _replace_cwd(spec, worktree, "first-turn")
        first_signals = await self._run_turn(_with_prompt(first, _LIVE_COMMIT_PROMPT))
        session_id, usage = _latest_usage(first_signals)
        first_commit = await runner.run(
            ["git", "show", f"HEAD:{_FIRST_FILE}"], timeout=30, cwd=worktree, check=True
        )
        assert first_commit.stdout.strip() == _FIRST_FILE_CONTENT
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=session_id,
            usage=usage,
            model=self._other_model(),
        )
        resumed = _replace_cwd(resumed, worktree, "second-turn")
        await self._run_turn(_with_prompt(resumed, _LIVE_SECOND_COMMIT_PROMPT))
        for turn in (first, resumed):
            transcript = stream_log_path(turn.turn_dir, turn.role).read_text(errors="replace")
            denied = [marker for marker in SANDBOX_DENIAL_MARKERS if marker in transcript]
            assert not denied, f"sandbox denial {denied} in {turn.turn_dir}"
        second_commit = await runner.run(
            ["git", "show", f"HEAD:{_SECOND_FILE}"], timeout=30, cwd=worktree, check=True
        )
        status = await runner.run(["git", "status", "--porcelain"], timeout=30, cwd=worktree)
        assert second_commit.stdout.strip() == _SECOND_FILE_CONTENT
        assert status.stdout == ""

    @pytest.mark.asyncio
    async def test_live_model_change_resumes_and_recalls_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        home = codex_home()
        first = self.build_spec(tmp_path, resume_session_id=None)
        await _init_live_repo(first.cwd)
        nonce = uuid4().hex
        first = _with_prompt(first, RESUME_NONCE_PROMPT.format(nonce=nonce))
        first_limit = self._emitted_limit(first)
        assert first_limit == _limit_for(_cached_window(home, self._model()))
        session_id, usage = _latest_usage(await self._run_turn(first))
        resumed = _with_prompt(
            self.build_spec(
                tmp_path, resume_session_id=session_id, usage=usage, model=self._other_model()
            ),
            RESUME_RECALL_PROMPT,
        )
        resumed_limit = self._emitted_limit(resumed)

        signals = await self._run_turn(resumed)

        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        assert nonce in message
        assert resumed_limit == _limit_for(_cached_window(home, self._other_model()))
        observed = _rollout_windows(home, session_id)
        assert observed[-1][0] == self._other_model()
        assert self._emitted_limit(resumed) == _limit_for(observed[-1][1])
        back = _with_model(resumed, self._model())
        assert self._emitted_limit(back) == _limit_for(_cached_window(home, self._model()))

    def _emitted_limit(self, spec: TurnSpec) -> int:
        agent = cast("CodexAgent", self._build_agent())
        argv = agent.process_sequence(spec, git_dirs=_fake_git_dirs(spec)).start(spec).argv
        assert MODEL_FLAG in argv
        assert argv[argv.index(MODEL_FLAG) + 1] == spec.rung.model
        setting = f"{AUTO_COMPACT_LIMIT_CONFIG}="
        (limit,) = [value.removeprefix(setting) for value in argv if value.startswith(setting)]
        return int(limit)


def _fake_git_dirs(spec: TurnSpec) -> tuple[Path, ...]:
    return ((spec.cwd / ".git").resolve(),)


def _sandbox_args(spec: TurnSpec) -> tuple[str, ...]:
    roots = json.dumps([str(spec.run_dir), str((spec.cwd / ".git").resolve())])
    return (
        CONFIG_FLAG,
        f'{SANDBOX_MODE_CONFIG}="{WORKSPACE_WRITE_SANDBOX}"',
        CONFIG_FLAG,
        f"{WRITABLE_ROOTS_CONFIG}={roots}",
    )


def _with_model(spec: TurnSpec, model: str) -> TurnSpec:
    return replace(spec, rung=replace(spec.rung, model=model))


def _limit_for(window: int) -> int:
    return int(window * _COMPACT_AT_PCT / _PERCENT)


def _cached_window(home: Path, model: str) -> int:
    cache = json.loads((home / MODELS_CACHE_FILENAME).read_text(encoding="utf-8"))
    (entry,) = [item for item in cache["models"] if item["slug"] == model]
    return int(entry["context_window"])


def _rollout_windows(home: Path, session_id: str) -> list[tuple[str, int]]:
    (path,) = (home / "sessions").glob(f"**/rollout-*-{session_id}.jsonl")
    model = ""
    windows: list[tuple[str, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        payload = entry.get("payload", {})
        if entry.get("type") == "turn_context":
            model = payload["model"]
        elif payload.get("type") == "token_count" and payload.get("info"):
            windows.append((model, payload["info"]["model_context_window"]))
    return windows


def _record(
    stdout_name: str,
    rollout_name: str | None,
    *,
    session_id: str,
    expected_signals: Sequence[AgentSignal],
    exit_code: int = 0,
    stderr_name: str = _EMPTY_STDERR,
) -> RecordedStream:
    sessions = {} if rollout_name is None else {"rollout": _FIXTURES_DIR / rollout_name}
    return RecordedStream(
        name=stdout_name,
        stdout=_FIXTURES_DIR / stdout_name,
        stderr=_FIXTURES_DIR / stderr_name,
        exit_code=exit_code,
        expected_signals=expected_signals,
        session_id=session_id,
        session_logs=sessions,
    )


def _write_model_cache(home: Path, models: Sequence[tuple[str, int]]) -> None:
    payload = {
        "models": [
            {"slug": slug, "context_window": context_window} for slug, context_window in models
        ]
    }
    (home / MODELS_CACHE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_rollout_window(
    home: Path, session_id: str | None, context_window: int, model: str
) -> None:
    if session_id is None:
        message = "a resumed invocation needs a session id"
        raise AssertionError(message)
    session_dir = home / "sessions" / "2030" / "01" / "01"
    session_dir.mkdir(parents=True, exist_ok=True)
    rollout = session_dir / f"rollout-2030-01-01T00-00-00-{session_id}.jsonl"
    entry = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": 1, "output_tokens": 1},
                "last_token_usage": {"input_tokens": 1},
                "model_context_window": context_window,
            },
            "rate_limits": None,
        },
    }
    turn_context = {"type": "turn_context", "payload": {"model": model}}
    rollout.write_text(json.dumps(turn_context) + "\n" + json.dumps(entry) + "\n", encoding="utf-8")


def _replace_cwd(spec: TurnSpec, cwd: Path, suffix: str) -> TurnSpec:
    return TurnSpec(
        rung=spec.rung,
        role=spec.role,
        prompt=spec.prompt,
        cwd=cwd,
        run_dir=spec.run_dir,
        turn_dir=spec.turn_dir.parent / suffix,
        add_dirs=spec.add_dirs,
        env=spec.env,
        session_id=spec.session_id,
        usage=spec.usage,
    )


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
    if not usage:
        raise AssertionError(_STATUS_MESSAGE)
    return session.session_id, usage[-1]


def _failure_for(error: Mapping[str, object], session_id: str) -> TurnFailed:
    clock = FakeClock(_SAMPLE_NOW)
    parser = CodexTurnParser(
        session_id=None,
        initial_usage=None,
        home=Path(),
        clock=clock,
        reset_time=ResetTimeParser(clock),
    )
    parser.feed(json.dumps({"type": "thread.started", "thread_id": session_id}))
    parser.feed(
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": json.dumps(error)},
            }
        )
    )
    return next(signal for signal in parser.finish(1, "") if isinstance(signal, TurnFailed))


def _install_fake_codex(directory: Path) -> None:
    directory.mkdir()
    executable = directory / "codex"
    response = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps({"ok": True})},
        }
    )
    turn_response = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "thread.started", "thread_id": _FAKE_TURN_SESSION_ID},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "fake turn complete"},
            },
            {"type": "turn.completed"},
        )
    )
    executable.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" "$CODEX_HOME" "$OPENAI_API_KEY" '
        f'"$CODEX_API_KEY" "$OPENROUTER_API_KEY" >> "${_CODEX_ENV_CAPTURE}"\n'
        'if [ "$1" = "--version" ]; then\n'
        "  printf '%s\\n' 'codex-cli 0.156.1'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "sandbox" ]; then\n'
        "  shift\n"
        '  while [ "$1" != "--" ]; do shift; done\n'
        "  shift\n"
        '  exec "$@"\n'
        "fi\n"
        'if [ "$1" = "login" ]; then\n'
        "  printf '%s\\n' 'Not logged in'\n"
        "  exit 1\n"
        "fi\n"
        'if [ "$1" = "debug" ] && [ "$2" = "models" ] && [ "$3" = "--bundled" ]; then\n'
        '  catalog="${SHIKIRU_CODEX_BUNDLED_MODELS_JSON}"\n'
        '  if [ -z "$catalog" ]; then catalog=\'{"models":[]}\'; fi\n'
        '  printf "%s\\n" "$catalog"\n'
        '  exit "${SHIKIRU_CODEX_BUNDLED_MODELS_EXIT_CODE:-0}"\n'
        "fi\n"
        'if [ "$1" = "exec" ] && [ "$2" != "--ephemeral" ]; then\n'
        "  cat <<'SHIKIRU_TURN_EVENTS'\n"
        f"{turn_response}\n"
        "SHIKIRU_TURN_EVENTS\n"
        "  exit 0\n"
        "fi\n"
        f"printf '%s\\n' '{response}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


async def _init_live_repo(path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await SubprocessRunner().run(["git", "init"], timeout=30, cwd=path, check=True)


@contextmanager
def _codex_home_environment(home: Path):
    previous = os.environ.get(CODEX_HOME_ENV_VAR)
    os.environ[CODEX_HOME_ENV_VAR] = str(home)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(CODEX_HOME_ENV_VAR, None)
        else:
            os.environ[CODEX_HOME_ENV_VAR] = previous


def _rollout_file(spec: TurnSpec) -> Path:
    return (
        Path(spec.env[CODEX_HOME_ENV_VAR])
        / "sessions"
        / "2026"
        / "09"
        / "26"
        / f"rollout-2026-09-26T00-00-00-{spec.session_id}.jsonl"
    )


def _resume_argv(spec: TurnSpec) -> tuple[str, ...]:
    if spec.session_id is None:
        message = "compaction scenarios resume a recorded session"
        raise AssertionError(message)
    return (
        "codex",
        "exec",
        RESUME_SUBCOMMAND,
        spec.session_id,
        "--json",
        MODEL_FLAG,
        _MODEL,
        CONFIG_FLAG,
        f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
        CONFIG_FLAG,
        f"{AUTO_COMPACT_LIMIT_CONFIG}=80000",
        *_sandbox_args(spec),
        spec.prompt,
    )
