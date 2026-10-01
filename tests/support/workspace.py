from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.caches import CacheCloner, default_cow_args
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.config.settings import RepoConfig
    from shikiru.process import SubprocessRunner
    from shikiru.workspace.base_refs import BaseRefs


def task_root_builder(
    runner: SubprocessRunner,
    project_root: Path,
    repo: RepoConfig,
    base_refs: BaseRefs | None = None,
    trackers: Sequence[str] = (),
) -> TaskRootBuilder:
    return TaskRootBuilder(
        Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        project_root,
        repo,
        CacheCloner(runner, cow_args=default_cow_args(), copy_timeout=CACHE_COPY_TIMEOUT),
        base_refs if base_refs is not None else LocalBaseRefs(),
        trackers=trackers,
    )
