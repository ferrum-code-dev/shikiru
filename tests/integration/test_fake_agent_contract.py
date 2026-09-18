"""`fake_agent` passes the agent plugin contract, offline and live (04 §3, 07 §3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from fake_agent.agent import REPL_COMMAND, SESSION_ID_ENV_VAR, create
from pydantic import BaseModel

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    Activity,
    AgentFactory,
    AttentionNeeded,
    Compacted,
    FailureKind,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.agents.testing import AgentContractSuite, LaunchCase, RespawnCall, SampleCase

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "fake"
_MODEL = "fake-standard"
_ROLE = "implementer"

_FULL_TURN_SESSION_ID = "11111111-1111-1111-1111-111111111111"
_TWO_TURNS_SESSION_ID = "22222222-2222-2222-2222-222222222222"
_ERRORS_SESSION_ID = "33333333-3333-3333-3333-333333333333"

_FULL_TURN_USAGE = Usage(
    input_tokens=100,
    output_tokens=50,
    cache_read_tokens=10,
    cache_write_tokens=5,
    cost_usd=0.00107175,
    cost_basis="api_equivalent",
    context_used_pct=0.11499999999999999,
)
_TWO_TURNS_USAGE = Usage(
    input_tokens=300,
    output_tokens=150,
    cache_read_tokens=30,
    cache_write_tokens=15,
    cost_usd=0.00321525,
    cost_basis="api_equivalent",
    context_used_pct=0.22999999999999998,
)
_NO_TURNS_USAGE = Usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="api_equivalent",
    context_used_pct=None,
)

_FIXED_NEW_SESSION_ID = "fixed-new-session-id"


class _AskResult(BaseModel):
    ok: bool


class TestFakeAgentContract(AgentContractSuite):
    """`fake_agent`'s own contract test, cheap enough to run live in every `poe check`."""

    runs_live_checks_in_default_gate: ClassVar[bool] = True

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> dict[str, object]:
        return {"asks": [{"ok": True}]}

    def launch_case_options(self) -> dict[str, object]:
        return {**self.agent_options(), "new_session_id": _FIXED_NEW_SESSION_ID}

    def build_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        return LaunchSpec(
            rung=Rung(agent="fake", model=_MODEL, effort=None),
            role=_ROLE,
            cwd=repo,
            run_dir=tmp_path / "run",
            add_dirs=(),
            env={},
            resume_session_id=resume_session_id,
        )

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def hook_signal_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="hooks_full_turn.jsonl",
                files={HOOKS_FILENAME: _FIXTURES_DIR / "hooks_full_turn.jsonl"},
                expected_signals=[
                    SessionStarted(session_id=_FULL_TURN_SESSION_ID),
                    PromptSubmitted(text="implement the retry helper"),
                    Activity(tool_running=True),
                    Activity(tool_running=False),
                    TurnEnded(last_message="accept: implement the retry helper"),
                    Compacted(),
                    AttentionNeeded(message="waiting for operator input"),
                    SessionEnded(),
                ],
                expected_usage=_FULL_TURN_USAGE,
                session_id=_FULL_TURN_SESSION_ID,
            )
        ]

    def usage_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="hooks_two_turns.jsonl",
                files={HOOKS_FILENAME: _FIXTURES_DIR / "hooks_two_turns.jsonl"},
                expected_signals=[
                    SessionStarted(session_id=_TWO_TURNS_SESSION_ID),
                    PromptSubmitted(text="first turn"),
                    TurnEnded(last_message="accept: first turn"),
                    PromptSubmitted(text="second turn"),
                    TurnEnded(last_message="accept: second turn"),
                ],
                expected_usage=_TWO_TURNS_USAGE,
                session_id=_TWO_TURNS_SESSION_ID,
            )
        ]

    def error_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="errors.jsonl",
                files={HOOKS_FILENAME: _FIXTURES_DIR / "errors.jsonl"},
                expected_signals=[
                    TurnFailed(
                        kind=FailureKind.QUOTA,
                        message="rate limited",
                        resets_at=datetime(2030, 1, 1, tzinfo=UTC),
                    ),
                    TurnFailed(
                        kind=FailureKind.OTHER, message="unexpected failure", resets_at=None
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERRORS_SESSION_ID,
            )
        ]

    def launch_cases(self, tmp_path: Path) -> list[LaunchCase]:
        new_spec = self.build_spec(tmp_path / "new", resume_session_id=None)
        resume_spec = self.build_spec(tmp_path / "resume", resume_session_id="the-old-session-id")
        return [
            LaunchCase(
                name="new session",
                spec=new_spec,
                expected=RespawnCall(
                    argv=(REPL_COMMAND,),
                    env={
                        RUN_DIR_ENV_VAR: str(new_spec.run_dir),
                        ROLE_ENV_VAR: _ROLE,
                        SESSION_ID_ENV_VAR: _FIXED_NEW_SESSION_ID,
                    },
                    cwd=new_spec.cwd,
                ),
            ),
            LaunchCase(
                name="resume",
                spec=resume_spec,
                expected=RespawnCall(
                    argv=(REPL_COMMAND,),
                    env={
                        RUN_DIR_ENV_VAR: str(resume_spec.run_dir),
                        ROLE_ENV_VAR: _ROLE,
                        SESSION_ID_ENV_VAR: "the-old-session-id",
                    },
                    cwd=resume_spec.cwd,
                ),
            ),
        ]
