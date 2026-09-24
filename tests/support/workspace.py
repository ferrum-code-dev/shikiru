from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.workspace.caches import CacheCloner, default_cow_args
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.process import SubprocessRunner


def task_root_builder(runner: SubprocessRunner, project_root: Path) -> TaskRootBuilder:
    return TaskRootBuilder(
        Git(runner), project_root, CacheCloner(runner, cow_args=default_cow_args())
    )
