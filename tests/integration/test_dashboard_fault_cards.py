"""The fault cards the dashboard views derive from recorded engine faults."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.store.events import EventLog
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, state_views

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.dashboard.contract import EngineFaultView
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
PUSH = "base push main"
REJECTION = "! [rejected] main -> main (non-fast-forward)"
PUSH_ARGV = ["git", "push", "origin", "main"]


def _record(connection: Connection, scope: str, message: str, **data: object) -> None:
    EventLog(connection, FakeClock(START)).append(
        EventKind.ENGINE_FAULT,
        message=message,
        data={"scope": scope, "error": "CommandFailed", "recoverable": True, "repeat": 1, **data},
    )


def _cards(reader: StateReader, tmp_path: Path) -> dict[str, EngineFaultView]:
    views = state_views(reader, write_minimal_project(tmp_path), FakeClock(START), StubServices())
    return {fault.scope: fault for fault in views.engine_faults()}


def _detail(fault: EngineFaultView, label: str) -> str:
    return next(row.value for row in fault.details if row.label == label)


def test_a_legacy_event_without_detail_shows_the_error_text_its_message_recorded(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    _record(connection, PUSH, f"{PUSH} failed: CommandFailed: remote hung up")
    fault = _cards(reader, tmp_path)[PUSH]
    assert "remote hung up" in fault.cause
    assert "remote hung up" in _detail(fault, "Error")


def test_a_failed_command_shows_the_rejection_and_keeps_the_full_evidence(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    stderr = f"To origin\n{REJECTION}\nerror: failed to push some refs\n"
    _record(
        connection,
        PUSH,
        f"{PUSH} failed: CommandFailed: git push",
        detail=f"git push origin main\n{stderr}",
        command=PUSH_ARGV,
        exit_code=1,
        stderr=stderr,
    )
    fault = _cards(reader, tmp_path)[PUSH]
    assert REJECTION in fault.cause
    assert "\n" not in fault.cause
    assert "git push origin main" not in fault.cause
    assert _detail(fault, "Command") == "git push origin main"
    assert _detail(fault, "Exit code") == "1"
    assert REJECTION in _detail(fault, "Stderr")


def test_a_fault_with_no_recorded_text_says_its_cause_is_unknown(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    _record(connection, PUSH, f"{PUSH} failed: ")
    fault = _cards(reader, tmp_path)[PUSH]
    assert fault.cause.startswith("unknown")


@pytest.mark.parametrize("scope", [PUSH, "remote check main", "pull request close"])
def test_recoverability_never_replaces_the_scopes_own_retry_policy(
    connection: Connection, reader: StateReader, tmp_path: Path, scope: str
) -> None:
    _record(connection, scope, f"{scope} failed: ValueError: x", recoverable=True)
    _record(connection, f"{scope} other", f"{scope} other failed: ValueError: x", recoverable=False)
    cards = _cards(reader, tmp_path)
    recoverable, unrecoverable = cards[scope], cards[f"{scope} other"]
    assert unrecoverable.needs_action
    if scope == PUSH:
        assert recoverable.status != unrecoverable.status
        assert "Retrying automatically" in unrecoverable.status
        assert not recoverable.needs_action
        assert "stop" in _detail(unrecoverable, "Retry policy")


def test_recovery_kinds_have_distinct_statuses_and_action_needed_cards_come_first(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    scopes = [PUSH, "pull request close", "reconcile main", "merge"]
    for scope in scopes:
        _record(connection, scope, f"{scope} failed: ValueError: x")
    views = state_views(reader, write_minimal_project(tmp_path), FakeClock(START), StubServices())
    faults = views.engine_faults()
    assert len({fault.status for fault in faults}) == len(scopes)
    assert faults[-1].scope == PUSH
    assert [fault.needs_action for fault in faults] == [True, True, True, False]
