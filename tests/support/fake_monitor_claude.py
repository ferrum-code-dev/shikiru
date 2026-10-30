import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from fake_agent.script import AcceptStep
from fake_agent.scripted import build_context, submit_scripted_report

_SESSION_ID_FLAG = "--session-id"
_RESUME_FLAG = "--resume"
_PROMPT_FLAG = "-p"
_IMPLEMENTER_ROLE = "implementer"
_INITIAL_RESULT = {
    "type": "result",
    "subtype": "success",
    "stop_reason": "end_turn",
    "terminal_reason": "completed",
    "result": "First result; verification continues.",
    "result_index": 0,
    "usage": {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 4,
    },
    "total_cost_usd": 0.01,
    "modelUsage": {"fake-1": {"contextWindow": 200000, "costBasis": "list"}},
}
_FINAL_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "stop_reason": "end_turn",
    "terminal_reason": "completed",
    "result": "The requested task is complete.",
    "result_index": 1,
    "origin": {
        "kind": "task-notification",
        "producer": "session-task",
        "runId": "run-redacted",
    },
    "usage": {
        "input_tokens": 4,
        "output_tokens": 6,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 8,
    },
    "total_cost_usd": 0.02,
    "modelUsage": {
        "fake-1": {
            "inputTokens": 14,
            "outputTokens": 26,
            "cacheReadInputTokens": 37,
            "cacheCreationInputTokens": 12,
            "costUSD": 0.02,
            "contextWindow": 200000,
            "costBasis": "list",
        }
    },
    "model_usage_since_last_result": {
        "fake-1": {
            "inputTokens": 4,
            "outputTokens": 6,
            "cacheReadInputTokens": 7,
            "cacheCreationInputTokens": 8,
            "costUSD": 0.01,
        }
    },
}


def emit(record: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def _session_id() -> str:
    arguments = sys.argv
    session_flag = _SESSION_ID_FLAG if _SESSION_ID_FLAG in arguments else _RESUME_FLAG
    return arguments[arguments.index(session_flag) + 1]


def _prompt() -> str:
    arguments = sys.argv
    return arguments[arguments.index(_PROMPT_FLAG) + 1]


def _record_prompt(prompt: str) -> None:
    prompt_path = Path(os.environ["SHIKIRU_TEST_CLAUDE_PROMPTS"])
    with prompt_path.open("a") as prompt_file:
        json.dump({"role": os.environ["SHIKIRU_ROLE"], "prompt": prompt}, prompt_file)
        prompt_file.write("\n")


def _emit_report(prompt: str, session_id: str, tool_id: str) -> None:
    os.environ["SHIKIRU_FAKE_SESSION_ID"] = session_id
    asyncio.run(submit_scripted_report(build_context(), AcceptStep(), prompt))
    emit(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "report accepted",
                        "is_error": False,
                    }
                ]
            },
        }
    )


def _complete_leader(prompt: str, session_id: str) -> None:
    report_id = "leader-report-redacted"
    emit({"type": "system", "subtype": "init", "session_id": session_id})
    emit(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Verification is complete."}]},
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": report_id,
                        "name": "shikiru report",
                        "input": {"file": "[redacted report]"},
                    }
                ]
            },
        }
    )
    _emit_report(prompt, session_id, report_id)
    emit(
        {
            "type": "result",
            "subtype": "success",
            "stop_reason": "end_turn",
            "terminal_reason": "completed",
            "result": "Verification accepted.",
        }
    )


def _run_implementer(prompt: str, session_id: str) -> None:
    emit({"type": "system", "subtype": "init", "session_id": session_id})
    emit(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Initial verification is running."}]},
        }
    )
    emit(_INITIAL_RESULT)
    emit({"type": "system", "subtype": "init", "session_id": session_id})
    Path(os.environ["SHIKIRU_TEST_CLAUDE_RELEASE_BACKGROUND"]).read_text()
    emit(
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task-redacted",
            "description": "Background verification is running.",
            "is_backgrounded": True,
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-redacted",
                        "name": "Bash",
                        "input": {"command": "[redacted verification]"},
                    }
                ]
            },
        }
    )
    emit(
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "task-redacted",
            "status": "completed",
            "summary": "Background verification reached its next phase.",
        }
    )
    emit(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-redacted",
                        "content": "verification passed",
                        "is_error": False,
                    }
                ]
            },
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reviewing the background verification result."}
                ]
            },
        }
    )
    Path(os.environ["SHIKIRU_TEST_CLAUDE_RELEASE_REPORT"]).read_text()
    report_id = "report-redacted"
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": report_id,
                        "name": "shikiru report",
                        "input": {"file": "[redacted report]"},
                    }
                ]
            },
        }
    )
    _emit_report(prompt, session_id, report_id)
    emit(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "The requested task is complete."}]},
        }
    )
    emit(_FINAL_RESULT)


def main() -> None:
    prompt = _prompt()
    session_id = _session_id()
    _record_prompt(prompt)
    if os.environ["SHIKIRU_ROLE"] == _IMPLEMENTER_ROLE:
        _run_implementer(prompt, session_id)
    else:
        _complete_leader(prompt, session_id)


if __name__ == "__main__":
    main()
