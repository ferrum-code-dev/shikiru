from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    RUNTIME_DEFAULT_MODEL,
    AgentSignal,
    Compacted,
    EvidenceFault,
    EvidenceRecovered,
    FailureKind,
    Rung,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.plugins.agents.muse.options import (
    BUDGET_AFTER_KEY,
    BUDGET_BEFORE_KEY,
    CACHED_TOKENS_KEY,
    COMPACTION_INSTALLED_EVENT,
    DEFAULT_COMPACT_AT_PCT,
    ESTIMATED_PROMPT_TOKENS_KEY,
    EVENT_KIND_KEY,
    EXEC_SUBCOMMAND,
    HARD_THRESHOLD_FLAG,
    INPUT_TOKENS_KEY,
    JSON_FLAG,
    MAX_COMPACT_AT_PCT,
    MODEL_COMPLETED_EVENT,
    MODEL_FLAG,
    MODEL_KEY,
    MUSE_COMMAND,
    MUSE_HOME_DIRECTORY,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    OUTPUT_TOKENS_KEY,
    PAYLOAD_EVENT_KEY,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    RUN_TERMINAL_FAILED_TYPE,
    SESSION_ID_FLAG,
    SESSION_LOG_FILENAME,
    SESSION_PAYLOAD_TYPE,
    SESSIONS_DIRECTORY,
    SOFT_THRESHOLD_FLAG,
    USAGE_KEY,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.plugins.agents.muse.turn import MuseTurnSequence
from shikiru.private import PRIVATE_FILE_MODE, write_private_text
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_TEST_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TURN_TIMEOUT_SECONDS = 10
_AGENT = "muse"
_MODEL = "muse-model"
_EFFORT = "high"
_SESSION_ID = "00000000-0000-7000-9000-000000000002"
_SPEC_PROMPT = "continue the task"
_SOFT_THRESHOLD = "0.8"
_HARD_THRESHOLD = "0.805"
_EXTRA_ARGS = ("--disable-shell",)
_CONTEXT_WINDOW_TOKENS = 1000
_SESSION_LOG_DATE = ("2026", "11", "12")
_CONTEXT_REASON = "context window exceeded"
_UNAVAILABLE_REASON = "upstream returned HTTP 503"
_NOT_JSON_LINE = "{not json"
_NOT_UTF8_LINE = "\udcff"
_NOT_UTF8_BYTES = b"\xff\n"
_FAKE_ENV_CALLS = "FAKE_MUSE_CALLS"
_FAKE_ENV_RESPONSES = "FAKE_MUSE_RESPONSES"
_FAKE_MUSE = """#!{python}
import json
import os
import stat
import sys
from pathlib import Path

calls = Path(os.environ["FAKE_MUSE_CALLS"])
responses = json.loads(os.environ["FAKE_MUSE_RESPONSES"])
index = len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0
with calls.open("a", encoding="utf-8") as log:
    prompt_path = Path(sys.argv[sys.argv.index("--prompt-file") + 1])
    record = {{
        "argv": sys.argv[1:],
        "auto_update": os.environ.get("MUSE_NO_AUTO_UPDATE"),
        "prompt": prompt_path.read_text(encoding="utf-8"),
        "prompt_mode": stat.S_IMODE(prompt_path.stat().st_mode),
    }}
    log.write(json.dumps(record) + "\\n")
response = responses[index]
if response["session_log_lines"]:
    session_log = Path(response["session_log_path"])
    session_log.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(line + "\\n" for line in response["session_log_lines"])
    with session_log.open("ab") as handle:
        handle.write(text.encode("utf-8", "surrogateescape"))
sys.stdout.write("".join(line + "\\n" for line in response["lines"]))
sys.exit(response["exit"])
"""


@dataclass(frozen=True)
class _Response:
    lines: list[str]
    exit_code: int
    session_log: tuple[str, ...] = ()


def _fixture_lines(name: str) -> list[str]:
    return (_FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()


def _success(session_log: tuple[str, ...] = ()) -> _Response:
    return _Response(_fixture_lines("turn_first"), 0, session_log)


def _auth_failure() -> _Response:
    return _Response(_fixture_lines("turn_auth_rejected"), 1)


def _failed(reason: str, session_log: tuple[str, ...] = ()) -> _Response:
    session_record = json.loads(_fixture_lines("turn_first")[0])
    failure = {
        "stream": session_record["stream"],
        "payload_type": RUN_TERMINAL_FAILED_TYPE,
        "payload": {"kind": "run_terminal", "terminal": "failed", "text": "", "reason": reason},
    }
    return _Response([json.dumps(session_record), json.dumps(failure)], 1, session_log)


def _context_failure(session_log: tuple[str, ...] = ()) -> _Response:
    return _failed(_CONTEXT_REASON, session_log)


def _session_log_path(data_home: Path) -> Path:
    return data_home.joinpath(
        MUSE_HOME_DIRECTORY,
        SESSIONS_DIRECTORY,
        *_SESSION_LOG_DATE,
        _SESSION_ID,
        SESSION_LOG_FILENAME,
    )


def _session_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {
            RECORD_PAYLOAD_TYPE_KEY: SESSION_PAYLOAD_TYPE,
            RECORD_PAYLOAD_KEY: {PAYLOAD_EVENT_KEY: event},
        }
    )


def _compaction_line(before: int, after: int) -> str:
    return _session_event(
        {
            EVENT_KIND_KEY: COMPACTION_INSTALLED_EVENT,
            BUDGET_BEFORE_KEY: {ESTIMATED_PROMPT_TOKENS_KEY: before},
            BUDGET_AFTER_KEY: {ESTIMATED_PROMPT_TOKENS_KEY: after},
        }
    )


def _model_line(estimated_prompt_tokens: int) -> str:
    return _session_event(
        {
            EVENT_KIND_KEY: MODEL_COMPLETED_EVENT,
            MODEL_KEY: _MODEL,
            USAGE_KEY: {INPUT_TOKENS_KEY: 1, OUTPUT_TOKENS_KEY: 1, CACHED_TOKENS_KEY: 0},
            ESTIMATED_PROMPT_TOKENS_KEY: estimated_prompt_tokens,
        }
    )


def _compactions(signals: list[AgentSignal]) -> list[Compacted]:
    return [signal for signal in signals if isinstance(signal, Compacted)]


def _usage_reports(signals: list[AgentSignal]) -> list[Usage]:
    return [signal.usage for signal in signals if isinstance(signal, UsageReported)]


def _faults(signals: list[AgentSignal]) -> list[EvidenceFault]:
    return [signal for signal in signals if isinstance(signal, EvidenceFault)]


def _recoveries(signals: list[AgentSignal]) -> list[EvidenceRecovered]:
    return [signal for signal in signals if isinstance(signal, EvidenceRecovered)]


def _failures(signals: list[AgentSignal]) -> list[TurnFailed]:
    return [signal for signal in signals if isinstance(signal, TurnFailed)]


def _endings(signals: list[AgentSignal]) -> list[TurnEnded]:
    return [signal for signal in signals if isinstance(signal, TurnEnded)]


async def _run_turn(
    tmp_path: Path,
    responses: list[_Response],
    *,
    rung: Rung,
    session_id: str | None = None,
    data_home: Path | None = None,
    base_environment: dict[str, str] | None = None,
    spec_environment: dict[str, str] | None = None,
    turn_prompt: str | None = None,
    compact_at_pct: float = DEFAULT_COMPACT_AT_PCT,
    prior_usage: Usage | None = None,
) -> tuple[list[dict[str, Any]], list[AgentSignal]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    data_home = data_home or tmp_path / "data"
    session_log = _session_log_path(data_home)
    scripted = [
        {
            "lines": response.lines,
            "exit": response.exit_code,
            "session_log_path": str(session_log),
            "session_log_lines": list(response.session_log),
        }
        for response in responses
    ]
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        XDG_DATA_HOME_ENV_VAR: str(data_home),
        _FAKE_ENV_CALLS: str(calls),
        _FAKE_ENV_RESPONSES: json.dumps(scripted),
        **(spec_environment or {}),
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    turn_dir = tmp_path / "turn"
    if turn_prompt is not None:
        turn_dir.mkdir()
        write_private_text(turn_dir / PROMPT_FILENAME, turn_prompt)
    spec = TurnSpec(
        rung=rung,
        role="implementer",
        prompt=_SPEC_PROMPT,
        cwd=repo,
        run_dir=tmp_path / "run",
        turn_dir=turn_dir,
        add_dirs=(),
        env=env,
        session_id=session_id,
        usage=prior_usage,
    )
    clock = FakeClock(_TEST_NOW)
    sequence = MuseTurnSequence(
        args=_EXTRA_ARGS,
        base_environment=base_environment if base_environment is not None else dict(os.environ),
        clock=clock,
        reset_time=ResetTimeParser(clock),
        user_home=tmp_path / "home",
        context_window_tokens=_CONTEXT_WINDOW_TOKENS,
        compact_at_pct=compact_at_pct,
    )
    turn = await StreamingTurn.start(spec, SubprocessRunner(), sequence)
    async with asyncio.timeout(_TURN_TIMEOUT_SECONDS):
        signals = [signal async for signal in turn.signals()]
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    return recorded, signals


def _expected_argv(tmp_path: Path, flags: tuple[str, ...]) -> list[str]:
    return [
        EXEC_SUBCOMMAND,
        JSON_FLAG,
        *flags,
        SOFT_THRESHOLD_FLAG,
        _SOFT_THRESHOLD,
        HARD_THRESHOLD_FLAG,
        _HARD_THRESHOLD,
        *_EXTRA_ARGS,
        PROMPT_FILE_FLAG,
        str(tmp_path / "turn" / PROMPT_FILENAME),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rung", "session_id", "flags"),
    [
        (Rung(agent=_AGENT, model=RUNTIME_DEFAULT_MODEL), None, ()),
        (
            Rung(agent=_AGENT, model=_MODEL, effort=_EFFORT),
            _SESSION_ID,
            (SESSION_ID_FLAG, _SESSION_ID, MODEL_FLAG, _MODEL, REASONING_EFFORT_FLAG, _EFFORT),
        ),
    ],
)
async def test_turn_builds_exec_json_argv_from_session_model_and_effort(
    tmp_path: Path, rung: Rung, session_id: str | None, flags: tuple[str, ...]
) -> None:
    calls, _ = await _run_turn(tmp_path, [_success()], rung=rung, session_id=session_id)

    assert calls[0]["argv"] == _expected_argv(tmp_path, flags)


@pytest.mark.asyncio
async def test_turn_forces_auto_update_off_over_base_and_turn_environment(tmp_path: Path) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        base_environment={**os.environ, NO_AUTO_UPDATE_ENV_VAR: "0"},
        spec_environment={NO_AUTO_UPDATE_ENV_VAR: "0"},
    )

    assert calls[0]["auto_update"] == NO_AUTO_UPDATE_VALUE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "expected_calls", "expects_failure"),
    [
        ([_context_failure(), _success()], 2, False),
        ([_auth_failure()], 1, True),
        ([_context_failure(), _context_failure()], 2, True),
    ],
)
async def test_turn_retries_once_only_after_a_context_failure(
    tmp_path: Path,
    responses: list[_Response],
    expected_calls: int,
    expects_failure: bool,
) -> None:
    calls, signals = await _run_turn(tmp_path, responses, rung=Rung(agent=_AGENT, model=_MODEL))

    assert len(calls) == expected_calls
    assert any(isinstance(signal, TurnFailed) for signal in signals) is expects_failure


