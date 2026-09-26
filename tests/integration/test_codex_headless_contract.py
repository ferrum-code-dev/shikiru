"""Exercise Codex's process-per-turn contract with recorded JSONL and live checks."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from shikiru.agents.contracts import (
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
from shikiru.agents.sdk.turn import TurnProcessResult
from shikiru.agents.testing import AgentContractSuite, InvocationCase, RecordedStream
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
from shikiru.plugins.agents.codex.options import (
    ADD_DIR_FLAG,
    CODEX_HOME_ENV_VAR,
    CONFIG_FLAG,
    MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG,
    MODEL_FLAG,
    MODELS_CACHE_FILENAME,
    REASONING_EFFORT_CONFIG,
    RESUME_SUBCOMMAND,
    SANDBOX_FLAG,
    WORKSPACE_WRITE_SANDBOX,
)
from shikiru.plugins.agents.codex.turn import CodexTurnParser, CodexTurnSequence
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "codex"
_ROLE = "implementer"
_MODEL = "gpt-5.6-sol"
_OTHER_MODEL = "gpt-5.6-terra"
_EFFORT = "low"
_TEST_CONTEXT_WINDOW = 100_000
_ROLLOUT_CONTEXT_WINDOW = 250_000
_COMPACT_AT_PCT = 80.0
_LOW_LIVE_COMPACT_AT_PCT = 1.0
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
_COMPACTION_ROLLOUT = "rollout_compaction.jsonl"
_SAMPLE_NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
_SESSION_FIRST = "session-first"
_SESSION_RESUMED = "session-resume_model_change"
_SESSION_OPENROUTER_LIVE = "session-openrouter-live"
_CONTEXT_WINDOW = 258_400
_USAGE_FIRST = Usage(55_675, 330, 52_096, 0, None, "unknown", 5.506578947368421)
_USAGE_RESUMED = Usage(129_393, 608, 105_344, 0, None, "unknown", 7.201238390092879)
_OPENROUTER_LIVE_CONTEXT_WINDOW = 997_500
_OPENROUTER_LIVE_INPUT_TOKENS = 12_340
_OPENROUTER_LIVE_OUTPUT_TOKENS = 5
_OPENROUTER_LIVE_CACHE_WRITE_TOKENS = 12_272
_OPENROUTER_LIVE_MESSAGE = "OK"
_CODEX_ENV_CAPTURE = "SHIKIRU_CODEX_ENV_CAPTURE"
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

    async def _run_turn(self, spec: TurnSpec) -> list[AgentSignal]:
        turn = await self._build_agent().start_turn(spec)
        async with asyncio.timeout(self._live_turn_timeout_seconds):
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
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        home = tmp_path / "codex-home"
        self._prepare_home(home, repo)
        turn_name = "01-turn" if resume_session_id is None else "02-turn"
        return TurnSpec(
            rung=Rung(agent="codex", model=model or _MODEL, effort=_EFFORT),
            role=_ROLE,
            prompt="complete the requested turn",
            cwd=repo,
            run_dir=run_dir,
            turn_dir=tmp_path / "turns" / turn_name,
            add_dirs=(),
            env={CODEX_HOME_ENV_VAR: str(home)},
            session_id=resume_session_id,
            usage=usage,
        )

    def parser_for(self, sample: RecordedStream) -> CodexTurnParser:
        clock = FakeClock(_SAMPLE_NOW)
        return CodexTurnParser(
            session_id=sample.session_id,
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
                "turn_openrouter_live_redacted.jsonl",
                "rollout_openrouter_live_redacted.jsonl",
                session_id=_SESSION_OPENROUTER_LIVE,
                expected_signals=_OPENROUTER_LIVE_SIGNALS,
                stderr_name="turn_openrouter_live_redacted.stderr.log",
            ),
        ]

    def error_streams(self) -> Sequence[RecordedStream]:
        return []

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        new_spec = self.build_spec(tmp_path / "new", resume_session_id=None)
        (new_spec.cwd / ".git").mkdir()
        new_common = str(new_spec.cwd / ".git")
        new_argv = (
            "codex",
            "exec",
            "--json",
            MODEL_FLAG,
            _MODEL,
            CONFIG_FLAG,
            f'{REASONING_EFFORT_CONFIG}="{_EFFORT}"',
            CONFIG_FLAG,
            f"{MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG}=80000",
            SANDBOX_FLAG,
            WORKSPACE_WRITE_SANDBOX,
            ADD_DIR_FLAG,
            str(new_spec.run_dir),
            ADD_DIR_FLAG,
            new_common,
            new_spec.prompt,
        )
        resumed = self.build_spec(
            tmp_path / "resume",
            resume_session_id="session-resumed",
            model=_OTHER_MODEL,
        )
        resumed_home = Path(resumed.env[CODEX_HOME_ENV_VAR])
        _write_rollout_window(resumed_home, resumed.session_id, _ROLLOUT_CONTEXT_WINDOW)
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
            f"{MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG}=200000",
            resumed.prompt,
        )
        provider_spec = self.build_spec(
            tmp_path / "provider",
            resume_session_id=None,
        )
        (provider_spec.cwd / ".git").mkdir()
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
            InvocationCase(
                name="new turns use workspace-write and grant the run and git common directories",
                options={"compact_at_pct": _COMPACT_AT_PCT},
                spec=new_spec,
                expected_argv=[new_argv],
                expected_env=[{CODEX_HOME_ENV_VAR: new_spec.env[CODEX_HOME_ENV_VAR]}],
            ),
            InvocationCase(
                name="resumed model changes use the latest rollout context window",
                options={"compact_at_pct": _COMPACT_AT_PCT},
                spec=resumed,
                expected_argv=[resumed_argv],
                expected_env=[{CODEX_HOME_ENV_VAR: resumed.env[CODEX_HOME_ENV_VAR]}],
                interactive_session_id="session-resumed",
                expected_interactive_command=("codex", "resume", "session-resumed"),
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
                        f"{MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG}=80000",
                        SANDBOX_FLAG,
                        WORKSPACE_WRITE_SANDBOX,
                        ADD_DIR_FLAG,
                        str(provider_spec.run_dir),
                        ADD_DIR_FLAG,
                        str(provider_spec.cwd / ".git"),
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
        return cast("CodexAgent", agent).process_sequence(spec)

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def headless_ask_model(self) -> str:
        return _MODEL

    def headless_ask_prompt(self) -> str:
        return _LIVE_ASK_PROMPT

    def test_turn_inherits_operator_environment_and_uses_turn_codex_home(
        self, tmp_path: Path, operator_codex_home: Path
    ) -> None:
        spec = self.build_spec(tmp_path, resume_session_id=None)
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        command = agent.process_sequence(spec, git_common_dir=tmp_path / ".git").start(spec)

        assert command.env[CODEX_HOME_ENV_VAR] == spec.env[CODEX_HOME_ENV_VAR]
        assert command.env[CODEX_HOME_ENV_VAR] != str(operator_codex_home)
        assert command.env["OPENAI_API_KEY"] == _SYNTHETIC_OPENAI_KEY
        assert command.env["CODEX_API_KEY"] == _SYNTHETIC_CODEX_KEY
        assert command.env["OPENROUTER_API_KEY"] == _SYNTHETIC_PROVIDER_KEY
        assert f"{MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG}=80000" in command.argv

    def test_context_error_json_compacts_and_retries_once_on_the_same_session(
        self, tmp_path: Path
    ) -> None:
        spec = self.build_spec(tmp_path, resume_session_id=None)
        (spec.cwd / ".git").mkdir()
        agent = create(
            AgentContext(
                options={"compact_at_pct": _COMPACT_AT_PCT},
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )
        sequence = agent.process_sequence(spec)
        first = sequence.start(spec)
        session_id = "session-context-failure"
        parser = CodexTurnParser(
            session_id=None,
            initial_usage=None,
            home=Path(spec.env[CODEX_HOME_ENV_VAR]),
            clock=SystemClock(),
            reset_time=ResetTimeParser(SystemClock()),
        )
        signals = parser.feed(json.dumps({"type": "thread.started", "thread_id": session_id}))
        parser.feed(
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {
                        "message": json.dumps(
                            {
                                "status": 400,
                                "code": "context_length_exceeded",
                                "message": "context length exceeded",
                            }
                        )
                    },
                }
            )
        )
        signals.extend(parser.finish(1, ""))
        second = sequence.advance(spec, TurnProcessResult(tuple(signals), 1, ""))
        assert first.argv[:2] == ("codex", "exec")
        assert second is not None
        assert second.argv[:4] == ("codex", "exec", RESUME_SUBCOMMAND, session_id)
        assert f"{MODEL_AUTO_COMPACT_TOKEN_LIMIT_CONFIG}=80000" in second.argv
        terminal = TurnFailed(FailureKind.CONTEXT, "context length exceeded", None)
        assert (
            sequence.advance(
                spec,
                TurnProcessResult((SessionStarted(session_id), terminal), 1, ""),
            )
            is None
        )

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
                TurnFailed(FailureKind.CONTEXT, "prompt exceeds context window", None),
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
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
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
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        monkeypatch.setenv(_CODEX_ENV_CAPTURE, str(capture_path))
        agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

        checks = await agent.doctor()

        login = next(check for check in checks if check.name == "login")
        assert all(check.ok for check in checks)
        assert "not verified" in login.detail
        assert capture_path.read_text().splitlines() == [
            "--version",
            str(operator_codex_home),
            _SYNTHETIC_OPENAI_KEY,
            _SYNTHETIC_CODEX_KEY,
            _SYNTHETIC_PROVIDER_KEY,
            "--version",
            str(operator_codex_home),
            _SYNTHETIC_OPENAI_KEY,
            _SYNTHETIC_CODEX_KEY,
            _SYNTHETIC_PROVIDER_KEY,
        ]

    @pytest.mark.asyncio
    async def test_offline_compaction_replays_and_retries_once(self, tmp_path: Path) -> None:
        rollout = tmp_path / "rollout-session-compacted.jsonl"
        compaction_entries = (
            (_FIXTURES_DIR / _COMPACTION_ROLLOUT).read_text(encoding="utf-8").splitlines()
        )
        rollout.write_text(compaction_entries[0] + "\n", encoding="utf-8")
        clock = FakeClock(_SAMPLE_NOW)
        parser = CodexTurnParser(
            session_id="session-compacted",
            initial_usage=Usage(13_824, 0, 0, 0, None, "unknown", 5.35),
            home=tmp_path,
            rollout_path=rollout,
            clock=clock,
            reset_time=ResetTimeParser(clock),
        )
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(compaction_entries[1:]) + "\n")
        parser.feed(json.dumps({"type": "thread.started", "thread_id": "session-compacted"}))
        parser.feed(json.dumps({"type": "turn.completed", "usage": {}}))

        signals = parser.finish(0, "")

        assert Compacted(13_824, 0) in signals
        usage = next(signal.usage for signal in signals if isinstance(signal, UsageReported))
        assert usage.context_used_pct is not None
        assert usage.context_used_pct == 0.0
        assert usage.context_used_pct < 5.35
        assert isinstance(signals[-1], TurnEnded)

    def test_unknown_session_sets_rollout_compaction_baseline_on_start(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "codex-home"
        sessions = home / "sessions" / "2030" / "01" / "01"
        sessions.mkdir(parents=True)
        rollout = sessions / "rollout-2030-01-01T00-00-00-session-unknown.jsonl"
        compaction_entry = (
            (_FIXTURES_DIR / _COMPACTION_ROLLOUT).read_text(encoding="utf-8").splitlines()[1]
        )
        rollout.write_text(compaction_entry + "\n", encoding="utf-8")
        clock = FakeClock(_SAMPLE_NOW)
        parser = CodexTurnParser(
            session_id=None,
            initial_usage=None,
            home=home,
            clock=clock,
            reset_time=ResetTimeParser(clock),
        )
        parser.feed(json.dumps({"type": "thread.started", "thread_id": "session-unknown"}))
        parser.feed(json.dumps({"type": "turn.completed", "usage": {}}))

        signals = parser.finish(0, "")

        assert not any(isinstance(signal, Compacted) for signal in signals)

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

    def _build_agent(self) -> Agent:
        with _codex_home_environment(self._active_home):
            return super()._build_agent()

    def _prepare_home(self, home: Path, repo: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        if self._live:
            source_home = Path(os.environ.get(CODEX_HOME_ENV_VAR, Path.home() / ".codex"))
            source_auth = source_home / "auth.json"
            if not source_auth.is_file():
                message = "authenticated Codex credentials are required for live checks"
                raise AssertionError(message)
            shutil.copyfile(source_auth, home / "auth.json")
            (home / "auth.json").chmod(0o600)
            source_cache = source_home / MODELS_CACHE_FILENAME
            if source_cache.is_file():
                shutil.copyfile(source_cache, home / MODELS_CACHE_FILENAME)
        else:
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
        checks = await self._build_agent().doctor()
        assert all(check.ok for check in checks)

    @pytest.mark.asyncio
    async def test_live_worktree_commits_survive_exec_resume(self, tmp_path: Path) -> None:
        self._require_live()
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
        self._trust_project(self._active_home, worktree)
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
            model=_OTHER_MODEL,
        )
        self._trust_project(self._active_home, worktree)
        resumed = _replace_cwd(resumed, worktree, "second-turn")
        await self._run_turn(_with_prompt(resumed, _LIVE_SECOND_COMMIT_PROMPT))
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
        first = self.build_spec(tmp_path, resume_session_id=None)
        await _init_live_repo(first.cwd)
        nonce = uuid4().hex
        session_id, usage = _latest_usage(
            await self._run_turn(_with_prompt(first, RESUME_NONCE_PROMPT.format(nonce=nonce)))
        )
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=session_id,
            usage=usage,
            model=_OTHER_MODEL,
        )

        signals = await self._run_turn(_with_prompt(resumed, RESUME_RECALL_PROMPT))

        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        assert nonce in message

    @pytest.mark.asyncio
    async def test_live_low_compaction_threshold_preserves_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        first = self.build_spec(tmp_path, resume_session_id=None)
        await _init_live_repo(first.cwd)
        nonce = uuid4().hex
        started, usage = _latest_usage(
            await self._run_turn(
                _with_prompt(first, f"Remember this nonce for later in this session: {nonce}")
            )
        )
        resumed = self.build_spec(tmp_path, resume_session_id=started, usage=usage)
        low_agent = create(
            AgentContext(
                options={"compact_at_pct": _LOW_LIVE_COMPACT_AT_PCT},
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )

        signals = await _run_with_agent(low_agent, _with_prompt(resumed, RESUME_RECALL_PROMPT))

        assert any(isinstance(signal, Compacted) for signal in signals)
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        assert nonce in message

    def _trust_project(self, home: Path, project: Path) -> None:
        project_json = json.dumps(str(project), ensure_ascii=False)
        with (home / "config.toml").open("a", encoding="utf-8") as handle:
            handle.write(f'\n[projects.{project_json}]\ntrust_level = "trusted"\n')


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


def _write_rollout_window(home: Path, session_id: str | None, context_window: int) -> None:
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
    rollout.write_text(json.dumps(entry) + "\n", encoding="utf-8")


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
    executable.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" "$CODEX_HOME" "$OPENAI_API_KEY" '
        f'"$CODEX_API_KEY" "$OPENROUTER_API_KEY" >> "${_CODEX_ENV_CAPTURE}"\n'
        'if [ "$1" = "--version" ]; then\n'
        "  printf '%s\\n' 'codex-cli 0.156.1'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "login" ]; then\n'
        "  printf '%s\\n' 'Not logged in'\n"
        "  exit 1\n"
        "fi\n"
        f"printf '%s\\n' '{response}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


async def _run_with_agent(agent: Agent, spec: TurnSpec) -> list[AgentSignal]:
    turn = await agent.start_turn(spec)
    async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
        return [signal async for signal in turn.signals()]


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
