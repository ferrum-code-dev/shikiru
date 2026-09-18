"""`shikiru hook <role>`: records one hook payload, or does nothing outside Shikiru (04 §2)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from shikiru.agents.sdk.hooks import HookRecorder

RUN_DIR_ENV_VAR = "SHIKIRU_RUN_DIR"

_ROLE_ARGUMENT = typer.Argument(help="The agent role this hook fired for, e.g. leader.")


def hook_command(role: str = _ROLE_ARGUMENT) -> None:
    """Append the hook payload on stdin to `$SHIKIRU_RUN_DIR/<role>/hooks.jsonl`, if set."""
    run_dir = os.environ.get(RUN_DIR_ENV_VAR)
    if not run_dir:
        return
    payload = json.loads(sys.stdin.read())
    HookRecorder(Path(run_dir) / role).record(payload)
