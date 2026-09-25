"""Exercise Claude Code's headless contract with stream-json fixture scenarios."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from shikiru.agents.contracts import (
    Agent,
    AgentContext,
    AgentFactory,
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
    shikiru_command,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.testing import (
    RESUME_NONCE_PROMPT,
    RESUME_RECALL_PROMPT,
    AgentContractSuite,
    InvocationCase,
    RecordedStream,
)
from shikiru.clock import SystemClock
from shikiru.domain.recovery import Escalate, Failed, RecoveryPolicy, TurnCounters, decide
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    schema_for,
)
from shikiru.domain.tasks import EscalationReason
from shikiru.plugins.agents.claude.agent import ClaudeAgent, create
from shikiru.plugins.agents.claude.options import DEFAULT_ARGS
from shikiru.plugins.agents.claude.turn import ClaudeTurnParser
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.agents.sdk.turn import TurnParser, TurnProcessSequence

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "claude"
_ROLE = "implementer"
_MODEL = "claude-haiku-4-5-20251001"
_OTHER_MODEL = "claude-sonnet-4-5-20250929"
_OPENROUTER_MODEL = "anthropic/claude-haiku-4-5"
_OPENROUTER_LIVE_SESSION_ID = "session-openrouter-live-redacted"
_OPENROUTER_LIVE_RESPONSE = "[redacted live response]"
_OPENROUTER_REPORT_LIVE_SESSION_ID = "session-openrouter-report-live-redacted"
_OPENROUTER_REPORT_LIVE_RESPONSE = "[redacted live report response]"
_SESSION_RESUMED = "session-resumed"
_LIVE_MODEL_ENV = "SHIKIRU_CLAUDE_LIVE_MODEL"
_LIVE_CHANGE_MODEL_ENV = "SHIKIRU_CLAUDE_LIVE_CHANGE_MODEL"
_CLAUDE_HOME_DIRNAME = "claude-home"
_CLAUDE_CONFIG_DIRNAME = "claude-config"
_SAMPLE_NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
_COMPACTION_THRESHOLD = 80.0
_LOW_COMPACTION_THRESHOLD = 0.0
_RESET_FIVE_HOUR = datetime(2030, 1, 1, 1, tzinfo=UTC)
_RESET_SEVEN_DAY = datetime(2030, 1, 5, tzinfo=UTC)
_COST = 0.00001
_LIVE_ASK_PROMPT = "Return the JSON object with ok set to true."
_BASIC_LIVE_PROMPT = "Reply with exactly OK and do nothing else."
_LIVE_REPO_INIT_TIMEOUT_SECONDS = 30.0


class _AskResult(BaseModel):
    ok: bool


class TestClaudeHeadlessContract(AgentContractSuite):
    """Exercise Claude's stream scenarios and the six optional live checks."""

    runs_live_checks_in_default_gate = False

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> Mapping[str, object]:
        return {"compact_at_pct": _LOW_COMPACTION_THRESHOLD}

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
        turn_dir = tmp_path / "turns" / ("01-turn" if resume_session_id is None else "02-turn")
        return TurnSpec(
            rung=Rung(
                agent="claude",
                model=model or os.environ.get(_LIVE_MODEL_ENV) or _MODEL,
                effort="low",
            ),
            role=_ROLE,
            prompt=_BASIC_LIVE_PROMPT,
            cwd=repo,
            run_dir=run_dir,
            turn_dir=turn_dir,
            add_dirs=(),
            env={"SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE},
            session_id=resume_session_id,
            usage=usage,
        )

    def _live_report_environment(self, spec: TurnSpec) -> Mapping[str, str]:
        home_dir = spec.cwd.parent / _CLAUDE_HOME_DIRNAME
        config_dir = spec.cwd.parent / _CLAUDE_CONFIG_DIRNAME
        home_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        return {
            **spec.env,
            "HOME": str(home_dir),
            "CLAUDE_CONFIG_DIR": str(config_dir),
        }

    @pytest.mark.asyncio
    async def test_live_report_payload_is_confined_to_scratch_inputs(self, tmp_path: Path) -> None:
        spec = await self._prepare_live_report_spec(
            self.build_spec(tmp_path, resume_session_id=None)
        )
        project_root = Path.cwd().resolve()
        prompt_path = Path(spec.prompt)
        report_input = spec.run_dir / "contract-report-input.json"
        role_dir = spec.run_dir / spec.role
        turn_link = role_dir / CURRENT_TURN_LINK_NAME
        schema_path = spec.turn_dir / REPORT_SCHEMA_FILENAME
        isolated_home = tmp_path / "claude-home"
        isolated_config = tmp_path / "claude-config"

        assert project_root not in spec.cwd.resolve().parents
        assert project_root not in spec.run_dir.resolve().parents
        assert project_root not in isolated_home.resolve().parents
        assert project_root not in isolated_config.resolve().parents
        assert spec.add_dirs == ()
        assert not any(isolated_home.iterdir())
        assert not any(isolated_config.iterdir())
        assert {path.name for path in spec.cwd.iterdir()} == {".git", prompt_path.name}
        assert {path.name for path in spec.run_dir.iterdir()} == {report_input.name, spec.role}
        assert {path.name for path in role_dir.iterdir()} == {CURRENT_TURN_LINK_NAME}
        assert turn_link.is_symlink()
        assert turn_link.resolve() == spec.turn_dir.resolve()
        assert {path.name for path in spec.turn_dir.iterdir()} == {
            REPORT_KIND_FILENAME,
            REPORT_SCHEMA_FILENAME,
        }
        report_kind = await asyncio.to_thread((spec.turn_dir / REPORT_KIND_FILENAME).read_text)
        schema_json = await asyncio.to_thread(schema_path.read_text)
        report_input_json = await asyncio.to_thread(report_input.read_text)
        prompt = await asyncio.to_thread(prompt_path.read_text)
        assert report_kind == ReportKind.IMPLEMENTATION
        assert json.loads(schema_json) == schema_for(ReportKind.IMPLEMENTATION)
        assert json.loads(report_input_json) == {
            "status": "completed",
            "summary": "Isolated live report contract.",
            "commits": [],
            "tests": [],
            "trackers_updated": False,
            "risks": [],
        }
        assert prompt == (
            "Use Bash to run exactly this report command once:\n"
            f"{shikiru_command('report', '--file')} "
            '"$SHIKIRU_RUN_DIR/contract-report-input.json"\n'
            "Do not inspect or edit other files or run any other commands. "
            "After the command succeeds, reply exactly REPORT_OK."
        )

        command = self.process_sequence(self._build_agent(), spec).start(spec)
        add_dirs = [
            Path(command.argv[index + 1])
            for index, argument in enumerate(command.argv[:-1])
            if argument == "--add-dir"
        ]
        assert command.cwd == spec.cwd
        assert command.argv[:3] == ("claude", "-p", str(prompt_path))
        assert add_dirs == [spec.run_dir]
        assert command.env["HOME"] == str(isolated_home)
        assert command.env["CLAUDE_CONFIG_DIR"] == str(isolated_config)
        assert command.env["SHIKIRU_RUN_DIR"] == str(spec.run_dir)
        assert command.env["SHIKIRU_ROLE"] == spec.role
        for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            assert command.env.get(name) == os.environ.get(name)

    def parser_for(self, sample: RecordedStream) -> TurnParser:
        model = _OPENROUTER_MODEL if "openrouter" in sample.name else _MODEL
        return ClaudeTurnParser(
            session_id=sample.session_id or "missing-session",
            model=model,
            initial_usage=None,
            clock=FakeClock(_SAMPLE_NOW),
            reset_time=ResetTimeParser(FakeClock(_SAMPLE_NOW)),
            compact_process="compact_process" in sample.name,
        )

    def recorded_streams(self) -> Sequence[RecordedStream]:
        subscription_usage = Usage(100, 5, 12, 3, _COST, "api_equivalent", 115 / 200000 * 100)
        list_cost_usage = Usage(40, 8, 4, 1, 0.00002, "api_equivalent", 0.0225)
        openrouter_usage = Usage(70, 12, 0, 0, None, "unknown", None)
        openrouter_report_usage = Usage(2509, 238, 19865, 10067, None, "unknown", None)
        compact_usage = Usage(20, 8, 0, 0, 0.00003, "api_equivalent", 0.01)
        empty_stderr = _FIXTURES_DIR / "turn_rate_limit.stderr.log"
        return [
            RecordedStream(
                name="turn_subscription.jsonl",
                stdout=_FIXTURES_DIR / "turn_subscription.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted("session-subscription"),
                    ToolStarted("Read", '{"file_path":"README.md"}'),
                    ToolFinished("Read"),
                    Message("Ready."),
                    QuotaReported(
                        (
                            QuotaWindow("five_hour", 12.5, _RESET_FIVE_HOUR, _SAMPLE_NOW),
                            QuotaWindow("seven_day", 4, _RESET_SEVEN_DAY, _SAMPLE_NOW),
                        )
                    ),
                    UsageReported(subscription_usage),
                    TurnEnded("Ready."),
                ],
                session_id="session-subscription",
            ),
            RecordedStream(
                name="turn_list_cost.jsonl",
                stdout=_FIXTURES_DIR / "turn_list_cost.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted("session-list-cost"),
                    Message("Provider list-cost response."),
                    UsageReported(list_cost_usage),
                    TurnEnded("Provider list-cost response."),
                ],
                session_id="session-list-cost",
            ),
            RecordedStream(
                name="turn_openrouter.jsonl",
                stdout=_FIXTURES_DIR / "turn_openrouter.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted("session-openrouter"),
                    Message("OpenRouter response."),
                    UsageReported(openrouter_usage),
                    TurnEnded("OpenRouter response."),
                ],
                session_id="session-openrouter",
            ),
            RecordedStream(
                name="turn_openrouter_live_redacted.jsonl",
                stdout=_FIXTURES_DIR / "turn_openrouter_live_redacted.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted(_OPENROUTER_LIVE_SESSION_ID),
                    Message(_OPENROUTER_LIVE_RESPONSE),
                    UsageReported(Usage(17094, 2, 0, 0, None, "unknown", None)),
                    TurnEnded(_OPENROUTER_LIVE_RESPONSE),
                ],
                session_id=_OPENROUTER_LIVE_SESSION_ID,
            ),
            RecordedStream(
                name="turn_openrouter_report_live_redacted.jsonl",
                stdout=_FIXTURES_DIR / "turn_openrouter_report_live_redacted.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted(_OPENROUTER_REPORT_LIVE_SESSION_ID),
                    Message(_OPENROUTER_REPORT_LIVE_RESPONSE),
                    ToolStarted("Read", '{"file_path":"[redacted scratch prompt path]"}'),
                    ToolFinished("Read"),
                    Message(_OPENROUTER_REPORT_LIVE_RESPONSE),
                    ToolStarted("Bash", '{"command":"[redacted shikiru report command]"}'),
                    ToolFinished("Bash"),
                    Message(_OPENROUTER_REPORT_LIVE_RESPONSE),
                    UsageReported(openrouter_report_usage),
                    TurnEnded(_OPENROUTER_REPORT_LIVE_RESPONSE),
                ],
                session_id=_OPENROUTER_REPORT_LIVE_SESSION_ID,
            ),
            RecordedStream(
                name="turn_compact_process.jsonl",
                stdout=_FIXTURES_DIR / "turn_compact_process.jsonl",
                stderr=empty_stderr,
                exit_code=0,
                expected_signals=[
                    SessionStarted(_SESSION_RESUMED),
                    Compacted(12000, 1100),
                    Message("Context compacted."),
                    UsageReported(compact_usage),
                    TurnEnded("Context compacted."),
                ],
                session_id=_SESSION_RESUMED,
            ),
        ]

    def error_streams(self) -> Sequence[RecordedStream]:
        empty_stderr = _FIXTURES_DIR / "turn_rate_limit.stderr.log"
        return [
            RecordedStream(
                name="turn_rate_limit.jsonl",
                stdout=_FIXTURES_DIR / "turn_rate_limit.jsonl",
                stderr=empty_stderr,
                exit_code=1,
                expected_signals=[
                    SessionStarted("session-rate-limit"),
                    QuotaReported(
                        (
                            QuotaWindow("five_hour", 100, _RESET_FIVE_HOUR, _SAMPLE_NOW),
                            QuotaWindow("seven_day", 22, _RESET_SEVEN_DAY, _SAMPLE_NOW),
                        )
                    ),
                    TurnFailed(
                        FailureKind.QUOTA,
                        "You've hit your usage limit",
                        _RESET_FIVE_HOUR,
                    ),
                ],
                session_id="session-rate-limit",
            ),
            RecordedStream(
                name="turn_credit_exhausted.jsonl",
                stdout=_FIXTURES_DIR / "turn_credit_exhausted.jsonl",
                stderr=empty_stderr,
                exit_code=1,
                expected_signals=[
                    SessionStarted("session-credit-exhausted"),
                    TurnFailed(FailureKind.AUTH, "API credits are exhausted", None),
                ],
                session_id="session-credit-exhausted",
            ),
            RecordedStream(
                name="turn_unrecognized_model.jsonl",
                stdout=_FIXTURES_DIR / "turn_unrecognized_model.jsonl",
                stderr=_FIXTURES_DIR / "turn_unrecognized_model.stderr.log",
                exit_code=1,
                expected_signals=[
                    SessionStarted("session-unrecognized-model"),
                    TurnFailed(
                        FailureKind.UNAVAILABLE,
                        "Unknown model: not-a-model; unrecognized_model",
                        None,
                    ),
                ],
                session_id="session-unrecognized-model",
            ),
            RecordedStream(
                name="turn_context_failure.jsonl",
                stdout=_FIXTURES_DIR / "turn_context_failure.jsonl",
                stderr=_FIXTURES_DIR / "turn_context_failure.stderr.log",
                exit_code=1,
                expected_signals=[
                    SessionStarted(_SESSION_RESUMED),
                    TurnFailed(
                        FailureKind.CONTEXT,
                        "Prompt is too long for the context window",
                        None,
                    ),
                ],
                session_id=_SESSION_RESUMED,
            ),
            RecordedStream(
                name="turn_unknown_error.jsonl",
                stdout=_FIXTURES_DIR / "turn_unknown_error.jsonl",
                stderr=_FIXTURES_DIR / "turn_unknown_error.stderr.log",
                exit_code=1,
                expected_signals=[
                    SessionStarted("session-unknown"),
                    TurnFailed(
                        FailureKind.UNKNOWN,
                        "Claude reported an unclassified failure",
                        None,
                    ),
                ],
                session_id="session-unknown",
            ),
        ]

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = tmp_path / "run"
        extra_dir = tmp_path / "additional"
        resumed = TurnSpec(
            rung=Rung("claude", _MODEL, "low"),
            role=_ROLE,
            prompt="resume with the changed model",
            cwd=repo,
            run_dir=run_dir,
            turn_dir=tmp_path / "turns" / "resume",
            add_dirs=(extra_dir,),
            env={"SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE},
            session_id=_SESSION_RESUMED,
            usage=None,
        )
        changed_model = replace(resumed, rung=Rung("claude", _OTHER_MODEL, "high"))
        operator_access_spec = replace(
            resumed,
            env={
                **resumed.env,
                "ANTHROPIC_API_KEY": "synthetic-api-key",
                "ANTHROPIC_AUTH_TOKEN": "synthetic-auth-token",
                "ANTHROPIC_BASE_URL": "https://provider.invalid",
            },
        )
        compact_usage = Usage(1000, 500, 100, 20, None, "unknown", 85.0)
        compact_spec = replace(resumed, usage=compact_usage)
        retry_spec = replace(resumed, usage=None)
        run_dir_arg = str(run_dir)
        extra_dir_arg = str(extra_dir)
        common_tail = (
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            _MODEL,
            "--effort",
            "low",
            "--add-dir",
            run_dir_arg,
            "--add-dir",
            extra_dir_arg,
            *DEFAULT_ARGS,
        )
        context_failure = _record(
            "turn_context_failure.jsonl",
            "turn_context_failure.stderr.log",
            _SESSION_RESUMED,
        )
        compact_process = _record(
            "turn_compact_process.jsonl",
            "turn_rate_limit.stderr.log",
            _SESSION_RESUMED,
        )
        retry_success = _record(
            "turn_subscription.jsonl",
            "turn_rate_limit.stderr.log",
            "session-subscription",
        )
        return [
            InvocationCase(
                name="resumes the existing session with the selected model and effort",
                options={},
                spec=resumed,
                expected_argv=[
                    (
                        "claude",
                        "-p",
                        resumed.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    )
                ],
                expected_env=[{"SHIKIRU_ROLE": _ROLE}],
                interactive_session_id=_SESSION_RESUMED,
                expected_interactive_command=(
                    "claude",
                    "--resume",
                    _SESSION_RESUMED,
                    "--add-dir",
                    run_dir_arg,
                    *DEFAULT_ARGS,
                ),
            ),
            InvocationCase(
                name="a model change resumes the same session",
                options={},
                spec=changed_model,
                expected_argv=[
                    (
                        "claude",
                        "-p",
                        changed_model.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        "--output-format",
                        "stream-json",
                        "--verbose",
                        "--model",
                        _OTHER_MODEL,
                        "--effort",
                        "high",
                        "--add-dir",
                        run_dir_arg,
                        "--add-dir",
                        extra_dir_arg,
                        *DEFAULT_ARGS,
                    )
                ],
                expected_env=[{"SHIKIRU_ROLE": _ROLE}],
            ),
            InvocationCase(
                name="turn preserves operator provider environment",
                options={"args": []},
                spec=operator_access_spec,
                expected_argv=[
                    (
                        "claude",
                        "-p",
                        operator_access_spec.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        "--output-format",
                        "stream-json",
                        "--verbose",
                        "--model",
                        _MODEL,
                        "--effort",
                        "low",
                        "--add-dir",
                        run_dir_arg,
                        "--add-dir",
                        extra_dir_arg,
                    )
                ],
                expected_env=[
                    {
                        "ANTHROPIC_API_KEY": "synthetic-api-key",
                        "ANTHROPIC_AUTH_TOKEN": "synthetic-auth-token",
                        "ANTHROPIC_BASE_URL": "https://provider.invalid",
                    }
                ],
            ),
            InvocationCase(
                name="pre-turn compaction runs before a resumed prompt at the threshold",
                options={"compact_at_pct": _COMPACTION_THRESHOLD},
                spec=compact_spec,
                expected_argv=[
                    (
                        "claude",
                        "-p",
                        "/compact",
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    ),
                    (
                        "claude",
                        "-p",
                        compact_spec.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    ),
                ],
                expected_env=[{"SHIKIRU_ROLE": _ROLE}, {"SHIKIRU_ROLE": _ROLE}],
                outcome_streams=[compact_process],
            ),
            InvocationCase(
                name="context failure compacts and reruns the prompt once",
                options={"compact_at_pct": _COMPACTION_THRESHOLD},
                spec=retry_spec,
                expected_argv=[
                    (
                        "claude",
                        "-p",
                        retry_spec.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    ),
                    (
                        "claude",
                        "-p",
                        "/compact",
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    ),
                    (
                        "claude",
                        "-p",
                        retry_spec.prompt,
                        "--resume",
                        _SESSION_RESUMED,
                        *common_tail,
                    ),
                ],
                expected_env=[
                    {"SHIKIRU_ROLE": _ROLE},
                    {"SHIKIRU_ROLE": _ROLE},
                    {"SHIKIRU_ROLE": _ROLE},
                ],
                outcome_streams=[context_failure, compact_process, retry_success],
                expected_final_signals=[
                    SessionStarted(_SESSION_RESUMED),
                    Compacted(12000, 1100),
                    UsageReported(Usage(20, 8, 0, 0, 0.00003, "api_equivalent", 0.01)),
                    TurnEnded("Ready."),
                ],
            ),
        ]

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> TurnProcessSequence:
        return cast("ClaudeAgent", agent).process_sequence(spec)

    @pytest.mark.asyncio
    async def test_live_model_change_resumes_and_recalls_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        changed_model = os.environ.get(_LIVE_CHANGE_MODEL_ENV)
        if not changed_model:
            pytest.skip("no configured alternate Claude model for the live check")
        if changed_model == (os.environ.get(_LIVE_MODEL_ENV) or _MODEL):
            pytest.skip("configured live Claude models are identical")
        first = self.build_spec(tmp_path, resume_session_id=None)
        await SubprocessRunner().run(
            ["git", "init"],
            timeout=_LIVE_REPO_INIT_TIMEOUT_SECONDS,
            cwd=first.cwd,
            check=True,
        )
        nonce = uuid.uuid4().hex
        first_signals = await self._run_turn(
            replace(first, prompt=RESUME_NONCE_PROMPT.format(nonce=nonce))
        )
        session_id = next(
            signal.session_id for signal in first_signals if isinstance(signal, SessionStarted)
        )
        usages = [signal.usage for signal in first_signals if isinstance(signal, UsageReported)]
        assert usages, "live turn reported no usage"
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=session_id,
            usage=usages[-1],
            model=changed_model,
        )
        signals = await self._run_turn(replace(resumed, prompt=RESUME_RECALL_PROMPT))
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        assert nonce in message, "resumed turn did not recall the nonce"

    @pytest.mark.asyncio
    async def test_live_low_compaction_threshold_preserves_session_memory(
        self, tmp_path: Path
    ) -> None:
        self._require_live()
        first = self.build_spec(tmp_path, resume_session_id=None)
        await SubprocessRunner().run(
            ["git", "init"],
            timeout=_LIVE_REPO_INIT_TIMEOUT_SECONDS,
            cwd=first.cwd,
            check=True,
        )
        nonce = uuid.uuid4().hex
        first_signals = await self._run_turn(
            replace(first, prompt=f"Remember this nonce for later in this session: {nonce}")
        )
        session_id = next(
            signal.session_id for signal in first_signals if isinstance(signal, SessionStarted)
        )
        usages = [signal.usage for signal in first_signals if isinstance(signal, UsageReported)]
        assert usages, "live turn reported no usage"
        if usages[-1].context_used_pct is None:
            pytest.skip("configured provider does not report a reliable context window")
        resumed = self.build_spec(
            tmp_path,
            resume_session_id=session_id,
            usage=usages[-1],
        )
        signals = await self._run_turn(replace(resumed, prompt=RESUME_RECALL_PROMPT))
        assert any(isinstance(signal, Compacted) for signal in signals), (
            "resumed turn did not compact"
        )
        message = "\n".join(signal.text for signal in signals if isinstance(signal, Message))
        assert nonce in message, "compacted resumed turn forgot the nonce"

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def headless_ask_model(self) -> str:
        return "haiku"

    def headless_ask_prompt(self) -> str:
        return _LIVE_ASK_PROMPT

    def test_new_turn_uses_a_uuid_and_starts_a_session(self, tmp_path: Path) -> None:
        spec = self.build_spec(tmp_path, resume_session_id=None)
        agent = create(
            AgentContext(
                options={},
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )
        command = agent.process_sequence(spec).start(spec)
        session_index = command.argv.index("--session-id")

        assert command.argv[:3] == ("claude", "-p", spec.prompt)
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            command.argv[session_index + 1],
        )
        assert command.argv[-2:] == DEFAULT_ARGS

    def test_init_without_a_session_id_does_not_hide_malformed_stream(self) -> None:
        clock = FakeClock(_SAMPLE_NOW)
        parser = ClaudeTurnParser(
            session_id="expected-session",
            model=_MODEL,
            initial_usage=None,
            clock=clock,
            reset_time=ResetTimeParser(clock),
        )

        assert parser.feed('{"type":"system","subtype":"init"}') == []

    @pytest.mark.parametrize("malformed_line", ["not-json", "[]"])
    def test_malformed_stream_before_success_remains_unknown(self, malformed_line: str) -> None:
        clock = FakeClock(_SAMPLE_NOW)
        parser = ClaudeTurnParser(
            session_id="expected-session",
            model=_MODEL,
            initial_usage=None,
            clock=clock,
            reset_time=ResetTimeParser(clock),
        )
        parser.feed('{"type":"system","subtype":"init","session_id":"expected-session"}')
        parser.feed(malformed_line)

        signals = parser.feed('{"type":"result","subtype":"success","result":"Should not succeed"}')

        assert signals == [
            TurnFailed(FailureKind.UNKNOWN, "Claude emitted malformed stream-json", None)
        ]

    def test_credit_exhaustion_escalates_without_quota_probe(self) -> None:
        sample = next(
            stream
            for stream in self.error_streams()
            if stream.name == "turn_credit_exhausted.jsonl"
        )
        parser = self.parser_for(sample)
        signals = [
            signal
            for line in sample.stdout.read_text().splitlines()
            for signal in parser.feed(line)
        ]
        signals.extend(parser.finish(sample.exit_code, sample.stderr.read_text()))
        failure = next(signal for signal in signals if isinstance(signal, TurnFailed))
        action = decide(
            Failed(failure.kind, failure.resets_at, failure.message),
            TurnCounters(),
            RecoveryPolicy(
                now=_SAMPLE_NOW,
                max_quota_wait=timedelta(hours=8),
                has_next_rung=False,
                has_next_rung_different_agent=False,
            ),
        )

        assert action == Escalate(EscalationReason.AGENT_AUTH)

    @pytest.mark.asyncio
    async def test_headless_ask_preserves_operator_provider_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executable_directory = tmp_path / "bin"
        executable_directory.mkdir()
        executable = executable_directory / "claude"
        capture_path = tmp_path / "provider-environment.txt"
        executable.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$ANTHROPIC_API_KEY" "$ANTHROPIC_AUTH_TOKEN" '
            '"$ANTHROPIC_BASE_URL" > "$SHIKIRU_TEST_ENV_CAPTURE"\n'
            "printf '%s\\n' '{\"structured_output\":{\"ok\":true}}'\n"
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", str(executable_directory))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-api-key")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-auth-token")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://provider.invalid")
        monkeypatch.setenv("SHIKIRU_TEST_ENV_CAPTURE", str(capture_path))
        agent = create(
            AgentContext(
                options={},
                runner=SubprocessRunner(),
                clock=SystemClock(),
            )
        )
        headless = agent.headless()
        assert headless is not None

        result = await headless.ask(
            "return true",
            _AskResult.model_json_schema(),
            "haiku",
            None,
            tmp_path,
            timedelta(seconds=10),
        )

        assert result == {"ok": True}
        assert capture_path.read_text().splitlines() == [
            "synthetic-api-key",
            "synthetic-auth-token",
            "https://provider.invalid",
        ]


def _record(name: str, stderr: str, session_id: str) -> RecordedStream:
    return RecordedStream(
        name=name,
        stdout=_FIXTURES_DIR / name,
        stderr=_FIXTURES_DIR / stderr,
        exit_code=0,
        expected_signals=[],
        session_id=session_id,
    )
