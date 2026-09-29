from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.workspace.base_refs import LocalBaseRefs
from shikiru.workspace.caches import CacheCloner, default_cow_args
from shikiru.workspace.git import Git
from shikiru.workspace.roots import TaskRootBuilder

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
        Git(runner),
        project_root,
        repo,
        CacheCloner(runner, cow_args=default_cow_args()),
        base_refs if base_refs is not None else LocalBaseRefs(),
        trackers=trackers,
    )
