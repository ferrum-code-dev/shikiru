"""Headless per-turn API implementation for the test fake agent."""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from fake_agent.headless import FakeHeadlessAgent
from shikiru.agents.contracts import (
    AgentSignal,
    Compacted,
    FailureKind,
    Message,
    QuotaReported,
    QuotaWindow,
    SessionStarted,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnFailed,
    TurnSpec,
    Usage,
    UsageReported,
)
from shikiru.agents.sdk.turn import (
    StreamingTurn,
    TurnProcess,
    TurnProcessResult,
    TurnProcessSequence,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from shikiru.process import SubprocessRunner

FAKE_SESSION_ID_ENV_VAR = "SHIKIRU_FAKE_SESSION_ID"
FAKE_COMPACT_AT_PCT_ENV_VAR = "SHIKIRU_FAKE_COMPACT_AT_PCT"
_CLI_MODULE = "fake_agent.turn_cli"
_TURN_SUBCOMMAND = "turn"
_COMPACT_SUBCOMMAND = "compact"
_SESSION_ARGUMENT = "--session-id"
_PROMPT_ARGUMENT = "--prompt"
_MODEL_ARGUMENT = "--model"
_EFFORT_ARGUMENT = "--effort"
INITIAL_CONTEXT_USAGE_PERCENT = 40.0
WORK_CONTEXT_USAGE_PERCENT = 20.0
COMPACTED_CONTEXT_USAGE_PERCENT = 10.0
CONTINUE_PROMPT = "Continue the task exactly where you stopped."
COMPACTION_PRE_TOKENS = 2000
COMPACTION_POST_TOKENS = 400
INPUT_TOKENS_PER_TURN = 120
OUTPUT_TOKENS_PER_TURN = 60
CACHE_READ_TOKENS_PER_TURN = 20
CACHE_WRITE_TOKENS_PER_TURN = 10
_STDERR_LINE_ENDINGS = "\r\n"
CONTEXT_EXCEEDED_EVENT = "context_exceeded"
THRESHOLD_REACHED_EVENT = "threshold_reached"
_COMPACTION_FAILED_MESSAGE = "fake compaction did not compact"


@dataclass
class FakeTurnParser:
    """Parse the fake CLI's JSONL signal stream; `compact` marks a compaction process."""

    compact: bool = False
    context_exceeded: bool = False
    threshold_reached: bool = False
    compacted: bool = False

    def feed(self, line: str) -> list[AgentSignal]:
        entry = json.loads(line)
        event = entry["event"]
        if event == CONTEXT_EXCEEDED_EVENT:
            self.context_exceeded = True
            return [TurnFailed(FailureKind.OTHER, entry["message"], None)]
        if event == THRESHOLD_REACHED_EVENT:
            self.threshold_reached = True
            return []
        builder = _SIGNAL_BUILDERS.get(event)
        if builder is None:
            message = f"unrecognized fake stream event {event!r}"
            raise ValueError(message)
        signal = builder(entry)
        self.compacted |= isinstance(signal, Compacted)
        return [signal]

    def finish(self, exit_code: int, stderr: str) -> list[AgentSignal]:
        if exit_code != 0:
            return [
                TurnFailed(
                    kind=FailureKind.UNKNOWN,
                    message=f"fake CLI exited {exit_code}: {stderr.rstrip(_STDERR_LINE_ENDINGS)}",
                    resets_at=None,
                )
            ]
        if self.compact and not self.compacted:
            return [TurnFailed(FailureKind.OTHER, _COMPACTION_FAILED_MESSAGE, None)]
        return []


def _session_started(entry: Mapping[str, Any]) -> AgentSignal:
    return SessionStarted(entry["session_id"])


def _message(entry: Mapping[str, Any]) -> AgentSignal:
    return Message(entry["text"])


def _tool_started(entry: Mapping[str, Any]) -> AgentSignal:
    return ToolStarted(entry["name"], entry["detail"])


def _tool_finished(entry: Mapping[str, Any]) -> AgentSignal:
    return ToolFinished(entry["name"])


def _usage_reported(entry: Mapping[str, Any]) -> AgentSignal:
    return UsageReported(Usage(**entry["usage"]))


def _quota_reported(entry: Mapping[str, Any]) -> AgentSignal:
    windows = tuple(
        QuotaWindow(
            name=window["name"],
            used_pct=window["used_pct"],
            resets_at=(
                datetime.fromisoformat(window["resets_at"]) if window["resets_at"] else None
            ),
            observed_at=datetime.fromisoformat(window["observed_at"]),
        )
        for window in entry["windows"]
    )
    return QuotaReported(windows)


def _compacted(entry: Mapping[str, Any]) -> AgentSignal:
    return Compacted(entry["pre_tokens"], entry["post_tokens"])


def _turn_ended(entry: Mapping[str, Any]) -> AgentSignal:
    return TurnEnded(entry["last_message"])


def _turn_failed(entry: Mapping[str, Any]) -> AgentSignal:
    resets_at = datetime.fromisoformat(entry["resets_at"]) if entry.get("resets_at") else None
    return TurnFailed(FailureKind(entry["kind"]), entry["message"], resets_at)


_SIGNAL_BUILDERS: dict[str, Callable[[Mapping[str, Any]], AgentSignal]] = {
    "session_started": _session_started,
    "message": _message,
    "tool_started": _tool_started,
    "tool_finished": _tool_finished,
    "usage_reported": _usage_reported,
    "quota_reported": _quota_reported,
    "compacted": _compacted,
    "turn_ended": _turn_ended,
    "turn_failed": _turn_failed,
}


class FakeTurnSequence(TurnProcessSequence):
    """Build one fake turn's prompt, compaction and continue processes (obligation 8)."""

    def __init__(
        self, spec: TurnSpec, *, compact_at_pct: float, new_session_id: str | None
    ) -> None:
        self._session_id = spec.session_id or new_session_id or str(uuid.uuid4())
        self._compact_at_pct = compact_at_pct
        self._base_env = {**os.environ, **spec.env, FAKE_SESSION_ID_ENV_VAR: self._session_id}
        self._parser = FakeTurnParser()
        self._then: str | None = None
        self._recovered = False

    def start(self, spec: TurnSpec) -> TurnProcess:
        return self._prompt_process(spec, spec.prompt)

    def advance(self, spec: TurnSpec, previous: TurnProcessResult) -> TurnProcess | None:
        del previous
        if self._then is not None:
            prompt, self._then = self._then, None
            return self._prompt_process(spec, prompt) if self._parser.compacted else None
        if self._parser.threshold_reached:
            return self._compact_process(spec, then=CONTINUE_PROMPT)
        if self._parser.context_exceeded and not self._recovered:
            self._recovered = True
            return self._compact_process(spec, then=spec.prompt)
        return None

    def _prompt_process(self, spec: TurnSpec, prompt: str) -> TurnProcess:
        argv = [
            sys.executable,
            "-m",
            _CLI_MODULE,
            _TURN_SUBCOMMAND,
            _SESSION_ARGUMENT,
            self._session_id,
            _PROMPT_ARGUMENT,
            prompt,
            _MODEL_ARGUMENT,
            spec.rung.model,
        ]
        if spec.rung.effort is not None:
            argv.extend((_EFFORT_ARGUMENT, spec.rung.effort))
        environment = {**self._base_env, FAKE_COMPACT_AT_PCT_ENV_VAR: str(self._compact_at_pct)}
        self._parser = FakeTurnParser()
        return TurnProcess(argv=tuple(argv), cwd=spec.cwd, env=environment, parser=self._parser)

    def _compact_process(self, spec: TurnSpec, *, then: str) -> TurnProcess:
        self._then = then
        argv = (
            sys.executable,
            "-m",
            _CLI_MODULE,
            _COMPACT_SUBCOMMAND,
            _SESSION_ARGUMENT,
            self._session_id,
        )
        self._parser = FakeTurnParser(compact=True)
        return TurnProcess(argv=argv, cwd=spec.cwd, env=self._base_env, parser=self._parser)


class FakeHeadlessTurnAgent:
    """The headless side of the test fake agent's dual compatibility plugin."""

    name = "fake"

    def __init__(
        self,
        runner: SubprocessRunner,
        asks: Sequence[Mapping[str, Any]],
        compact_at_pct: float,
        new_session_id: str | None,
    ) -> None:
        self._runner = runner
        self._asks = list(asks)
        self._compact_at_pct = compact_at_pct
        self._new_session_id = new_session_id

    async def start_turn(self, spec: TurnSpec) -> StreamingTurn:
        return await StreamingTurn.start(spec, self._runner, self.process_sequence(spec))

    def process_sequence(self, spec: TurnSpec) -> FakeTurnSequence:
        return FakeTurnSequence(
            spec,
            compact_at_pct=self._compact_at_pct,
            new_session_id=self._new_session_id,
        )

    def interactive_command(self, session_id: str, run_dir: Path) -> tuple[str, ...]:
        del run_dir
        return (
            sys.executable,
            "-m",
            _CLI_MODULE,
            "interactive",
            _SESSION_ARGUMENT,
            session_id,
        )

    def headless(self) -> FakeHeadlessAgent:
        return FakeHeadlessAgent(self._asks)
