from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_PYTEST_TIMEOUT_SECONDS = 30
_FAKE_SCRIPT_ENV = "SHIKIRU_FAKE_SCRIPT_IMPLEMENTER"


async def test_fake_agent_environment_is_restored_after_a_failed_test(tmp_path: Path) -> None:
    states = tmp_path / "states.jsonl"
    completed = tmp_path / "completed"
    (tmp_path / "conftest.py").write_text(
        "import json, os, pytest\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_teardown(item, nextitem):\n"
        "    yield\n"
        f"    with open({str(states)!r}, 'a') as output:\n"
        f"        output.write(json.dumps({_FAKE_SCRIPT_ENV!r} in os.environ) + '\\n')\n"
    )
    (tmp_path / "test_example.py").write_text(
        "import os\n"
        "def test_failure():\n"
        f"    os.environ[{_FAKE_SCRIPT_ENV!r}] = 'turn script'\n"
        "    raise RuntimeError('deliberate test failure')\n"
        "def test_next():\n"
        f"    assert {_FAKE_SCRIPT_ENV!r} not in os.environ\n"
        f"    open({str(completed)!r}, 'w').close()\n"
    )
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {_FAKE_SCRIPT_ENV, "PYTEST_ADDOPTS"}
    }
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-c", "/dev/null", str(tmp_path)],
        env=env,
        timeout=_PYTEST_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 1
    assert states.exists(), result.stderr
    assert [json.loads(line) for line in states.read_text().splitlines()] == [False, False]
    assert completed.exists()
