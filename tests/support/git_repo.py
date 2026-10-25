"""Real git repos for workspace tests, never touching the operator's global git config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.process import SubprocessRunner

GIT_COMMAND = "git"
GIT_COMMAND_TIMEOUT = 30.0
CACHE_COPY_TIMEOUT = 60.0
_GIT_COMMAND_TIMEOUT_SECONDS = 10.0
TEST_GIT_USER_NAME = "Shikiru Test"
TEST_GIT_USER_EMAIL = "test@shikiru.example"
COMMIT_IDENTITY_ARGS = (
    "-c",
    f"user.name={TEST_GIT_USER_NAME}",
    "-c",
    f"user.email={TEST_GIT_USER_EMAIL}",
)

_NEUTRAL_GIT_CONFIG_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


async def run_git(runner: SubprocessRunner, cwd: Path | None, *args: str) -> str:
    """Run `git args` in `cwd` under the neutral git config and return its stripped stdout."""
    result = await runner.run(
        [GIT_COMMAND, *args],
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        cwd=cwd,
        env=_NEUTRAL_GIT_CONFIG_ENV,
        check=True,
    )
    return result.stdout.strip()


async def init_repo(runner: SubprocessRunner, path: Path, *, initial_branch: str = "main") -> None:
    """Create a repo with a local default branch and deterministic test committer identity.

    Git commands here mask global and system config, so host settings cannot leak into fixtures.
    """
    path.mkdir(parents=True, exist_ok=True)
    await run_git(runner, None, "-c", f"init.defaultBranch={initial_branch}", "init", str(path))
    await run_git(runner, path, "config", "user.name", TEST_GIT_USER_NAME)
    await run_git(runner, path, "config", "user.email", TEST_GIT_USER_EMAIL)


async def commit_all(
    runner: SubprocessRunner, repo_dir: Path, message: str, *, allow_empty: bool = False
) -> None:
    """Stage every change in `repo_dir` and commit it, with a local-only commit identity."""
    await run_git(runner, repo_dir, "add", "-A")
    args = [*COMMIT_IDENTITY_ARGS, "commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    await run_git(runner, repo_dir, *args)
