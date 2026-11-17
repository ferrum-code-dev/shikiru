"""Persist check repair observations across polling and database reopen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.check_repair import CheckFailure, CheckRepairStage
from shikiru.store.check_repair import CheckRepairStore
from shikiru.store.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_repair_rounds_survive_reopen_and_deduplicate_heads(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    connection = database.connect()
    store = CheckRepairStore(connection)
    failure = CheckFailure(name="tests", result="FAILURE", kind="CheckRun", details_url=None)
    first = store.start("T-1", "pr/1", "head-1", [failure])
    duplicate = store.start("T-1", "pr/1", "head-1", [failure])
    assert duplicate.id == first.id
    assert store.advance(
        first.id, CheckRepairStage.ANALYSIS, CheckRepairStage.PUBLICATION, verified_sha="fixed"
    )
    assert not store.advance(first.id, CheckRepairStage.ANALYSIS, CheckRepairStage.PUBLICATION)
    connection.close()

    connection = database.connect()
    store = CheckRepairStore(connection)
    restored = store.rounds("T-1", "pr/1")
    assert len(restored) == 1
    assert restored[0].failures == [failure]
    assert restored[0].stage is CheckRepairStage.PUBLICATION
    assert restored[0].verified_sha == "fixed"
    assert store.advance(
        first.id, CheckRepairStage.PUBLICATION, CheckRepairStage.PUBLISHED, published_sha="fixed"
    )
    second = store.start("T-1", "pr/1", "fixed", [failure])
    assert second.id != first.id
    assert store.rounds("T-1", "pr/1")[0].published_sha == "fixed"
    connection.close()
