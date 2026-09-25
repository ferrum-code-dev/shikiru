from __future__ import annotations

import sys
from importlib.metadata import version

import pytest

from shikiru.process import SubprocessRunner

pytestmark = pytest.mark.asyncio


async def test_version_flag_prints_the_installed_package_version() -> None:
    runner = SubprocessRunner()

    result = await runner.run([sys.executable, "-m", "shikiru", "--version"], timeout=10)

    assert result.returncode == 0
    assert result.stdout.strip() == version("shikiru")
