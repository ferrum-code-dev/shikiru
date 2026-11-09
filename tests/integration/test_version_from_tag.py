"""A wheel built from a release tag is named for the tag's version."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from shikiru.process import SubprocessRunner
from tests.support.git_repo import run_git

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GIT_COMMAND = "git"
UV_COMMAND = "uv"
UV_BUILD_ARGS = ("build", "--wheel", "--offline")
DISTRIBUTIONS_DIRECTORY = "dist"
TAG = "v9.9.9"
VERSION = "9.9.9"
WHEEL_PREFIX = f"shikiru-{VERSION}-"
BUILD_TIMEOUT_SECONDS = 120.0


@pytest.mark.skipif(
    shutil.which(GIT_COMMAND) is None or shutil.which(UV_COMMAND) is None,
    reason="needs git and uv",
)
async def test_wheel_built_from_a_tagged_clone_is_named_for_the_tag(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    clone = tmp_path / "clone"
    await run_git(runner, None, "clone", "--local", str(REPOSITORY_ROOT), str(clone))
    await run_git(runner, clone, "tag", TAG)

    await runner.run(
        (UV_COMMAND, *UV_BUILD_ARGS),
        timeout=BUILD_TIMEOUT_SECONDS,
        cwd=clone,
        check=True,
    )

    wheel_names = [wheel.name for wheel in (clone / DISTRIBUTIONS_DIRECTORY).glob("*.whl")]
    assert len(wheel_names) == 1
    assert wheel_names[0].startswith(WHEEL_PREFIX)
