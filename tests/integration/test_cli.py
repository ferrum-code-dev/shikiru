from __future__ import annotations

import sys
from importlib.metadata import version

import pytest

from shikiru.process import SubprocessRunner
from tests.support.waiting import MEDIUM_WAIT_SECONDS

pytestmark = pytest.mark.asyncio


async def test_version_flag_prints_the_installed_package_version() -> None:
    runner = SubprocessRunner()

    result = await runner.run(
        [sys.executable, "-m", "shikiru", "--version"], timeout=MEDIUM_WAIT_SECONDS
    )

    assert result.returncode == 0
    assert result.stdout.strip() == version("shikiru-cli")
