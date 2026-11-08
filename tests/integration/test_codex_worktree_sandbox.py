from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import AgentContext, Rung, TurnSpec
from shikiru.plugins.agents.codex.agent import create
from shikiru.plugins.agents.codex.options import CODEX_HOME_ENV_VAR, WRITABLE_ROOTS_CONFIG
from shikiru.process import SubprocessRunner
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.git_repo import commit_all, init_repo, run_git

if TYPE_CHECKING:
    from shikiru.agents.contracts import Turn

pytestmark = pytest.mark.asyncio

ARGV_CAPTURE_ENV = "FAKE_CODEX_ARGV_CAPTURE"
_BUNDLED_CONTEXT_WINDOW = 100_000
_BUNDLED_CATALOG = json.dumps(
    {"models": [{"slug": "model-x", "context_window": _BUNDLED_CONTEXT_WINDOW}]}
)
FAKE_CODEX = f"""#!/bin/sh
for arg in "$@"; do printf '%s\\n' "$arg"; done > "${ARGV_CAPTURE_ENV}"
if [ "$1" = "debug" ] && [ "$2" = "models" ] && [ "$3" = "--bundled" ]; then
  printf '%s\\n' '{_BUNDLED_CATALOG}'
  exit 0
fi
"""
RESUMED_SESSION = "session-resumed"


def _spec(tmp_path: Path, worktree: Path, session_id: str | None) -> TurnSpec:
    run_dir = tmp_path / "run"
    home = tmp_path / "codex-home"
    run_dir.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    return TurnSpec(
        rung=Rung(agent="codex", model="model-x", effort=None),
        role="implementer",
        prompt="prompt",
        cwd=worktree,
        run_dir=run_dir,
        turn_dir=tmp_path / "turns" / str(session_id),
        add_dirs=(),
        env={CODEX_HOME_ENV_VAR: str(home)},
        session_id=session_id,
        usage=None,
    )


async def _drain(turn: Turn) -> None:
    async for _ in turn.signals():
        pass


def _parse_roots(capture: Path) -> set[Path]:
    captured = capture.read_text(encoding="utf-8").splitlines()
    (setting,) = [arg for arg in captured if arg.startswith(f"{WRITABLE_ROOTS_CONFIG}=")]
    return {Path(root).resolve() for root in json.loads(setting.split("=", 1)[1])}


async def _writable_roots(tmp_path: Path, worktree: Path, session_id: str | None) -> set[Path]:
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))
    await _drain(await agent.start_turn(_spec(tmp_path, worktree, session_id)))
    return _parse_roots(tmp_path / "argv")


@pytest.mark.parametrize("session_id", [None, RESUMED_SESSION], ids=["first turn", "resumed turn"])
async def test_a_linked_worktree_turn_may_write_its_gitdir_the_common_dir_and_the_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str | None
) -> None:
    runner = SubprocessRunner()
    repo = tmp_path / "repo"
    await init_repo(runner, repo)
    await commit_all(runner, repo, "initial", allow_empty=True)
    worktree = tmp_path / "linked"
    await run_git(runner, repo, "worktree", "add", "-b", "task", str(worktree))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake = binaries / "codex"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(ARGV_CAPTURE_ENV, str(tmp_path / "argv"))

    roots = await _writable_roots(tmp_path, worktree, session_id)

    assert roots == {
        (repo / ".git" / "worktrees" / "linked").resolve(),
        (repo / ".git").resolve(),
        (tmp_path / "run").resolve(),
    }
