from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import pytest

from shikiru.plugins.agents.codex.options import TESTED_CODEX_VERSION
from shikiru.plugins.agents.codex.sandbox_probe import SANDBOX_CHECK, CodexSandboxProbe
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

DENY_ENV = "FAKE_CODEX_DENY"
FAKE_CODEX = f"""#!/bin/sh
shift
while [ "$1" != "--" ]; do shift; done
shift
set_mode() {{
  for arg in "$@"; do
    case "$arg" in
      ${DENY_ENV}) chmod "$MODE" "$arg" ;;
    esac
  done
}}
MODE=a-w set_mode "$@"
"$@"
MODE=u+w set_mode "$@"
"""

pytestmark = pytest.mark.asyncio


def _install_fake_codex(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory.mkdir()
    executable = directory / "codex"
    executable.write_text(FAKE_CODEX, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("pattern", "denied"),
    [(None, None), ("*/worktrees/*", "worktrees"), ("*/run", "/run")],
    ids=["granted", "worktree kept read-only", "run kept read-only"],
)
async def test_probe_passes_only_when_the_sandbox_grants_every_root_and_names_the_denied_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str | None, denied: str | None
) -> None:
    _install_fake_codex(tmp_path / "bin", monkeypatch)
    if pattern is not None:
        monkeypatch.setenv(DENY_ENV, pattern)

    check = await CodexSandboxProbe(SubprocessRunner()).check()

    assert check.name == SANDBOX_CHECK
    assert check.ok is (denied is None)
    assert denied is None or denied in check.detail


@pytest.mark.skipif(shutil.which("codex") is None, reason="needs the codex CLI")
async def test_real_codex_sandbox_grants_the_worktree_git_directory_and_run_directory() -> None:
    check = await CodexSandboxProbe(SubprocessRunner()).check()

    assert check.ok, f"{check.detail} (tested with Codex {TESTED_CODEX_VERSION})"
