"""`fake_agent` passes the agent plugin contract, offline and live (04 §3, 07 §3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from fake_agent.agent import PRICES_PATH, create
from fake_agent.signals import error_from_entry, signal_from_entry, usage_from_entries

from shikiru.agents.contracts import (
    Activity,
    AgentContext,
    AttentionNeeded,
    Compacted,
    FailureKind,
    InteractiveAgent,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    Usage,
)
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.testing import AgentContractSuite, Case, LaunchCase, RespawnCall
from shikiru.clock import SystemClock
from shikiru.process import SubprocessRunner

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "fake"
_MODEL = "fake-standard"


def _entries(filename: str) -> list[dict[str, Any]]:
    lines = (_FIXTURES_DIR / filename).read_text().splitlines()
    return [json.loads(line) for line in lines if line]


class TestFakeAgentContract(AgentContractSuite):
    """`fake_agent`'s own contract test, cheap enough to run live in every `poe check`."""

    runs_live_checks_in_default_gate: ClassVar[bool] = True

    def build_agent(self) -> InteractiveAgent:
        context = AgentContext(
            options={"asks": [{"ok": True}]}, runner=SubprocessRunner(), clock=SystemClock()
        )
        return create(context)

    def build_live_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        return LaunchSpec(
            rung=Rung(agent="fake", model=_MODEL, effort=None),
            role="implementer",
            cwd=repo,
            run_dir=tmp_path / "run",
            add_dirs=(),
            env={},
            resume_session_id=resume_session_id,
        )

    def headless_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    def hook_signal_cases(self) -> list[Case]:
        reset_time = ResetTimeParser(SystemClock())
        entries = _entries("hooks_full_turn.jsonl")
        expected = [
            SessionStarted(session_id="11111111-1111-1111-1111-111111111111"),
            PromptSubmitted(text="implement the retry helper"),
            Activity(tool_running=True),
            Activity(tool_running=False),
            TurnEnded(last_message="accept: implement the retry helper"),
            Compacted(),
            AttentionNeeded(message="waiting for operator input"),
            SessionEnded(),
        ]
        actual = [signal_from_entry(entry, reset_time) for entry in entries]
        return [Case(name="hooks_full_turn.jsonl", actual=actual, expected=expected)]

    def usage_cases(self) -> list[Case]:
        prices = PriceTable.from_toml(PRICES_PATH)
        entries = _entries("hooks_two_turns.jsonl")
        actual = usage_from_entries(entries, model=_MODEL, prices=prices)
        expected = Usage(
            input_tokens=300,
            output_tokens=150,
            cache_read_tokens=30,
            cache_write_tokens=15,
            cost_usd=0.00321525,
            cost_basis="api_equivalent",
            context_used_pct=0.22999999999999998,
        )
        return [Case(name="hooks_two_turns.jsonl", actual=actual, expected=expected)]

    def error_cases(self) -> list[Case]:
        reset_time = ResetTimeParser(SystemClock())
        entries = _entries("errors.jsonl")
        expected = [
            (FailureKind.QUOTA, datetime(2030, 1, 1, tzinfo=UTC)),
            (FailureKind.OTHER, None),
        ]
        actual = [error_from_entry(entry, reset_time) for entry in entries]
        return [Case(name="errors.jsonl", actual=actual, expected=expected)]

    def launch_cases(self, tmp_path: Path) -> list[LaunchCase]:
        context = AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock())
        agent = create(context)
        new_spec = self.build_live_spec(tmp_path, resume_session_id=None)

        def _check_new(call: RespawnCall) -> None:
            assert call.argv == ("fake-agent-repl",)
            assert call.env["SHIKIRU_RUN_DIR"] == str(new_spec.run_dir)
            assert call.env["SHIKIRU_ROLE"] == "implementer"
            assert call.env["SHIKIRU_FAKE_SESSION_ID"]

        resume_spec = self.build_live_spec(tmp_path, resume_session_id="the-old-session-id")

        def _check_resume(call: RespawnCall) -> None:
            assert call.argv == ("fake-agent-repl",)
            assert call.env["SHIKIRU_FAKE_SESSION_ID"] == "the-old-session-id"

        return [
            LaunchCase(name="new session", agent=agent, spec=new_spec, check=_check_new),
            LaunchCase(
                name="resume",
                agent=agent,
                spec=resume_spec,
                check=_check_resume,
                resume_state={"session_id": "the-old-session-id"},
            ),
        ]
