"""The task diff service over a test project's real SQLite state and Git repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.dashboard.services import TaskDiffService
from shikiru.operator.queries import OperatorQueries
from shikiru.process import SubprocessRunner
from shikiru.workspace.git import Git
from tests.support.git_repo import GIT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.store.reader import StateReader


def task_diff_service(reader: StateReader, project_root: Path, clock: Clock) -> TaskDiffService:
    """Build the diff popup's service for the project at `project_root`."""
    queries = OperatorQueries(
        reader,
        project="diff",
        project_root=project_root,
        engine_up=lambda: False,
        max_parallel=1,
        pr_check_max_attempts=1,
        clock=clock,
    )
    git = Git(SubprocessRunner(), command_timeout=GIT_COMMAND_TIMEOUT)
    return TaskDiffService(git, queries, reader, project_root)
