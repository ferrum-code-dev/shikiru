"""A one-process fake CLI for each headless agent turn."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING, TypedDict, cast

from fake_agent.repl import (
    build_context,
    save_script_index,
    script_index,
    script_raw_for,
    submit_scripted_report,
)
from fake_agent.script import (
    CrashStep,
    FailStep,
    NoReportStep,
    Script,
    StallStep,
)
from fake_agent.turns import (
    CACHE_READ_TOKENS_PER_TURN,
    CACHE_WRITE_TOKENS_PER_TURN,
    COMPACTED_CONTEXT_USAGE_PERCENT,
    COMPACTION_POST_TOKENS,
    COMPACTION_PRE_TOKENS,
    CONTEXT_USAGE_PERCENT,
    FAKE_SESSION_ID_ENV_VAR,
    INPUT_TOKENS_PER_TURN,
    OUTPUT_TOKENS_PER_TURN,
)
from shikiru.agents.contracts import role_dir
from shikiru.agents.testing.resume import RESUME_RECALL_PROMPT
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fake_agent.repl import ReplContext

_EVENT_SESSION_STARTED = "session_started"
_EVENT_MESSAGE = "message"
_EVENT_TOOL_STARTED = "tool_started"
_EVENT_TOOL_FINISHED = "tool_finished"
_EVENT_USAGE_REPORTED = "usage_reported"
_EVENT_COMPACTED = "compacted"
_EVENT_TURN_ENDED = "turn_ended"
_EVENT_TURN_FAILED = "turn_failed"
_REPORT_TOOL_NAME = "shikiru report"
_REPORT_TOOL_DETAIL = "submitting the turn report"
_SESSION_ID_ARGUMENT = "--session-id"
_PROMPT_ARGUMENT = "--prompt"
_MODEL_ARGUMENT = "--model"
_EFFORT_ARGUMENT = "--effort"
_FAKE_RETRY_ENV_VAR = "SHIKIRU_FAKE_RETRY"
_WAIT_PROMPT = "wait for the next instruction"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cache_read_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_write_tokens"
_COST_USD_FIELD = "cost_usd"
_COST_BASIS_FIELD = "cost_basis"
_CONTEXT_USED_PCT_FIELD = "context_used_pct"


class _SessionState(TypedDict):
    prompts: list[str]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    context_used_pct: float


def _emit(entry: Mapping[str, object]) -> None:
    line = json.dumps(entry, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _state(context: ReplContext) -> _SessionState:
    if not context.state_path.is_file():
        return {
            "prompts": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "context_used_pct": CONTEXT_USAGE_PERCENT,
        }
    return cast("_SessionState", json.loads(context.state_path.read_text()))


def _save_state(context: ReplContext, state: _SessionState) -> None:
    context.state_path.write_text(json.dumps(state))


def _session_started(session_id: str) -> None:
    _emit({"event": _EVENT_SESSION_STARTED, "session_id": session_id})


def _failure(step: FailStep) -> None:
    _emit(
        {
            "event": _EVENT_TURN_FAILED,
            "kind": step.failure_kind.value,
            "message": f"scripted {step.failure_kind.value} failure",
        }
    )


async def _run_turn(session_id: str, prompt: str) -> int:
    context = build_context()
    state = _state(context)
    _session_started(session_id)
    if prompt == _WAIT_PROMPT:
        await asyncio.Event().wait()
    prompts: list[str] = list(state["prompts"])
    response: str = (
        prompts[0] if prompt == RESUME_RECALL_PROMPT and prompts else f"accepted: {prompt}"
    )
    prompts.append(prompt)
    state["prompts"] = prompts
    _save_state(context, state)
    script = Script.from_env(
        script_raw_for(context.role), start_index=script_index(context.script_path)
    )
    step = script.next_step()
    save_script_index(context.script_path, script.index)
    if isinstance(step, CrashStep):
        return 1
    if isinstance(step, StallStep):
        await asyncio.Event().wait()
    if isinstance(step, FailStep):
        _failure(step)
        return 0
    if isinstance(step, NoReportStep):
        response = f"no report: {prompt}"
    _emit({"event": _EVENT_MESSAGE, "text": response})
    role_path = role_dir(context.run_dir, context.role)
    current_turn = role_path / CURRENT_TURN_LINK_NAME
    if current_turn.is_symlink():
        _emit(
            {
                "event": _EVENT_TOOL_STARTED,
                "name": _REPORT_TOOL_NAME,
                "detail": _REPORT_TOOL_DETAIL,
            }
        )
        await submit_scripted_report(context, step, prompt)
        _emit({"event": _EVENT_TOOL_FINISHED, "name": _REPORT_TOOL_NAME})
    state[_INPUT_TOKENS_FIELD] = int(state[_INPUT_TOKENS_FIELD]) + INPUT_TOKENS_PER_TURN
    state[_OUTPUT_TOKENS_FIELD] = int(state[_OUTPUT_TOKENS_FIELD]) + OUTPUT_TOKENS_PER_TURN
    state[_CACHE_READ_TOKENS_FIELD] = (
        int(state[_CACHE_READ_TOKENS_FIELD]) + CACHE_READ_TOKENS_PER_TURN
    )
    state[_CACHE_WRITE_TOKENS_FIELD] = (
        int(state[_CACHE_WRITE_TOKENS_FIELD]) + CACHE_WRITE_TOKENS_PER_TURN
    )
    _save_state(context, state)
    _emit(
        {
            "event": _EVENT_USAGE_REPORTED,
            "usage": {
                _INPUT_TOKENS_FIELD: state[_INPUT_TOKENS_FIELD],
                _OUTPUT_TOKENS_FIELD: state[_OUTPUT_TOKENS_FIELD],
                _CACHE_READ_TOKENS_FIELD: state[_CACHE_READ_TOKENS_FIELD],
                _CACHE_WRITE_TOKENS_FIELD: state[_CACHE_WRITE_TOKENS_FIELD],
                _COST_USD_FIELD: None,
                _COST_BASIS_FIELD: "unknown",
                _CONTEXT_USED_PCT_FIELD: state["context_used_pct"],
            },
        }
    )
    _emit({"event": _EVENT_TURN_ENDED, "last_message": response})
    return 0


async def _run_compaction(session_id: str) -> int:
    context = build_context()
    state = _state(context)
    state["context_used_pct"] = COMPACTED_CONTEXT_USAGE_PERCENT
    _save_state(context, state)
    _session_started(session_id)
    _emit(
        {
            "event": _EVENT_COMPACTED,
            "pre_tokens": COMPACTION_PRE_TOKENS,
            "post_tokens": COMPACTION_POST_TOKENS,
        }
    )
    _emit({"event": _EVENT_TURN_ENDED, "last_message": "context compacted"})
    return 0


async def _run_interactive(session_id: str) -> None:
    while line := sys.stdin.readline():
        prompt = line.rstrip("\n")
        if prompt == "/exit":
            return
        await _run_turn(session_id, prompt)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("turn", "compact", "interactive"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument(_SESSION_ID_ARGUMENT, required=True)
        if name == "turn":
            command_parser.add_argument(_PROMPT_ARGUMENT, required=True)
            command_parser.add_argument(_MODEL_ARGUMENT, required=True)
            command_parser.add_argument(_EFFORT_ARGUMENT)
    return parser


def main() -> None:
    """Run one headless turn, one compaction, or the interactive take-over REPL."""
    args = _parser().parse_args()
    os.environ[FAKE_SESSION_ID_ENV_VAR] = args.session_id
    if args.command == "turn":
        sys.exit(asyncio.run(_run_turn(args.session_id, args.prompt)))
    if args.command == "compact":
        sys.exit(asyncio.run(_run_compaction(args.session_id)))
    asyncio.run(_run_interactive(args.session_id))


if __name__ == "__main__":
    main()
