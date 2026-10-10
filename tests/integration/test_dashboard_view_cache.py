from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.dashboard.tick import pinned_tick
from shikiru.domain.escalation import options_for
from shikiru.domain.steps import first_step
from shikiru.domain.tasks import EscalationReason
from shikiru.store.escalations import EscalationStore
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, state_views
from tests.support.store import seed_task

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
ELAPSED_SECONDS = 7200
BROWSER_PAGE_LIMIT = 20


async def test_queue_and_summary_follow_task_writes_made_on_another_connection(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    views = state_views(reader, project_dir, clock, StubServices())
    assert views.queue() == []
    assert views.workspace_summary().running == 0

    task = seed_task(connection, clock, title="Add retry")
    assert [row.task_id for row in views.queue()] == [task.id]

    TaskStore(connection, clock).apply_transition(task.id, first_step(task))
    assert views.queue() == []
    assert views.workspace_summary().running == 1


async def test_decision_age_advances_with_the_clock_while_the_database_is_unchanged(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Choose a parser")
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=f"{task.id} · Choose a parser",
        packet_path=".shikiru/escalations/missing.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )
    views = state_views(reader, project_dir, clock, StubServices())
    assert views.escalations()[0].age == "now"

    clock.advance(ELAPSED_SECONDS)

    assert views.escalations()[0].age == "2h"


async def test_a_decision_opened_on_another_connection_is_counted_on_the_next_call(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Choose a parser")
    views = state_views(reader, project_dir, clock, StubServices())
    assert views.attention().decisions == 0

    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=f"{task.id} · Choose a parser",
        packet_path=".shikiru/escalations/missing.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )

    assert views.attention().decisions == 1


async def test_task_browser_pages_follow_task_writes_made_on_another_connection(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    views = state_views(reader, project_dir, clock, StubServices())

    def browser_task_ids() -> list[str]:
        (page,) = views.task_browser_pages(
            task_filter=TaskBrowserFilter.ALL,
            search="",
            limit=BROWSER_PAGE_LIMIT,
            page_count=1,
            selected_task_id=None,
        )
        return [row.task_id for row in page.rows]

    assert browser_task_ids() == []

    task = seed_task(connection, clock, title="Add retry")

    assert browser_task_ids() == [task.id]


def _open_decision(connection: Connection, clock: FakeClock, title: str) -> None:
    task = seed_task(connection, clock, title=title)
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline=f"{task.id} · {title}",
        packet_path=".shikiru/escalations/missing.md",
        options=options_for(EscalationReason.DECISION_REQUIRED),
    )


async def test_widgets_asking_within_one_tick_share_the_built_views(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _open_decision(connection, clock, "Choose a parser")
    views = state_views(reader, project_dir, clock, StubServices())

    with pinned_tick(clock.now()):
        assert views.attention() is views.attention()
        assert views.workspace_summary() is views.workspace_summary()

    assert views.attention() is not views.attention()


async def test_the_next_tick_shows_the_clock_change_and_a_data_change_shows_within_a_tick(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _open_decision(connection, clock, "Choose a parser")
    views = state_views(reader, project_dir, clock, StubServices())

    def first_decision_age() -> str:
        return views.attention().groups[0].escalations[0].age

    with pinned_tick(clock.now()):
        assert first_decision_age() == "now"
        clock.advance(ELAPSED_SECONDS)
        assert first_decision_age() == "now"
        assert views.attention().decisions == 1
        _open_decision(connection, clock, "Choose a layout")
        assert views.attention().decisions == 2
    with pinned_tick(clock.now()):
        assert first_decision_age() == "2h"
