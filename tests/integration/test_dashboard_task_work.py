"""Integration tests for `TaskWorkService` sharing one worktree read between concurrent callers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.config.loader import load_settings
from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import live_work_service
from tests.support.git_repo import commit_all, init_repo, run_git

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)
TASK_ID = "T-1"
BASE_BRANCH = "main"


async def test_concurrent_samples_of_one_task_share_a_single_read(tmp_path: Path) -> None:
    project_root = write_minimal_project(tmp_path)
    worktree = tmp_path / "worktree"
    runner = SubprocessRunner()
    await init_repo(runner, worktree)
    (worktree / "app.py").write_text("one\n")
    await commit_all(runner, worktree, "base")
    await run_git(runner, worktree, "checkout", "-b", "shikiru/live")
    (worktree / "app.py").write_text("two\n")
    service = live_work_service(FakeClock(START), load_settings(project_root))

    first, second = await asyncio.gather(
        service.sample(TASK_ID, worktree, BASE_BRANCH),
        service.sample(TASK_ID, worktree, BASE_BRANCH),
    )

    assert first is second
