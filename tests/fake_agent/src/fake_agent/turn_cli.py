"""A one-process fake CLI for each headless agent turn."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fake_agent.script import (
    AcceptStep,
    CrashStep,
    FailedReportSubmissionStep,
    FailStep,
    NoReportStep,
    Script,
    StallStep,
    TurnStep,
)
from fake_agent.scripted import (
    ScriptContext,
    SessionState,
    build_context,
    load_session_state,
    save_script_index,
    save_session_state,
    script_index,
    script_raw_for,
    submit_scripted_report,
)
from fake_agent.turns import (
    CACHE_READ_TOKENS_PER_TURN,
    CACHE_WRITE_TOKENS_PER_TURN,
    COMPACTED_CONTEXT_USAGE_PERCENT,
    COMPACTION_POST_TOKENS,
    COMPACTION_PRE_TOKENS,
    CONTINUE_PROMPT,
    FAKE_COMPACT_AT_PCT_ENV_VAR,
    FAKE_PROCESS_CAPTURE_FILENAME,
    FAKE_PROCESS_CAPTURE_MARKER,
    FAKE_SESSION_ID_ENV_VAR,
    INPUT_TOKENS_PER_TURN,
    OUTPUT_TOKENS_PER_TURN,
    THRESHOLD_REACHED_EVENT,
    WORK_CONTEXT_USAGE_PERCENT,
)
from shikiru.agents.contracts import RUN_DIR_ENV_VAR, ToolOutcome, role_dir, shikiru_command
from shikiru.agents.testing.resume import RESUME_RECALL_PROMPT
from shikiru.domain.reports import CURRENT_TURN_LINK_NAME, REPORT_FILENAME

if TYPE_CHECKING:
    from collections.abc import Mapping


_EVENT_SESSION_STARTED = "session_started"
_EVENT_MESSAGE = "message"
_EVENT_TOOL_STARTED = "tool_started"
_EVENT_TOOL_FINISHED = "tool_finished"
_EVENT_USAGE_REPORTED = "usage_reported"
_EVENT_QUOTA_REPORTED = "quota_reported"
_EVENT_COMPACTED = "compacted"
_EVENT_TURN_ENDED = "turn_ended"
_EVENT_TURN_FAILED = "turn_failed"
_WORK_TOOL_NAME = "read"
_WORK_TOOL_DETAIL = "reading the workspace"
_REPORT_TOOL_NAME = "shikiru report"
_REPORT_TOOL_DETAIL = "submitting the turn report"
_SESSION_ID_ARGUMENT = "--session-id"
_PROMPT_ARGUMENT = "--prompt"
_MODEL_ARGUMENT = "--model"
_EFFORT_ARGUMENT = "--effort"
_CAPTURE_PROCESSES_ARGUMENT = "--capture-processes-to"
_WAIT_PROMPT = "wait for the next instruction"
_REPORT_COMMAND_TIMEOUT_SECONDS = 10.0
CRASH_STDERR = "fake agent crashed as scripted"
_INPUT_TOKENS_FIELD = "input_tokens"
_OUTPUT_TOKENS_FIELD = "output_tokens"
_CACHE_READ_TOKENS_FIELD = "cache_read_tokens"
_CACHE_WRITE_TOKENS_FIELD = "cache_write_tokens"
_COST_USD_FIELD = "cost_usd"
_COST_BASIS_FIELD = "cost_basis"
_CONTEXT_USED_PCT_FIELD = "context_used_pct"
_REPORT_DRAFT_FILENAME = "report.draft.json"
_INVALID_REPORT_JSON = "{"


def _emit(entry: Mapping[str, object]) -> None:
    line = json.dumps(entry, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _session_started(session_id: str) -> None:
    _emit({"event": _EVENT_SESSION_STARTED, "session_id": session_id})


def _failure(step: FailStep) -> None:
    _emit(
        {
            "event": _EVENT_TURN_FAILED,
            "kind": step.failure_kind.value,
            "message": f"scripted {step.failure_kind.value} failure",
            "resets_at": step.resets_at,
        }
    )


def _response(prompt: str, earlier_prompts: list[str]) -> str:
    if prompt == CONTINUE_PROMPT:
        tasks = [
            index for index, earlier in enumerate(earlier_prompts) if earlier != CONTINUE_PROMPT
        ]
        if not tasks:
            return ""
        return _response(earlier_prompts[tasks[-1]], earlier_prompts[: tasks[-1]])
    if prompt == RESUME_RECALL_PROMPT:
        return next(iter(earlier_prompts), "")
    return f"accepted: {prompt}"


def _work(state: SessionState) -> None:
    _emit({"event": _EVENT_TOOL_STARTED, "name": _WORK_TOOL_NAME, "detail": _WORK_TOOL_DETAIL})
    state["context_used_pct"] = float(state["context_used_pct"]) + WORK_CONTEXT_USAGE_PERCENT
    _emit({"event": _EVENT_TOOL_FINISHED, "name": _WORK_TOOL_NAME})


def _apply_scripted_context(state: SessionState, step: TurnStep) -> None:
    if step.context_used_pct is not None:
        state["context_used_pct"] = step.context_used_pct


def _corrupt_report(current_turn: Path) -> None:
    (current_turn.resolve() / REPORT_FILENAME).write_text("{")


def _read_release(release_fifo: str) -> None:
    Path(release_fifo).read_text()


async def _await_release(step: TurnStep) -> None:
    if step.release_fifo:
        await asyncio.to_thread(_read_release, step.release_fifo)


async def _report(context: ScriptContext, step: TurnStep, prompt: str, current_turn: Path) -> None:
    await _await_release(step)
    _emit({"event": _EVENT_TOOL_STARTED, "name": _REPORT_TOOL_NAME, "detail": _REPORT_TOOL_DETAIL})
    await submit_scripted_report(context, step, prompt)
    if isinstance(step, AcceptStep) and step.corrupt_report:
        await asyncio.to_thread(_corrupt_report, current_turn)
    _emit({"event": _EVENT_TOOL_FINISHED, "name": _REPORT_TOOL_NAME})
    if isinstance(step, AcceptStep) and step.stall_after_report:
        await asyncio.Event().wait()


async def _failed_report_submission(context: ScriptContext, current_turn: Path) -> None:
    draft_path = current_turn / _REPORT_DRAFT_FILENAME
    await asyncio.to_thread(draft_path.write_text, _INVALID_REPORT_JSON)
    _emit({"event": _EVENT_TOOL_STARTED, "name": _REPORT_TOOL_NAME, "detail": _REPORT_TOOL_DETAIL})
    result = await context.runner.run(
        shlex.split(shikiru_command("report", "--file", str(draft_path))),
        timeout=_REPORT_COMMAND_TIMEOUT_SECONDS,
        cwd=context.cwd,
    )
    outcome = ToolOutcome.SUCCESS if result.returncode == 0 else ToolOutcome.FAILURE
    _emit(
        {
            "event": _EVENT_TOOL_FINISHED,
            "name": _REPORT_TOOL_NAME,
            "outcome": outcome.value,
            "exit_code": result.returncode,
        }
    )
    await asyncio.Event().wait()


async def _run_turn(session_id: str, prompt: str, compact_at_pct: float | None) -> int:
    context = build_context()
    state = load_session_state(context.state_path)
    _session_started(session_id)
    if prompt == _WAIT_PROMPT:
        await asyncio.Event().wait()
    response = _response(prompt, state["prompts"])
    state["prompts"] = [*state["prompts"], prompt]
    _work(state)
    save_session_state(context.state_path, state)
    if compact_at_pct is not None and state["context_used_pct"] >= compact_at_pct:
        _emit({"event": THRESHOLD_REACHED_EVENT})
        return 0
    script = Script.from_env(
        script_raw_for(context.role), start_index=script_index(context.script_path)
    )
    step = script.next_step()
    save_script_index(context.script_path, script.index)
    _apply_scripted_context(state, step)
    if step.quota_windows:
        _emit(
            {
                "event": _EVENT_QUOTA_REPORTED,
                "windows": [window.model_dump() for window in step.quota_windows],
            }
        )
    if isinstance(step, CrashStep):
        sys.stderr.write(CRASH_STDERR + "\n")
        return 1
    if isinstance(step, StallStep):
        await asyncio.Event().wait()
    if isinstance(step, FailStep):
        await _await_release(step)
        _failure(step)
        return 0
    if isinstance(step, NoReportStep):
        response = f"no report: {prompt}"
    _emit({"event": _EVENT_MESSAGE, "text": response})
    current_turn = role_dir(context.run_dir, context.role) / CURRENT_TURN_LINK_NAME
    if current_turn.is_symlink() and not isinstance(step, NoReportStep):
        if isinstance(step, FailedReportSubmissionStep):
            await _failed_report_submission(context, current_turn)
        else:
            await _report(context, step, prompt, current_turn)
    state[_INPUT_TOKENS_FIELD] = int(state[_INPUT_TOKENS_FIELD]) + INPUT_TOKENS_PER_TURN
    state[_OUTPUT_TOKENS_FIELD] = int(state[_OUTPUT_TOKENS_FIELD]) + OUTPUT_TOKENS_PER_TURN
    state[_CACHE_READ_TOKENS_FIELD] = (
        int(state[_CACHE_READ_TOKENS_FIELD]) + CACHE_READ_TOKENS_PER_TURN
    )
    state[_CACHE_WRITE_TOKENS_FIELD] = (
        int(state[_CACHE_WRITE_TOKENS_FIELD]) + CACHE_WRITE_TOKENS_PER_TURN
    )
    save_session_state(context.state_path, state)
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
    state = load_session_state(context.state_path)
    state["context_used_pct"] = COMPACTED_CONTEXT_USAGE_PERCENT
    save_session_state(context.state_path, state)
    _session_started(session_id)
    _emit(
        {
            "event": _EVENT_COMPACTED,
            "pre_tokens": COMPACTION_PRE_TOKENS,
            "post_tokens": COMPACTION_POST_TOKENS,
        }
    )
    return 0


async def _run_interactive(session_id: str) -> None:
    while line := sys.stdin.readline():
        prompt = line.rstrip("\n")
        if prompt == "/exit":
            return
        await _run_turn(session_id, prompt, None)


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
        if name == "interactive":
            command_parser.add_argument(_MODEL_ARGUMENT, required=True)
            command_parser.add_argument(_EFFORT_ARGUMENT)
            command_parser.add_argument(_CAPTURE_PROCESSES_ARGUMENT)
    return parser


def main() -> None:
    """Run one headless turn, one compaction, or the interactive take-over REPL."""
    args = _parser().parse_args()
    os.environ[FAKE_SESSION_ID_ENV_VAR] = args.session_id
    run_directory = os.environ.get(RUN_DIR_ENV_VAR)
    capture_path = None
    if args.command == "interactive":
        capture_path = (
            Path(args.capture_processes_to) if args.capture_processes_to is not None else None
        )
    elif args.command == "turn" and run_directory is not None:
        run_path = Path(run_directory)
        if (run_path / FAKE_PROCESS_CAPTURE_MARKER).is_file():
            capture_path = run_path / FAKE_PROCESS_CAPTURE_FILENAME
    if capture_path is not None:
        with capture_path.open("a", encoding="utf-8") as capture_file:
            capture_file.write(
                json.dumps(
                    {
                        "command": args.command,
                        "session_id": args.session_id,
                        "model": args.model,
                        "effort": args.effort,
                    }
                )
                + "\n"
            )
    if args.command == "turn":
        compact_at_pct = float(os.environ[FAKE_COMPACT_AT_PCT_ENV_VAR])
        sys.exit(asyncio.run(_run_turn(args.session_id, args.prompt, compact_at_pct)))
    if args.command == "compact":
        sys.exit(asyncio.run(_run_compaction(args.session_id)))
    asyncio.run(_run_interactive(args.session_id))


if __name__ == "__main__":
    main()
