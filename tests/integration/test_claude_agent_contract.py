"""Claude Code satisfies the agent plugin contract with committed captures (04 §3)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    Activity,
    AttentionNeeded,
    Compacted,
    FailureKind,
    LaunchSpec,
    LegacyAgentFactory,
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    shikiru_command,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.testing import (
    LaunchCase,
    LegacyAgentContractSuite,
    RecordingPaneHandle,
    SampleCase,
    TmuxSocket,
)
from shikiru.clock import SystemClock
from shikiru.plugins.agents.claude.agent import create
from shikiru.plugins.agents.claude.options import PERMISSION_MODE_BYPASS, PERMISSION_MODE_FLAG
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.agents.contracts import AgentSession, InteractiveAgent
    from shikiru.tmux.pane import TmuxPane

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "claude"
_MODEL = "claude-sonnet-4-5-20250929"
_ROLE = "implementer"
_FULL_TURN_SESSION_ID = "11111111-1111-1111-1111-111111111111"
_TWO_TURNS_SESSION_ID = "22222222-2222-2222-2222-222222222222"
_ERROR_SESSION_ID = "33333333-3333-3333-3333-333333333333"
_QUOTA_MESSAGE = "You've hit your session limit · resets 3:10am (Europe/Warsaw)"

_FULL_TURN_USAGE = Usage(
    input_tokens=100,
    output_tokens=50,
    cache_read_tokens=10,
    cache_write_tokens=5,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)
_TWO_TURNS_USAGE = Usage(
    input_tokens=300,
    output_tokens=150,
    cache_read_tokens=30,
    cache_write_tokens=15,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)
_NO_TURNS_USAGE = Usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)

_LIVE_START_TIMEOUT_SECONDS = 30.0
_TRUST_PROMPT_MARKER = "Yes, I trust this folder"
_TRUST_CONFIRM_MARKER = "Enter to confirm"
_TRUST_PROMPT_STABILITY_COUNT = 10


class _AskResult(BaseModel):
    ok: bool


class TestClaudeAgentContract(LegacyAgentContractSuite):
    """Exercise Claude's offline contract and gate live checks behind SHIKIRU_E2E."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False

    def agent_factory(self) -> LegacyAgentFactory:
        return create

    def agent_options(self) -> dict[str, object]:
        return {"auth": "subscription"}

    def build_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = tmp_path / "run"
        return LaunchSpec(
            rung=Rung(agent="claude", model=_MODEL, effort="high"),
            role=_ROLE,
            cwd=repo,
            run_dir=run_dir,
            add_dirs=(),
            env={RUN_DIR_ENV_VAR: str(run_dir), ROLE_ENV_VAR: _ROLE},
            resume_session_id=resume_session_id,
        )

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def headless_ask_model(self) -> str:
        return "haiku"

    def hook_signal_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="full turn",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_full_turn.jsonl",
                    "transcript_full_turn.jsonl": _FIXTURES_DIR / "transcript_full_turn.jsonl",
                },
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
                name="two turns",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_two_turns.jsonl",
                    "transcript_two_turns.jsonl": _FIXTURES_DIR / "transcript_two_turns.jsonl",
                },
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
        cases = (
            (
                "quota",
                FailureKind.QUOTA,
                _QUOTA_MESSAGE,
                ResetTimeParser(SystemClock()).parse(_QUOTA_MESSAGE),
            ),
            ("unavailable", FailureKind.UNAVAILABLE, "service overloaded", None),
            ("auth", FailureKind.AUTH, "authentication failed", None),
            ("context", FailureKind.CONTEXT, "prompt is too long", None),
            ("other", FailureKind.OTHER, "unexpected failure", None),
        )
        return [
            SampleCase(
                name=f"{name} failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / f"hooks_{name}_error.jsonl",
                    f"transcript_{name}_error.jsonl": _FIXTURES_DIR
                    / f"transcript_{name}_error.jsonl",
                },
                expected_signals=[TurnFailed(kind=kind, message=message, resets_at=resets_at)],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            )
            for name, kind, message, resets_at in cases
        ]

    def launch_cases(self, tmp_path: Path) -> list[LaunchCase]:
        del tmp_path
        return []

    async def _launch_live(
        self, tmux_server: TmuxSocket, tmp_path: Path, *, prepare_turn: bool
    ) -> tuple[InteractiveAgent, LaunchSpec, TmuxPane, AgentSession]:
        agent, spec, pane, session = await super()._launch_live(
            tmux_server, tmp_path, prepare_turn=prepare_turn
        )
        role_path = spec.run_dir / spec.role
        hooks_path = role_path / HOOKS_FILENAME
        prompt_visible = False
        prompt_observations = 0

        async def _startup_ready() -> bool:
            nonlocal prompt_observations, prompt_visible
            visible = await pane.capture_visible()
            if _TRUST_PROMPT_MARKER in visible and _TRUST_CONFIRM_MARKER in visible:
                prompt_observations += 1
                if prompt_observations >= _TRUST_PROMPT_STABILITY_COUNT:
                    prompt_visible = True
                    return True
            else:
                prompt_observations = 0
            return hooks_path.is_file()

        await wait_until(_startup_ready, timeout=_LIVE_START_TIMEOUT_SECONDS)
        if prompt_visible:
            await pane.send_keys("Down", "Enter")
            await wait_until(hooks_path.is_file, timeout=_LIVE_START_TIMEOUT_SECONDS)
        return agent, spec, pane, session

    async def test_offline_launch_argv_and_env(self, tmp_path: Path) -> None:
        spec = self.build_spec(tmp_path / "new", resume_session_id=None)
        pane = RecordingPaneHandle()
        await self._build_agent().launch(spec, pane)
        call = pane.last_respawn()
        assert call.cwd == spec.cwd
        assert call.env[RUN_DIR_ENV_VAR] == str(spec.run_dir)
        assert call.env[ROLE_ENV_VAR] == _ROLE
        assert call.env["ANTHROPIC_API_KEY"] is None
        assert call.env["ANTHROPIC_AUTH_TOKEN"] is None
        assert call.argv[:2] == ("claude", "--session-id")
        uuid.UUID(call.argv[2])
        assert call.argv[3:7] == ("--model", _MODEL, "--effort", "high")
        assert call.argv[-2:] == (PERMISSION_MODE_FLAG, PERMISSION_MODE_BYPASS)
        settings = json.loads(call.argv[call.argv.index("--settings") + 1])
        assert set(settings["hooks"]) == {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "StopFailure",
            "Notification",
            "PostCompact",
            "SessionEnd",
        }
        assert all(
            value == [{"hooks": [{"type": "command", "command": shikiru_command("hook")}]}]
            for value in settings["hooks"].values()
        )

        resume_spec = self.build_spec(tmp_path / "resume", resume_session_id="the-old-session-id")
        resume_pane = RecordingPaneHandle()
        await self._build_agent().launch(resume_spec, resume_pane)
        resume_call = resume_pane.last_respawn()
        assert resume_call.argv[:3] == ("claude", "--resume", "the-old-session-id")

        api_key_name = "SHIKIRU_CLAUDE_TEST_KEY"
        previous_key = os.environ.get(api_key_name)
        os.environ[api_key_name] = "test-key"
        try:
            api_agent = self._build_agent(
                {"auth": "api_key", "api_key_env": api_key_name, "args": []}
            )
            api_pane = RecordingPaneHandle()
            api_spec = replace(
                spec,
                env={**spec.env, "ANTHROPIC_AUTH_TOKEN": "parent-token"},
            )
            await api_agent.launch(api_spec, api_pane)
            api_environment = api_pane.last_respawn().env
            assert api_environment["ANTHROPIC_API_KEY"] == "test-key"
            assert api_environment["ANTHROPIC_AUTH_TOKEN"] is None
        finally:
            if previous_key is None:
                os.environ.pop(api_key_name, None)
            else:
                os.environ[api_key_name] = previous_key
