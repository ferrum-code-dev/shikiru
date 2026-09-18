"""Real git repos for workspace tests, never touching the operator's global git config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.process import SubprocessRunner

_GIT_COMMAND_TIMEOUT_SECONDS = 10.0
_COMMIT_IDENTITY_ARGS = ("-c", "user.name=Shikiru Test", "-c", "user.email=test@shikiru.example")

_NEUTRAL_GIT_CONFIG_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


async def init_repo(runner: SubprocessRunner, path: Path, *, initial_branch: str = "main") -> None:
    """Create a git repo at `path` with a local (never global or system) default branch name.

    Every git command here runs with `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at
    `os.devnull`, so an operator setting such as `commit.gpgsign` or `core.hooksPath` in their
    own git config can never leak into these tests.
    """
    path.mkdir(parents=True, exist_ok=True)
    await runner.run(
        ["git", "-c", f"init.defaultBranch={initial_branch}", "init", str(path)],
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        env=_NEUTRAL_GIT_CONFIG_ENV,
        check=True,
    )


async def commit_all(
    runner: SubprocessRunner, repo_dir: Path, message: str, *, allow_empty: bool = False
) -> None:
    """Stage every change in `repo_dir` and commit it, with a local-only commit identity."""
    await runner.run(
        ["git", "add", "-A"],
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        cwd=repo_dir,
        env=_NEUTRAL_GIT_CONFIG_ENV,
        check=True,
    )
    args = ["git", *_COMMIT_IDENTITY_ARGS, "commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    await runner.run(
        args,
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        cwd=repo_dir,
        env=_NEUTRAL_GIT_CONFIG_ENV,
        check=True,
    )
