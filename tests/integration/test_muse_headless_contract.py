"""Exercise Muse's headless contract with recorded `muse exec --json` turns and session logs."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from shikiru.agents.contracts import (
    COST_BASIS_UNKNOWN,
    QUOTA_WINDOW_FIVE_HOUR,
    QUOTA_WINDOW_SEVEN_DAY,
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    RUNTIME_DEFAULT_MODEL,
    AgentFactory,
    Compacted,
    EvidenceRecovered,
    FailureKind,
    Message,
    QuotaReported,
    QuotaWindow,
    Rung,
    SessionStarted,
    ToolDetailReported,
    ToolFinished,
    ToolOutcome,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.testing import (
    AgentContractSuite,
    CompactionCase,
    CompactionScenario,
    InvocationCase,
    RecordedStream,
    ReplayedProcess,
)
from shikiru.operator.probe import live_report_instructions
from shikiru.plugins.agents.muse.agent import MuseAgent, create
from shikiru.plugins.agents.muse.options import (
    DEFAULT_ARGS,
    DEFAULT_COMPACT_AT_PCT,
    EXEC_SUBCOMMAND,
    HARD_THRESHOLD_FLAG,
    JSON_FLAG,
    MODEL_FLAG,
    MUSE_COMMAND,
    MUSE_HOME_DIRECTORY,
    NO_AUTO_UPDATE_ENV_VAR,
    NO_AUTO_UPDATE_VALUE,
    PROMPT_FILE_FLAG,
    PROMPT_FILENAME,
    REASONING_EFFORT_FLAG,
    RESUME_SUBCOMMAND,
    SESSION_ID_FLAG,
    SESSION_LOG_FILENAME,
    SESSIONS_DIRECTORY,
    SOFT_THRESHOLD_FLAG,
    XDG_DATA_HOME_ENV_VAR,
)
from shikiru.plugins.agents.muse.session_log import NO_SESSION_LOG_MARK
from shikiru.plugins.agents.muse.turn import (
    ContextWindow,
    MuseTurnParser,
    SessionWatch,
    compaction_thresholds,
)
from tests.support.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.agents.contracts import Agent, AgentSignal
    from shikiru.agents.sdk.turn import TurnProcessSequence

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
_OFFLINE_HOME = _FIXTURES / "offline-home"
_COMPACTING_HOME = _FIXTURES / "compacting-home"
_EXCERPT_FIXTURE = _FIXTURES / "session_compaction.jsonl"
_HOME_KEY = "home"
_DATA_DIRECTORY = "data"
_SESSION_DATE = ("2026", "11", "12")
_NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
_MILLISECONDS = 1000

_SESSION_FIRST = "00000000-0000-7000-9000-000000000002"
_SESSION_TOOL = "00000000-0000-7000-9000-00000000005e"
_SESSION_HEADLESS = "00000000-0000-7000-9000-000000000090"
_SESSION_CONTEXT_FAILURE_LOG = _SESSION_FIRST

_EXIT_OK = 0
_EXIT_FAILED = 1
_TOOL_EXIT_CODE = 0
_ROLE = "implementer"
_PROMPT = "complete the requested turn"
_ASK_PROMPT = "Answer with the word OK as JSON."
_OTHER_MODEL = "muse-wonder-1.0"
_EFFORT = "high"
_USER_AUTO_UPDATE = "0"
_COMPACT_AT_PCT = 50.0
_LIVE_COMPACT_AT_PCT = 5.0
_LIVE_CHANGE_MODEL = "muse-spark-1.2"

_OK = "OK"
_TOOL_FIRST_DELTA = "01fa8a"
_TOOL_SECOND_DELTA = "2"
_TOOL_TEXT = "01fa8a2"
_TOOL_NAME = "bash"
_TOOL_COMMAND = "git rev-parse --short HEAD"
_HEADLESS_TEXT = '{"answer":"OK"}'
_AUTH_REASON = "your API key from META_API_KEY was rejected — update or unset it"
_UNKNOWN_MODEL_REASON = (
    "model `x` does not exist or you lack access [request_id=00000000-0000-7000-9000-000000000019]"
)
_UNMAPPED_REASON = "stream reset by peer"
_CONTEXT_REASON = "context window exceeded"
_COMPACTION_PRE_TOKENS = 44662
_COMPACTION_POST_TOKENS = 25159

_FIVE_HOUR_RESET_MS = 1794514840000
_SEVEN_DAY_RESET_MS = 1794787200000
_FIRST_AS_OF_MS = 1794502385942
_RESUME_AS_OF_MS = 1794502401623
_TOOL_AS_OF_MS = 1794502411139
_HEADLESS_AS_OF_MS = 1794502433020
_UNKNOWN_MODEL_AS_OF_MS = 1794502583072


class _Answer(BaseModel):
    answer: str


def _instant(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / _MILLISECONDS, tz=UTC)


def _quota(five_hour_used: float, weekly_used: float, as_of_ms: int) -> QuotaReported:
    observed = _instant(as_of_ms)
    return QuotaReported(
        (
            QuotaWindow(
                QUOTA_WINDOW_FIVE_HOUR, five_hour_used, _instant(_FIVE_HOUR_RESET_MS), observed
            ),
            QuotaWindow(
                QUOTA_WINDOW_SEVEN_DAY, weekly_used, _instant(_SEVEN_DAY_RESET_MS), observed
            ),
        )
    )


def _usage(input_tokens: int, output_tokens: int, cache_read_tokens: int) -> UsageReported:
    return UsageReported(
        Usage(input_tokens, output_tokens, cache_read_tokens, 0, None, COST_BASIS_UNKNOWN, None)
    )


def _session_log(data_home: Path, session_id: str) -> Path:
    return data_home.joinpath(
        MUSE_HOME_DIRECTORY,
        SESSIONS_DIRECTORY,
        *_SESSION_DATE,
        session_id,
        SESSION_LOG_FILENAME,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _excerpt() -> str:
    return _EXCERPT_FIXTURE.read_text(encoding="utf-8")


def _excerpt_twice() -> str:
    return "\n".join(_excerpt().splitlines() * 2) + "\n"


def _exec_argv(
    turn_dir: Path,
    *,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    compact_at_pct: float = DEFAULT_COMPACT_AT_PCT,
) -> tuple[str, ...]:
    soft, hard = compaction_thresholds(compact_at_pct)
    argv: list[str] = [MUSE_COMMAND, EXEC_SUBCOMMAND, JSON_FLAG]
    if session_id is not None:
        argv.extend((SESSION_ID_FLAG, session_id))
    if model is not None:
        argv.extend((MODEL_FLAG, model))
    if effort is not None:
        argv.extend((REASONING_EFFORT_FLAG, effort))
    argv.extend((SOFT_THRESHOLD_FLAG, str(soft), HARD_THRESHOLD_FLAG, str(hard)))
    argv.extend(DEFAULT_ARGS)
    argv.extend((PROMPT_FILE_FLAG, str(turn_dir / PROMPT_FILENAME)))
    return tuple(argv)


def _stream(
    stem: str,
    *,
    session_id: str | None,
    expected: Sequence[AgentSignal] = (),
    exit_code: int = _EXIT_OK,
    session_home: Path | None = None,
    label: str | None = None,
) -> RecordedStream:
    return RecordedStream(
        name=label or stem,
        stdout=_FIXTURES / f"{stem}.jsonl",
        stderr=_FIXTURES / f"{stem}.stderr.log",
        exit_code=exit_code,
        expected_signals=list(expected),
        session_id=session_id,
        session_logs={} if session_home is None else {_HOME_KEY: session_home},
    )


class TestMuseHeadlessContract(AgentContractSuite):
    """Exercise Muse's process-per-turn contract, with recorded turns and session logs."""

    runs_live_checks_in_default_gate = False

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> Mapping[str, Any]:
        return {}

    def build_spec(
        self,
        tmp_path: Path,
        *,
        resume_session_id: str | None,
        usage: Any = None,
        model: str | None = None,
    ) -> TurnSpec:
        repo = tmp_path / "repo"
        run_dir = tmp_path / "run"
        for directory in (repo, run_dir, tmp_path):
            directory.mkdir(parents=True, exist_ok=True)
        turn_dir = Path(tempfile.mkdtemp(dir=tmp_path, prefix="turn-"))
        return TurnSpec(
            rung=Rung(agent=MUSE_COMMAND, model=model or RUNTIME_DEFAULT_MODEL),
            role=_ROLE,
            prompt=_PROMPT,
            cwd=repo,
            run_dir=run_dir,
            turn_dir=turn_dir,
            add_dirs=(),
            env={
                XDG_DATA_HOME_ENV_VAR: str(tmp_path / _DATA_DIRECTORY),
                RUN_DIR_ENV_VAR: str(run_dir),
                ROLE_ENV_VAR: _ROLE,
            },
            session_id=resume_session_id,
            usage=usage,
        )

    def parser_for(self, sample: RecordedStream) -> MuseTurnParser:
        clock = FakeClock(_NOW)
        home = sample.session_logs.get(_HOME_KEY, _OFFLINE_HOME)
        watch = SessionWatch(
            home=home,
            session_id=sample.session_id,
            context_window=ContextWindow(tokens=None),
            mark=NO_SESSION_LOG_MARK,
        )
        return MuseTurnParser(clock, ResetTimeParser(clock), watch)

    def recorded_streams(self) -> Sequence[RecordedStream]:
        return [
            _stream(
                "turn_first",
                session_id=_SESSION_FIRST,
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    Message(_OK),
                    _quota(8, 2, _FIRST_AS_OF_MS),
                    EvidenceRecovered(),
                    _usage(32_358, 18, 0),
                    TurnEnded(_OK),
                ],
            ),
            _stream(
                "turn_first",
                session_id=_SESSION_FIRST,
                session_home=_COMPACTING_HOME,
                label="turn_first with a session compaction",
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    Message(_OK),
                    _quota(8, 2, _FIRST_AS_OF_MS),
                    EvidenceRecovered(),
                    Compacted(_COMPACTION_PRE_TOKENS, _COMPACTION_POST_TOKENS),
                    _usage(32_358, 18, 0),
                    TurnEnded(_OK),
                ],
            ),
            _stream(
                "turn_resume",
                session_id=_SESSION_FIRST,
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    Message(_OK),
                    _quota(9, 3, _RESUME_AS_OF_MS),
                    EvidenceRecovered(),
                    _usage(32_415, 12, 16_244),
                    TurnEnded(_OK),
                ],
            ),
            _stream(
                "turn_tool",
                session_id=_SESSION_TOOL,
                expected=[
                    SessionStarted(_SESSION_TOOL),
                    ToolStarted(_TOOL_NAME, ""),
                    ToolDetailReported(_TOOL_NAME, _TOOL_COMMAND),
                    ToolFinished(_TOOL_NAME, ToolOutcome.SUCCESS, _TOOL_EXIT_CODE),
                    Message(_TOOL_FIRST_DELTA),
                    Message(_TOOL_SECOND_DELTA),
                    _quota(9, 3, _TOOL_AS_OF_MS),
                    EvidenceRecovered(),
                    _usage(64_953, 151, 16_244),
                    TurnEnded(_TOOL_TEXT),
                ],
            ),
            _stream(
                "turn_headless_result",
                session_id=_SESSION_HEADLESS,
                expected=[
                    SessionStarted(_SESSION_HEADLESS),
                    Message(_HEADLESS_TEXT),
                    _quota(9, 3, _HEADLESS_AS_OF_MS),
                    EvidenceRecovered(),
                    _usage(27_472, 48, 0),
                    TurnEnded(_HEADLESS_TEXT),
                ],
            ),
        ]

    def error_streams(self) -> Sequence[RecordedStream]:
        return [
            _stream(
                "turn_auth_rejected",
                session_id=_SESSION_FIRST,
                exit_code=_EXIT_FAILED,
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    EvidenceRecovered(),
                    _usage(0, 0, 0),
                    TurnFailed(FailureKind.AUTH, _AUTH_REASON, None),
                ],
            ),
            _stream(
                "turn_unknown_model",
                session_id=_SESSION_FIRST,
                exit_code=_EXIT_FAILED,
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    _quota(10, 3, _UNKNOWN_MODEL_AS_OF_MS),
                    EvidenceRecovered(),
                    _usage(0, 0, 0),
                    TurnFailed(FailureKind.OTHER, _UNKNOWN_MODEL_REASON, None),
                ],
            ),
            _stream(
                "turn_unmapped_failure",
                session_id=_SESSION_FIRST,
                exit_code=_EXIT_FAILED,
                expected=[
                    SessionStarted(_SESSION_FIRST),
                    EvidenceRecovered(),
                    _usage(0, 0, 0),
                    TurnFailed(FailureKind.UNKNOWN, _UNMAPPED_REASON, None),
                ],
            ),
        ]

    def invocation_cases(self, tmp_path: Path) -> Sequence[InvocationCase]:
        new = self.build_spec(tmp_path / "new", resume_session_id=None)
        resumed = self.build_spec(tmp_path / "resumed", resume_session_id=_SESSION_FIRST)
        changed = replace(
            self.build_spec(tmp_path / "changed", resume_session_id=_SESSION_FIRST),
            rung=Rung(agent=MUSE_COMMAND, model=_OTHER_MODEL, effort=_EFFORT),
        )
        environment = self.build_spec(tmp_path / "environment", resume_session_id=None)
        environment = replace(
            environment, env={**environment.env, NO_AUTO_UPDATE_ENV_VAR: _USER_AUTO_UPDATE}
        )
        default_env = {NO_AUTO_UPDATE_ENV_VAR: NO_AUTO_UPDATE_VALUE}
        return [
            InvocationCase(
                name="a new turn starts a session on the runtime default model",
                options={},
                spec=new,
                expected_argv=[_exec_argv(new.turn_dir)],
                expected_env=[default_env],
                outcome_streams=[_stream("turn_first", session_id=_SESSION_FIRST)],
                expected_final_signals=[SessionStarted(_SESSION_FIRST), TurnEnded(_OK)],
            ),
            InvocationCase(
                name="a resumed turn passes its session id",
                options={},
                spec=resumed,
                expected_argv=[_exec_argv(resumed.turn_dir, session_id=_SESSION_FIRST)],
                expected_env=[default_env],
                outcome_streams=[_stream("turn_resume", session_id=_SESSION_FIRST)],
                expected_final_signals=[SessionStarted(_SESSION_FIRST), TurnEnded(_OK)],
            ),
            InvocationCase(
                name="a model and effort change applies to the resumed session",
                options={},
                spec=changed,
                expected_argv=[
                    _exec_argv(
                        changed.turn_dir,
                        session_id=_SESSION_FIRST,
                        model=_OTHER_MODEL,
                        effort=_EFFORT,
                    )
                ],
                expected_env=[default_env],
                outcome_streams=[_stream("turn_resume", session_id=_SESSION_FIRST)],
                expected_final_signals=[SessionStarted(_SESSION_FIRST), TurnEnded(_OK)],
                interactive_session_id=_SESSION_FIRST,
                expected_interactive_command=(
                    MUSE_COMMAND,
                    RESUME_SUBCOMMAND,
                    _SESSION_FIRST,
                    MODEL_FLAG,
                    _OTHER_MODEL,
                    REASONING_EFFORT_FLAG,
                    _EFFORT,
                    *DEFAULT_ARGS,
                ),
            ),
            InvocationCase(
                name="the environment keeps auto-update off and the session home",
                options={},
                spec=environment,
                expected_argv=[_exec_argv(environment.turn_dir)],
                expected_env=[
                    {
                        **default_env,
                        XDG_DATA_HOME_ENV_VAR: environment.env[XDG_DATA_HOME_ENV_VAR],
                    }
                ],
                outcome_streams=[_stream("turn_first", session_id=_SESSION_FIRST)],
                expected_final_signals=[SessionStarted(_SESSION_FIRST), TurnEnded(_OK)],
            ),
        ]

    def process_sequence(self, agent: Agent, spec: TurnSpec) -> TurnProcessSequence:
        return cast("MuseAgent", agent).process_sequence(spec, ContextWindow(tokens=None))

    def compaction_scenarios(self, tmp_path: Path) -> Sequence[CompactionScenario]:
        options = {"compact_at_pct": _COMPACT_AT_PCT}
        threshold_dir = tmp_path / "threshold"
        threshold = self.build_spec(threshold_dir, resume_session_id=None)
        threshold_log = _session_log(threshold_dir / _DATA_DIRECTORY, _SESSION_FIRST)
        recovery_dir = tmp_path / "recovery"
        recovery = self.build_spec(recovery_dir, resume_session_id=None)
        recovery_log = _session_log(recovery_dir / _DATA_DIRECTORY, _SESSION_CONTEXT_FAILURE_LOG)
        _write(recovery_log, "")
        failed_dir = tmp_path / "failed"
        failed = self.build_spec(failed_dir, resume_session_id=None)
        failed_log = _session_log(failed_dir / _DATA_DIRECTORY, _SESSION_CONTEXT_FAILURE_LOG)
        _write(failed_log, "")
        return [
            CompactionScenario(
                name="the session compacts twice within one turn",
                case=CompactionCase.THRESHOLD,
                options=options,
                spec=threshold,
                processes=[
                    ReplayedProcess(
                        _stream("turn_first", session_id=_SESSION_FIRST),
                        {threshold_log: _excerpt_twice()},
                    )
                ],
                expected_argv=[_exec_argv(threshold.turn_dir, compact_at_pct=_COMPACT_AT_PCT)],
            ),
            CompactionScenario(
                name="a context failure reruns the prompt on the session, compacting first",
                case=CompactionCase.CONTEXT_FAILURE,
                options=options,
                spec=recovery,
                processes=[
                    ReplayedProcess(
                        _stream(
                            "turn_context_failure",
                            session_id=_SESSION_FIRST,
                            exit_code=_EXIT_FAILED,
                        )
                    ),
                    ReplayedProcess(
                        _stream("turn_resume", session_id=_SESSION_FIRST),
                        {recovery_log: _excerpt()},
                    ),
                ],
                expected_argv=[
                    _exec_argv(recovery.turn_dir, compact_at_pct=_COMPACT_AT_PCT),
                    _exec_argv(
                        recovery.turn_dir,
                        session_id=_SESSION_FIRST,
                        compact_at_pct=_COMPACT_AT_PCT,
                    ),
                ],
            ),
            CompactionScenario(
                name="a failure after the context retry ends the turn",
                case=CompactionCase.FAILED_RECOVERY,
                options=options,
                spec=failed,
                processes=[
                    ReplayedProcess(
                        _stream(
                            "turn_context_failure",
                            session_id=_SESSION_FIRST,
                            exit_code=_EXIT_FAILED,
                        )
                    ),
                    ReplayedProcess(
                        _stream(
                            "turn_unknown_model",
                            session_id=_SESSION_FIRST,
                            exit_code=_EXIT_FAILED,
                        )
                    ),
                ],
                expected_argv=[
                    _exec_argv(failed.turn_dir, compact_at_pct=_COMPACT_AT_PCT),
                    _exec_argv(
                        failed.turn_dir,
                        session_id=_SESSION_FIRST,
                        compact_at_pct=_COMPACT_AT_PCT,
                    ),
                ],
            ),
        ]

    def live_compaction_options(self) -> Mapping[str, Any]:
        return {"compact_at_pct": _LIVE_COMPACT_AT_PCT}

    def live_change_model(self, first_model: str) -> str:
        del first_model
        return _LIVE_CHANGE_MODEL

    def live_report_command_is_signalled(self) -> bool:
        """Muse names a tool only as `bash` when it starts, so the report is checked by file."""
        return False

    def live_report_turn_prompt(self, spec: TurnSpec, prompt_path: Path) -> str:
        """Muse reads a prompt that only names a workspace file as untrusted, so inline it."""
        del prompt_path
        return live_report_instructions(spec.run_dir)

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _Answer

    def headless_ask_model(self) -> str:
        return RUNTIME_DEFAULT_MODEL

    def headless_ask_prompt(self) -> str:
        return _ASK_PROMPT
