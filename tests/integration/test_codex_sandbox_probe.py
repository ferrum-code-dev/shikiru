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


async def test_probe_passes_when_the_sandbox_grants_the_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_codex(tmp_path / "bin", monkeypatch)

    check = await CodexSandboxProbe(SubprocessRunner()).check()

    assert check.name == SANDBOX_CHECK
    assert check.ok, check.detail


@pytest.mark.parametrize(("pattern", "denied"), [("*/worktrees/*", "worktrees"), ("*/run", "/run")])
async def test_probe_fails_naming_the_root_the_sandbox_keeps_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str, denied: str
) -> None:
    _install_fake_codex(tmp_path / "bin", monkeypatch)
    monkeypatch.setenv(DENY_ENV, pattern)

    check = await CodexSandboxProbe(SubprocessRunner()).check()

    assert not check.ok
    assert denied in check.detail


@pytest.mark.skipif(shutil.which("codex") is None, reason="needs the codex CLI")
async def test_real_codex_sandbox_grants_the_worktree_git_directory_and_run_directory() -> None:
    check = await CodexSandboxProbe(SubprocessRunner()).check()

    assert check.ok, f"{check.detail} (tested with Codex {TESTED_CODEX_VERSION})"
