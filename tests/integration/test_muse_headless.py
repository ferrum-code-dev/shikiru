"""Muse headless calls through the real process runner and a fake executable."""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.agents.contracts import RUNTIME_DEFAULT_MODEL, AgentContext
from shikiru.errors import CommandFailed, HeadlessOutputError
from shikiru.plugins.agents.muse.agent import create
from shikiru.plugins.agents.muse.options import MUSE_COMMAND, RUN_TERMINAL_COMPLETED_TYPE
from shikiru.process import SubprocessRunner
from tests.support.clock import LONG_AGO, FakeClock

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_STDOUT_ENV = "FAKE_MUSE_HEADLESS_STDOUT"
_EXIT_ENV = "FAKE_MUSE_HEADLESS_EXIT"
_FAKE_MUSE = """#!{python}
import os
import sys

sys.stdout.write(os.environ["FAKE_MUSE_HEADLESS_STDOUT"])
sys.exit(int(os.environ["FAKE_MUSE_HEADLESS_EXIT"]))
"""
_COMPLETED_RECORD = {
    "payload_type": RUN_TERMINAL_COMPLETED_TYPE,
    "payload": {"text": '{"answer":"done"}'},
}
_PROMPT = "Return a short answer."
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def _install_fake_muse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    exit_code: int = 0,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(_FAKE_MUSE.format(python=sys.executable), encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(_STDOUT_ENV, stdout)
    monkeypatch.setenv(_EXIT_ENV, str(exit_code))


async def _ask(tmp_path: Path) -> Mapping[str, Any]:
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=FakeClock(LONG_AGO)))
    headless = agent.headless()
    assert headless is not None
    return await headless.ask(
        _PROMPT,
        _SCHEMA,
        RUNTIME_DEFAULT_MODEL,
        None,
        tmp_path,
        timeout=timedelta(seconds=10),
    )


@pytest.mark.asyncio
async def test_headless_returns_the_completed_json_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stdout=json.dumps(_COMPLETED_RECORD) + "\n")

    assert await _ask(tmp_path) == {"answer": "done"}


@pytest.mark.asyncio
async def test_headless_rejects_invalid_result_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {
        "payload_type": RUN_TERMINAL_COMPLETED_TYPE,
        "payload": {"text": "not json"},
    }
    _install_fake_muse(tmp_path, monkeypatch, stdout=json.dumps(record) + "\n")

    with pytest.raises(HeadlessOutputError, match="not valid JSON"):
        await _ask(tmp_path)


@pytest.mark.asyncio
async def test_headless_rejects_missing_completed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stdout="{}\n")

    with pytest.raises(HeadlessOutputError, match="no completed result"):
        await _ask(tmp_path)


@pytest.mark.asyncio
async def test_headless_raises_for_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, stdout="", exit_code=2)

    with pytest.raises(CommandFailed):
        await _ask(tmp_path)