@pytest.mark.asyncio
async def test_context_retry_resumes_the_session_the_failed_process_started(tmp_path: Path) -> None:
    calls, _ = await _run_turn(
        tmp_path, [_context_failure(), _success()], rung=Rung(agent=_AGENT, model=_MODEL)
    )

    assert calls[0]["argv"] == _expected_argv(tmp_path, (MODEL_FLAG, _MODEL))
    assert calls[1]["argv"] == _expected_argv(
        tmp_path, (SESSION_ID_FLAG, _SESSION_ID, MODEL_FLAG, _MODEL)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_prompt", "expected_prompt"),
    [(None, _SPEC_PROMPT), ("engine prompt", "engine prompt")],
)
async def test_turn_prompt_file_is_private_and_keeps_an_engine_written_prompt(
    tmp_path: Path, engine_prompt: str | None, expected_prompt: str
) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        turn_prompt=engine_prompt,
    )

    assert calls[0]["prompt"] == expected_prompt
    assert calls[0]["prompt_mode"] == PRIVATE_FILE_MODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compact_at_pct", "soft", "hard"),
    [
        (0, "0.0", "0.005"),
        (DEFAULT_COMPACT_AT_PCT, _SOFT_THRESHOLD, _HARD_THRESHOLD),
        (MAX_COMPACT_AT_PCT, "0.99", "0.995"),
    ],
)
async def test_compaction_thresholds_keep_hard_above_soft_across_the_supported_range(
    tmp_path: Path, compact_at_pct: float, soft: str, hard: str
) -> None:
    calls, _ = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        compact_at_pct=compact_at_pct,
    )

    argv = calls[0]["argv"]
    assert argv[argv.index(SOFT_THRESHOLD_FLAG) + 1] == soft
    assert argv[argv.index(HARD_THRESHOLD_FLAG) + 1] == hard


