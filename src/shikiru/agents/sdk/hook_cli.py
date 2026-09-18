"""`shikiru hook`: records one hook payload, or does nothing outside Shikiru (04 §2)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from shikiru.agents.contracts import ROLE_ENV_VAR, RUN_DIR_ENV_VAR, role_dir
from shikiru.agents.sdk.hooks import HookRecorder


def hook_command() -> None:
    """Append the hook payload on stdin to `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/hooks.jsonl`, if set."""
    run_dir = os.environ.get(RUN_DIR_ENV_VAR)
    role = os.environ.get(ROLE_ENV_VAR)
    if not run_dir or not role:
        return
    payload = json.loads(sys.stdin.read())
    HookRecorder(role_dir(Path(run_dir), role)).record(payload)
