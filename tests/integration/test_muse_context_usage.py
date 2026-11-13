from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    AgentContext,
    AgentSignal,
    EvidenceFault,
    Rung,
    TurnEnded,
    TurnSpec,
    Usage,
    UsageReported,
)
from shikiru.plugins.agents.muse.agent import create
from shikiru.plugins.agents.muse.options import (
    CACHED_TOKENS_KEY,
    ESTIMATED_PROMPT_TOKENS_KEY,
    EVENT_KIND_KEY,
    INPUT_TOKENS_KEY,
    MODEL_COMPLETED_EVENT,
    MODEL_KEY,
    MUSE_COMMAND,
    MUSE_HOME_DIRECTORY,
    OUTPUT_TOKENS_KEY,
    PAYLOAD_EVENT_KEY,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    SESSION_LOG_FILENAME,
    SESSION_PAYLOAD_TYPE,
    SESSIONS_DIRECTORY,
    USAGE_KEY,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.process import SubprocessRunner
from tests.integration.fake_muse import install_fake_exec, install_fake_muse
from tests.support.clock import LONG_AGO, FakeClock

if TYPE_CHECKING:
    from collections.abc import Sequence

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "turn_first.jsonl"
_SESSION_ID = "00000000-0000-7000-9000-000000000002"
_SESSION_LOG_DATE = ("2026", "11", "12")
_TURN_TIMEOUT_SECONDS = 10
_ROLE = "implementer"
_PROMPT = "continue the task"
_LISTED_MODEL = "muse-spark-1.2"
_LISTED_WINDOW_TOKENS = 200000
_UNLISTED_MODEL = "muse-unlisted-9.9"
_ESTIMATED_PROMPT_TOKENS = 50000
_EXPECTED_CONTEXT_PCT = 25.0
_PRIOR_CONTEXT_PCT = 40.0
_AMBIGUOUS_CATALOG_ARGS = ("--model", _LISTED_MODEL)


def _session_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {
            RECORD_PAYLOAD_TYPE_KEY: SESSION_PAYLOAD_TYPE,
            RECORD_PAYLOAD_KEY: {PAYLOAD_EVENT_KEY: event},
        }
    )


def _model_line(model: str, estimated_prompt_tokens: int) -> str:
    return _session_event(
        {
            EVENT_KIND_KEY: MODEL_COMPLETED_EVENT,
            MODEL_KEY: model,
            USAGE_KEY: {INPUT_TOKENS_KEY: 1, OUTPUT_TOKENS_KEY: 1, CACHED_TOKENS_KEY: 0},
            ESTIMATED_PROMPT_TOKENS_KEY: estimated_prompt_tokens,
        }
    )


def _session_log_path(data_home: Path) -> Path:
    return data_home.joinpath(
        MUSE_HOME_DIRECTORY,
        SESSIONS_DIRECTORY,
        *_SESSION_LOG_DATE,
        _SESSION_ID,
        SESSION_LOG_FILENAME,
    )


async def _run_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str,
    options: dict[str, Any] | None = None,
    prior_usage: Usage | None = None,
) -> list[AgentSignal]:
    install_fake_muse(tmp_path, monkeypatch)
    data_home = tmp_path / "data"
    install_fake_exec(
        monkeypatch,
        session_log=_session_log_path(data_home),
        session_lines=[_model_line(model, _ESTIMATED_PROMPT_TOKENS)],
        stream_lines=_FIXTURE.read_text(encoding="utf-8").splitlines(),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = TurnSpec(
        rung=Rung(agent=MUSE_COMMAND, model=model),
        role=_ROLE,
        prompt=_PROMPT,
        cwd=repo,
        run_dir=tmp_path / "run",
        turn_dir=tmp_path / "turn",
        add_dirs=(),
        env={XDG_DATA_HOME_ENV_VAR: str(data_home)},
        session_id=None,
        usage=prior_usage,
    )
    context = AgentContext(
        options=options or {}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)
    )
    turn = await create(context).start_turn(spec)
    async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
        return [signal async for signal in turn.signals()]


def _context_usages(signals: Sequence[AgentSignal]) -> list[float | None]:
    return [
        signal.usage.context_used_pct for signal in signals if isinstance(signal, UsageReported)
    ]


def _fault_messages(signals: Sequence[AgentSignal]) -> list[str]:
    return [signal.message for signal in signals if isinstance(signal, EvidenceFault)]


async def test_context_usage_is_the_latest_prompt_estimate_over_the_listed_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals = await _run_turn(tmp_path, monkeypatch, model=_LISTED_MODEL)

    assert _context_usages(signals) == [_EXPECTED_CONTEXT_PCT]
    assert _fault_messages(signals) == []
    assert any(isinstance(signal, TurnEnded) for signal in signals)


async def test_context_usage_stays_unknown_when_the_catalog_lacks_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals = await _run_turn(tmp_path, monkeypatch, model=_UNLISTED_MODEL)

    assert _context_usages(signals) == [None]
    faults = _fault_messages(signals)
    assert len(faults) == 1
    assert _UNLISTED_MODEL in faults[0]
    assert any(isinstance(signal, TurnEnded) for signal in signals)


async def test_context_usage_stays_unknown_when_the_catalog_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals = await _run_turn(
        tmp_path,
        monkeypatch,
        model=_LISTED_MODEL,
        options={"args": list(_AMBIGUOUS_CATALOG_ARGS)},
    )

    assert _context_usages(signals) == [None]
    faults = _fault_messages(signals)
    assert len(faults) == 1
    assert _LISTED_MODEL in faults[0]
    assert any(isinstance(signal, TurnEnded) for signal in signals)


@pytest.mark.parametrize(
    ("model", "options"),
    [
        (_UNLISTED_MODEL, {}),
        (_LISTED_MODEL, {"args": list(_AMBIGUOUS_CATALOG_ARGS)}),
    ],
    ids=("model-not-in-catalog", "catalog-unavailable"),
)
async def test_unknown_window_drops_the_prior_context_share(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    options: dict[str, Any],
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, _PRIOR_CONTEXT_PCT)

    signals = await _run_turn(
        tmp_path, monkeypatch, model=model, options=options, prior_usage=prior
    )

    assert _context_usages(signals) == [None]
    assert len(_fault_messages(signals)) == 1
    assert any(isinstance(signal, TurnEnded) for signal in signals)