@pytest.mark.asyncio
async def test_turn_reports_its_compaction_and_the_latest_prompt_estimate_as_context_usage(
    tmp_path: Path,
) -> None:
    response = _success((_compaction_line(800, 300), _model_line(200), _model_line(600)))

    _, signals = await _run_turn(tmp_path, [response], rung=Rung(agent=_AGENT, model=_MODEL))

    assert _compactions(signals) == [Compacted(pre_tokens=800, post_tokens=300)]
    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_context_retry_reports_only_the_compactions_each_process_installed(
    tmp_path: Path,
) -> None:
    responses = [
        _context_failure((_compaction_line(900, 400), _model_line(950))),
        _success((_compaction_line(700, 200), _model_line(300))),
    ]

    _, signals = await _run_turn(tmp_path, responses, rung=Rung(agent=_AGENT, model=_MODEL))

    assert _compactions(signals) == [
        Compacted(pre_tokens=900, post_tokens=400),
        Compacted(pre_tokens=700, post_tokens=200),
    ]
    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_context_usage_keeps_the_prior_share_when_the_process_made_no_model_call(
    tmp_path: Path,
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, 12.5)

    _, signals = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        prior_usage=prior,
    )

    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_malformed_session_log_keeps_the_successful_result(tmp_path: Path) -> None:
    _, signals = await _run_turn(
        tmp_path,
        [_success((_NOT_JSON_LINE,))],
        rung=Rung(agent=_AGENT, model=_MODEL),
    )

    assert len(_endings(signals)) == 1
    assert _failures(signals) == []
    assert len(_faults(signals)) == 1
    assert _compactions(signals) == []


