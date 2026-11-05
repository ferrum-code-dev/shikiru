from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from fake_agent import turn_cli
from fake_agent.scripted import script_index
from fake_agent.turns import FAKE_COMPACT_AT_PCT_ENV_VAR, FAKE_SESSION_ID_ENV_VAR

from shikiru.agents.contracts import ROLE_ENV_VAR, RUN_DIR_ENV_VAR, role_dir

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio

_ROLE = "implementer"
_SCRIPT_ENV = "SHIKIRU_FAKE_SCRIPT_IMPLEMENTER"
_STATE_DIRNAME = "fake_state"
_SCRIPT_INDEX_FILENAME = "script-index.json"
_SESSION_STARTED_EVENT = "session_started"
_FULL_CONTEXT_PCT = "100"
_SESSION_ID = "killed-session"


class _IndexAtSessionStart(io.StringIO):
    def __init__(self, script_path: Path) -> None:
        super().__init__()
        self._script_path = script_path
        self.index_when_session_started: int | None = None

    def write(self, text: str, /) -> int:
        if _SESSION_STARTED_EVENT in text:
            self.index_when_session_started = script_index(self._script_path)
        return super().write(text)


async def test_a_turn_reports_its_session_only_after_consuming_its_scripted_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    script_path = role_dir(run_dir, _ROLE) / _STATE_DIRNAME / _SCRIPT_INDEX_FILENAME
    monkeypatch.setenv(RUN_DIR_ENV_VAR, str(run_dir))
    monkeypatch.setenv(ROLE_ENV_VAR, _ROLE)
    monkeypatch.setenv(FAKE_COMPACT_AT_PCT_ENV_VAR, _FULL_CONTEXT_PCT)
    monkeypatch.setenv(_SCRIPT_ENV, json.dumps([{"kind": "fail", "failure_kind": "unavailable"}]))
    monkeypatch.setenv(FAKE_SESSION_ID_ENV_VAR, _SESSION_ID)
    output = _IndexAtSessionStart(script_path)
    monkeypatch.setattr("sys.stdout", output)

    await turn_cli.run_turn(_SESSION_ID, "work", float(_FULL_CONTEXT_PCT))

    assert output.index_when_session_started == 1
