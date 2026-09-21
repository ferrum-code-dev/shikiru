from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile

from shikiru.agents.contracts import PromptSubmitted, SessionStarted
from shikiru.agents.sdk.hooks import HookRecorder
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.codex.session import CodexSession
from tests.support.clock import FakeClock
from tests.support.waiting import yield_control

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "codex"
_PRICES_PATH = (
    Path(__file__).parents[2] / "src" / "shikiru" / "plugins" / "agents" / "codex" / "prices.toml"
)
_ROLE = "implementer"
_SESSION_ID = "cbb5d575-41f3-4e6b-a24f-483c51c0a097"
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _session(run_dir: Path, *, compact_below_free_pct: int) -> CodexSession:
    clock = FakeClock(_START)
    return CodexSession(
        run_dir,
        _ROLE,
        _SESSION_ID,
        0,
        run_dir / _ROLE / "rollout_full_turn.jsonl",
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices=PriceTable.from_toml(_PRICES_PATH),
        model="gpt-5.6-sol",
        api_key_auth=False,
        compact_below_free_pct=compact_below_free_pct,
        codex_home=run_dir / "codex",
    )


async def test_before_turn_requests_compaction_below_free_context(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    copyfile(_FIXTURES_DIR / "rollout_full_turn.jsonl", role_dir / "rollout_full_turn.jsonl")

    assert await _session(run_dir, compact_below_free_pct=100).before_turn() == ["/compact"]
    assert await _session(run_dir, compact_below_free_pct=90).before_turn() == []


async def test_session_discovers_rollout_when_start_hook_has_no_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    codex_home = tmp_path / "codex"
    rollout = (
        codex_home / "sessions" / "2026" / "01" / f"rollout-2026-01-01T00-00-00-{_SESSION_ID}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    copyfile(_FIXTURES_DIR / "rollout_full_turn.jsonl", rollout)
    HookRecorder(role_dir).record({"hook_event_name": "SessionStart", "session_id": _SESSION_ID})
    clock = FakeClock(_START)
    session = CodexSession(
        run_dir,
        _ROLE,
        None,
        0,
        None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices=PriceTable.from_toml(_PRICES_PATH),
        model="gpt-5.6-sol",
        api_key_auth=False,
        compact_below_free_pct=60,
        codex_home=codex_home,
    )

    signal = await session.signals().__anext__()

    assert signal == SessionStarted(session_id=_SESSION_ID)
    assert (await session.usage()).input_tokens == 14829


async def test_session_ignores_foreign_and_malformed_hook_entries(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    role_path = run_dir / _ROLE
    role_path.mkdir(parents=True)
    clock = FakeClock(_START)
    session = CodexSession(
        run_dir,
        _ROLE,
        _SESSION_ID,
        0,
        None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices=PriceTable.from_toml(_PRICES_PATH),
        model="gpt-5.6-sol",
        api_key_auth=False,
        compact_below_free_pct=60,
        codex_home=tmp_path / "codex",
        session_start_observed=True,
    )
    recorder = HookRecorder(role_path)
    recorder.record(
        {
            "hook_event_name": "SessionStart",
            "session_id": "foreign-session",
        }
    )
    recorder.record({"hook_event_name": "UserPromptSubmit", "session_id": 42, "prompt": "bad"})
    recorder.record(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": _SESSION_ID,
            "prompt": "current turn",
        }
    )

    signal = await session.signals().__anext__()

    assert signal == PromptSubmitted(text="current turn")


async def test_resumed_session_waits_for_session_start_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    role_path = run_dir / _ROLE
    role_path.mkdir(parents=True)
    clock = FakeClock(_START)
    session = CodexSession(
        run_dir,
        _ROLE,
        _SESSION_ID,
        0,
        None,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices=PriceTable.from_toml(_PRICES_PATH),
        model="gpt-5.6-sol",
        api_key_auth=False,
        compact_below_free_pct=60,
        codex_home=tmp_path / "codex",
    )
    HookRecorder(role_path).record(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": _SESSION_ID,
            "prompt": "before start",
        }
    )
    signals = session.signals()
    pending = asyncio.create_task(signals.__anext__())
    await yield_control()

    assert not pending.done()
    pending.cancel()
    with suppress(asyncio.CancelledError):
        await pending
