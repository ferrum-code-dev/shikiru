"""Muse Code session log location and reading on disk."""

from __future__ import annotations

from pathlib import Path

from shikiru.plugins.agents.muse.session_log import (
    compaction_count,
    locate_session_log,
    read_session_records,
)

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "session_compaction.jsonl"
_SESSION_ID = "5c53b311-57d1-48bb-a292-e701563d01a1"
_OTHER_SESSION_ID = "00000000-0000-7000-9000-000000000001"


def test_session_log_is_found_under_its_date_directory_and_read_back(tmp_path: Path) -> None:
    session_directory = tmp_path / "sessions" / "2026" / "11" / "12" / _SESSION_ID
    session_directory.mkdir(parents=True)
    (session_directory / "session.jsonl").write_text(_FIXTURE.read_text(encoding="utf-8"))

    log = session_directory / "session.jsonl"

    assert locate_session_log(tmp_path, _SESSION_ID) == log
    assert compaction_count(read_session_records(log)) == 1


def test_unknown_session_has_no_log(tmp_path: Path) -> None:
    assert locate_session_log(tmp_path, _OTHER_SESSION_ID) is None
