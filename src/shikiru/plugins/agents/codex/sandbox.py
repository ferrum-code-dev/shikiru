"""Codex sandbox configuration shared by turns and the doctor probe."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shikiru.plugins.agents.codex.options import (
    CONFIG_FLAG,
    SANDBOX_MODE_CONFIG,
    WORKSPACE_WRITE_SANDBOX,
    WRITABLE_ROOTS_CONFIG,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def sandbox_config_args(roots: Sequence[Path]) -> list[str]:
    """Return the `-c` arguments that make `roots` writable under `workspace-write`.

    Configuration overrides are the one mechanism `codex exec` and `codex exec resume` share;
    `exec resume` has no `--sandbox` or `--add-dir`.
    """
    return [
        CONFIG_FLAG,
        f'{SANDBOX_MODE_CONFIG}="{WORKSPACE_WRITE_SANDBOX}"',
        CONFIG_FLAG,
        f"{WRITABLE_ROOTS_CONFIG}={json.dumps([str(root) for root in roots])}",
    ]