@pytest.mark.asyncio
async def test_malformed_session_log_keeps_the_classified_failure(tmp_path: Path) -> None:
    _, signals = await _run_turn(
        tmp_path,
        [_failed(_UNAVAILABLE_REASON, (_NOT_JSON_LINE,))],
        rung=Rung(agent=_AGENT, model=_MODEL),
    )

    [failure] = _failures(signals)
    assert failure.kind is FailureKind.UNAVAILABLE
    assert failure.message == _UNAVAILABLE_REASON
    assert len(_faults(signals)) == 1


@pytest.mark.asyncio
async def test_unreadable_session_log_keeps_the_successful_result(tmp_path: Path) -> None:
    _, signals = await _run_turn(
        tmp_path,
        [_success((_NOT_UTF8_LINE,))],
        rung=Rung(agent=_AGENT, model=_MODEL),
    )

    assert len(_endings(signals)) == 1
    assert _failures(signals) == []
    assert len(_faults(signals)) == 1
    assert _compactions(signals) == []


@pytest.mark.asyncio
async def test_session_log_unreadable_before_the_turn_keeps_the_result_without_context(
    tmp_path: Path,
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, 12.5)
    unreadable_log = _session_log_path(tmp_path / "data")
    unreadable_log.parent.mkdir(parents=True)
    unreadable_log.write_bytes(_NOT_UTF8_BYTES)

    _, signals = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        prior_usage=prior,
    )

    assert len(_endings(signals)) == 1
    assert len(_faults(signals)) == 1
    assert _compactions(signals) == []
    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_missing_session_log_is_reported_without_inventing_compaction_or_context(
    tmp_path: Path,
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, 12.5)

    _, signals = await _run_turn(
        tmp_path,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        prior_usage=prior,
    )

    assert len(_endings(signals)) == 1
    assert len(_faults(signals)) == 1
    assert _compactions(signals) == []
    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_session_log_appearing_during_resumed_turn_is_not_attributed_to_that_turn(
    tmp_path: Path,
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, 12.5)

    _, signals = await _run_turn(
        tmp_path,
        [
            _success(
                (
                    _compaction_line(900, 400),
                    _model_line(950),
                    _compaction_line(700, 200),
                    _model_line(300),
                )
            )
        ],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        prior_usage=prior,
    )

    assert len(_endings(signals)) == 1
    assert len(_faults(signals)) == 1
    assert len(_recoveries(signals)) == 1
    assert _compactions(signals) == []
    assert _usage_reports(signals)[-1].context_used_pct == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_malformed_session_log_fault_recovers_only_after_it_becomes_usable(
    tmp_path: Path,
) -> None:
    prior = Usage(0, 0, 0, 0, None, COST_BASIS_UNKNOWN, 12.5)
    data_home = tmp_path / "data"
    session_log = _session_log_path(data_home)
    session_log.parent.mkdir(parents=True)
    session_log.write_text(
        _session_event(
            {
                EVENT_KIND_KEY: MODEL_COMPLETED_EVENT,
                MODEL_KEY: _MODEL,
                USAGE_KEY: {INPUT_TOKENS_KEY: 1},
            }
        ),
        encoding="utf-8",
    )
    malformed_turn_dir = tmp_path / "malformed"
    malformed_turn_dir.mkdir()

    _, malformed_signals = await _run_turn(
        malformed_turn_dir,
        [_success()],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        data_home=data_home,
        prior_usage=prior,
    )

    assert len(_faults(malformed_signals)) == 1
    assert _recoveries(malformed_signals) == []
    assert _compactions(malformed_signals) == []
    assert _usage_reports(malformed_signals)[-1].context_used_pct == pytest.approx(12.5)

    session_log.write_text(
        "\n".join((_compaction_line(900, 400), _model_line(950))) + "\n",
        encoding="utf-8",
    )
    usable_turn_dir = tmp_path / "usable"
    usable_turn_dir.mkdir()
    _, usable_signals = await _run_turn(
        usable_turn_dir,
        [_success((_compaction_line(700, 200), _model_line(300)))],
        rung=Rung(agent=_AGENT, model=_MODEL),
        session_id=_SESSION_ID,
        data_home=data_home,
        prior_usage=prior,
    )

    assert _faults(usable_signals) == []
    assert len(_recoveries(usable_signals)) == 1
    assert _compactions(usable_signals) == [Compacted(pre_tokens=700, post_tokens=200)]
    assert _usage_reports(usable_signals)[-1].context_used_pct == pytest.approx(30.0)
