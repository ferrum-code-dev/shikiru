import asyncio
import json
import os
import sys
from typing import Any

from fake_agent.script import AcceptStep
from fake_agent.scripted import build_context, submit_scripted_report

_SESSION_ID_FLAG = "--session-id"
_RESUME_FLAG = "--resume"
_PROMPT_FLAG = "-p"
_ROLE_ENV_VAR = "SHIKIRU_ROLE"
_SESSION_ENV_VAR = "SHIKIRU_FAKE_SESSION_ID"
_LIST_COST_USD_BY_ROLE = {"implementer": 1.25, "leader": 0.75}
_COST_MODEL = "fake-cost-model"
_USAGE = {
    "input_tokens": 10,
    "output_tokens": 20,
    "cache_read_input_tokens": 30,
    "cache_creation_input_tokens": 4,
}
_CONTEXT_WINDOW = 200000


def emit(record: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def _argument_after(flag: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1]


def _session_id() -> str:
    flag = _SESSION_ID_FLAG if _SESSION_ID_FLAG in sys.argv else _RESUME_FLAG
    return _argument_after(flag)


def _result(list_cost_usd: float) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "result": "The requested task is complete.",
        "usage": _USAGE,
        "total_cost_usd": list_cost_usd,
        "modelUsage": {_COST_MODEL: {"costBasis": "list", "contextWindow": _CONTEXT_WINDOW}},
    }


def main() -> None:
    session_id = _session_id()
    os.environ[_SESSION_ENV_VAR] = session_id
    emit({"type": "system", "subtype": "init", "session_id": session_id})
    asyncio.run(
        submit_scripted_report(build_context(), AcceptStep(), _argument_after(_PROMPT_FLAG))
    )
    emit(_result(_LIST_COST_USD_BY_ROLE[os.environ[_ROLE_ENV_VAR]]))


if __name__ == "__main__":
    main()
