"""Git histories with release tags for the release scripts' tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.process import SubprocessRunner
from tests.support.git_repo import commit_all, init_repo, run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

INITIAL_COMMIT = "initial commit"


@dataclass(frozen=True)
class Tag:
    name: str


type Step = str | Tag


async def build_history(path: Path, steps: Sequence[Step]) -> dict[str, str]:
    """Commit `steps` in order on a fresh repo whose first commit is `INITIAL_COMMIT`.

    Returns the full sha of each commit, keyed by its subject line.
    """
    runner = SubprocessRunner()
    await init_repo(runner, path)
    await commit_all(runner, path, INITIAL_COMMIT, allow_empty=True)
    shas: dict[str, str] = {}
    for step in steps:
        if isinstance(step, Tag):
            await run_git(runner, path, "tag", step.name)
        else:
            await commit_all(runner, path, step, allow_empty=True)
            shas[step.partition("\n")[0]] = await run_git(runner, path, "rev-parse", "HEAD")
    return shas
