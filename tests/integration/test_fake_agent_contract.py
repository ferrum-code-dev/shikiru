"""The per-turn fake agent proves the headless plugin contract (04 §3)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from fake_agent.agent import FakeAgent, create
from fake_agent.turns import (
    FAKE_COMPACT_AT_PCT_ENV_VAR,
    FAKE_SESSION_ID_ENV_VAR,
    FakeTurnParser,
)
from pydantic import BaseModel

from shikiru.agents.contracts import (
    Agent,
    AgentFactory,
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
from shikiru.agents.testing import AgentContractSuite, InvocationCase, RecordedStream

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.agents.sdk.turn import TurnParser, TurnProcessSequence

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "agents" / "fake"
_ROLE = "implementer"
_MODEL = "fake-standard"
_FIXED_SESSION_ID = "fixed-new-session-id"
_RESUMED_SESSION_ID = "resumed-session-id"
_COMPACTION_THRESHOLD = 80.0
_TOKEN_USAGE = Usage(
    input_tokens=120,
    output_tokens=60,
    cache_read_tokens=20,
    cache_write_tokens=10,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=90.0,
)
_COMPACTED_USAGE = Usage(
    input_tokens=240,
    output_tokens=120,
    cache_read_tokens=40,
    cache_write_tokens=20,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=30.0,
)
_RETRY_USAGE = Usage(
    input_tokens=120,
    output_tokens=60,
    cache_read_tokens=20,
    cache_write_tokens=10,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=30.0,
)
_CLI_PREFIX = (sys.executable, "-m", "fake_agent.turn_cli")
_TURN_ARGUMENTS = ("turn", "--session-id")
_COMPACT_ARGUMENTS = ("compact", "--session-id")
_PROMPT_ARGUMENT = "--prompt"
_MODEL_ARGUMENT = "--model"


class _AskResult(BaseModel):
    ok: bool


class TestFakeAgentContract(AgentContractSuite):
    """Run the fake plugin's offline checks and its inexpensive live checks by default."""

    runs_live_checks_in_default_gate: ClassVar[bool] = True

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> Mapping[str, object]:
        return {
            "asks": [{"ok": True}],
            "auth": "none",
            "compact_at_pct": 10.0,
            "new_session_id": _FIXED_SESSION_ID,
        }

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
        turn_name = "01-turn" if resume_session_id is None else "02-turn"
        turn_dir = tmp_path / "turns" / turn_name
        return TurnSpec(
            rung=Rung(agent="fake", model=model or _MODEL, effort=None),
            role=_ROLE,
            prompt="complete the requested turn",
            cwd=repo,
            run_dir=run_dir,
            turn_dir=turn_dir,
            add_dirs=(),
            env={"SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE},
            session_id=resume_session_id,
            usage=usage,
        )

    def parser_for(self, sample: RecordedStream) -> TurnParser:
        del sample
        return FakeTurnParser()

    def recorded_streams(self) -> Sequence[RecordedStream]:
        full_session = "session-full"
        compacted_session = "session-compacted"
        return [
            RecordedStream(
                name="turn_full.jsonl",
                stdout=_FIXTURE_DIR / "turn_full.jsonl",
                stderr=_FIXTURE_DIR / "turn_full.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(full_session),
                    Message("completed the requested work"),
                    ToolStarted("shikiru report", "submitting report"),
                    ToolFinished("shikiru report"),
                    UsageReported(_TOKEN_USAGE),
                    TurnEnded("completed the requested work"),
                ],
                session_id=full_session,
            ),
            RecordedStream(
                name="turn_compacted.jsonl",
                stdout=_FIXTURE_DIR / "turn_compacted.jsonl",
                stderr=_FIXTURE_DIR / "turn_compacted.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(compacted_session),
                    UsageReported(
                        Usage(
                            input_tokens=200,
                            output_tokens=100,
                            cache_read_tokens=30,
                            cache_write_tokens=15,
                            cost_usd=None,
                            cost_basis="unknown",
                            context_used_pct=90.0,
                        )
                    ),
                    Compacted(2000, 400),
                    Message("the earlier nonce is still remembered"),
                    UsageReported(_COMPACTED_USAGE),
                    TurnEnded("the earlier nonce is still remembered"),
                ],
                session_id=compacted_session,
            ),
            RecordedStream(
                name="turn_compact_process.jsonl",
                stdout=_FIXTURE_DIR / "turn_compact_process.jsonl",
                stderr=_FIXTURE_DIR / "turn_full.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    Compacted(2000, 400),
                    TurnEnded("context compacted"),
                ],
                session_id=_RESUMED_SESSION_ID,
            ),
            RecordedStream(
                name="turn_retry_success.jsonl",
                stdout=_FIXTURE_DIR / "turn_retry_success.jsonl",
                stderr=_FIXTURE_DIR / "turn_full.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    Message("retry completed"),
                    UsageReported(_RETRY_USAGE),
                    TurnEnded("retry completed"),
                ],
                session_id=_RESUMED_SESSION_ID,
            ),
        ]

    def error_streams(self) -> Sequence[RecordedStream]:
        quota_session = "session-quota"
        unknown_session = "session-unknown"
        return [
            RecordedStream(
                name="turn_quota_error.jsonl",
                stdout=_FIXTURE_DIR / "turn_quota_error.jsonl",
                stderr=_FIXTURE_DIR / "turn_quota_error.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(quota_session),
                    TurnFailed(
                        kind=FailureKind.QUOTA,
                        message="rate limited",
                        resets_at=datetime(2030, 1, 1, tzinfo=UTC),
                    ),
                ],
                session_id=quota_session,
            ),
            RecordedStream(
                name="turn_unknown_error.jsonl",
                stdout=_FIXTURE_DIR / "turn_unknown_error.jsonl",
                stderr=_FIXTURE_DIR / "turn_unknown_error.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(unknown_session),
                    TurnFailed(
                        kind=FailureKind.UNKNOWN,
                        message="an unrecognized structured failure",
                        resets_at=None,
                    ),
                ],
                session_id=unknown_session,
            ),
            RecordedStream(
                name="turn_unmapped_error.jsonl",
                stdout=_FIXTURE_DIR / "turn_unmapped_error.jsonl",
                stderr=_FIXTURE_DIR / "turn_unmapped_error.stderr.log",
                exit_code=9,
                expected_signals=[
                    SessionStarted("session-unmapped"),
                    TurnFailed(
                        kind=FailureKind.UNKNOWN,
                        message="fake CLI exited 9: unrecognized failure text",
                        resets_at=None,
                    ),
                ],
                session_id="session-unmapped",
            ),
            RecordedStream(
                name="turn_context_failure.jsonl",
                stdout=_FIXTURE_DIR / "turn_context_failure.jsonl",
                stderr=_FIXTURE_DIR / "turn_full.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    TurnFailed(FailureKind.OTHER, "context full", None),
                ],
                session_id=_RESUMED_SESSION_ID,
            ),
            RecordedStream(
                name="turn_retry_context_failure.jsonl",
                stdout=_FIXTURE_DIR / "turn_retry_context_failure.jsonl",
                stderr=_FIXTURE_DIR / "turn_full.stderr.log",
                exit_code=0,
                expected_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    TurnFailed(FailureKind.OTHER, "context still full", None),
                ],
                session_id=_RESUMED_SESSION_ID,
            ),
        ]

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        options = self.agent_options()
        new_spec = self.build_spec(tmp_path / "new", resume_session_id=None)
        resumed_spec = self.build_spec(
            tmp_path / "resumed", resume_session_id=_RESUMED_SESSION_ID, model="fake-next"
        )
        compact_spec = self.build_spec(
            tmp_path / "compact",
            resume_session_id=_RESUMED_SESSION_ID,
            usage=_TOKEN_USAGE,
        )
        outcome_streams = {
            sample.name: sample for sample in (*self.recorded_streams(), *self.error_streams())
        }
        retry_spec = self.build_spec(tmp_path / "retry", resume_session_id=_RESUMED_SESSION_ID)
        return [
            InvocationCase(
                name="new session",
                options=options,
                spec=new_spec,
                expected_argv=[
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _FIXED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        new_spec.prompt,
                        _MODEL_ARGUMENT,
                        new_spec.rung.model,
                    )
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _FIXED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: "10.0",
                    }
                ],
            ),
            InvocationCase(
                name="resumed session with model change",
                options=options,
                spec=resumed_spec,
                expected_argv=[
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        resumed_spec.prompt,
                        _MODEL_ARGUMENT,
                        resumed_spec.rung.model,
                    )
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: "10.0",
                    }
                ],
                interactive_session_id=_RESUMED_SESSION_ID,
                expected_interactive_command=(
                    *_CLI_PREFIX,
                    "interactive",
                    "--session-id",
                    _RESUMED_SESSION_ID,
                ),
            ),
            InvocationCase(
                name="no-credentials auth mode",
                options={**options, "auth": "none"},
                spec=new_spec,
                expected_argv=[
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _FIXED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        new_spec.prompt,
                        _MODEL_ARGUMENT,
                        new_spec.rung.model,
                    )
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _FIXED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                    }
                ],
            ),
            InvocationCase(
                name="pre-turn compaction before a resumed turn",
                options={**options, "compact_at_pct": _COMPACTION_THRESHOLD},
                spec=compact_spec,
                expected_argv=[
                    (*_CLI_PREFIX, *_COMPACT_ARGUMENTS, _RESUMED_SESSION_ID),
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        compact_spec.prompt,
                        _MODEL_ARGUMENT,
                        compact_spec.rung.model,
                    ),
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "compact",
                    },
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: str(_COMPACTION_THRESHOLD),
                    },
                ],
                outcome_streams=[outcome_streams["turn_compact_process.jsonl"]],
            ),
            InvocationCase(
                name="context failure compacts and reruns the prompt once",
                options={**options, "compact_at_pct": _COMPACTION_THRESHOLD},
                spec=retry_spec,
                expected_argv=[
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        retry_spec.prompt,
                        _MODEL_ARGUMENT,
                        retry_spec.rung.model,
                    ),
                    (*_CLI_PREFIX, *_COMPACT_ARGUMENTS, _RESUMED_SESSION_ID),
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        retry_spec.prompt,
                        _MODEL_ARGUMENT,
                        retry_spec.rung.model,
                    ),
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: str(_COMPACTION_THRESHOLD),
                    },
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "compact",
                    },
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "1",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: str(_COMPACTION_THRESHOLD),
                    },
                ],
                outcome_streams=[
                    outcome_streams["turn_context_failure.jsonl"],
                    outcome_streams["turn_compact_process.jsonl"],
                    outcome_streams["turn_retry_success.jsonl"],
                ],
                expected_final_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    Compacted(2000, 400),
                    Message("retry completed"),
                    UsageReported(_RETRY_USAGE),
                    TurnEnded("retry completed"),
                ],
            ),
            InvocationCase(
                name="second context failure ends after one retry",
                options={**options, "compact_at_pct": _COMPACTION_THRESHOLD},
                spec=retry_spec,
                expected_argv=[
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        retry_spec.prompt,
                        _MODEL_ARGUMENT,
                        retry_spec.rung.model,
                    ),
                    (*_CLI_PREFIX, *_COMPACT_ARGUMENTS, _RESUMED_SESSION_ID),
                    (
                        *_CLI_PREFIX,
                        *_TURN_ARGUMENTS,
                        _RESUMED_SESSION_ID,
                        _PROMPT_ARGUMENT,
                        retry_spec.prompt,
                        _MODEL_ARGUMENT,
                        retry_spec.rung.model,
                    ),
                ],
                expected_env=[
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "0",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: str(_COMPACTION_THRESHOLD),
                    },
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "compact",
                    },
                    {
                        FAKE_SESSION_ID_ENV_VAR: _RESUMED_SESSION_ID,
                        "SHIKIRU_FAKE_PHASE": "turn",
                        "SHIKIRU_FAKE_RETRY": "1",
                        FAKE_COMPACT_AT_PCT_ENV_VAR: str(_COMPACTION_THRESHOLD),
                    },
                ],
                outcome_streams=[
                    outcome_streams["turn_context_failure.jsonl"],
                    outcome_streams["turn_compact_process.jsonl"],
                    outcome_streams["turn_retry_context_failure.jsonl"],
                ],
                expected_final_signals=[
                    SessionStarted(_RESUMED_SESSION_ID),
                    Compacted(2000, 400),
                    TurnFailed(FailureKind.OTHER, "context still full", None),
                ],
            ),
        ]

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> TurnProcessSequence:
        return cast("FakeAgent", agent).process_sequence(spec)

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult
