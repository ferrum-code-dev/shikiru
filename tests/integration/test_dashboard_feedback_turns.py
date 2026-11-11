from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.dashboard.contract import TaskBrowserFilter
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Step, Transition
from shikiru.domain.tasks import Mode, Role, Status
from shikiru.operator.queries import OperatorQueries
from shikiru.store.events import EventLog
from shikiru.store.tasks import TaskStore
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import widget_context
from tests.support.store import seed_task

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

START = datetime(2026, 1, 1, tzinfo=UTC)
ACCEPT = "accept"


def _turn_completed(events: EventLog, task: Task, role: Role, summary: str, turn: str) -> None:
    events.append(
        EventKind.TURN_COMPLETED,
        task_id=task.id,
        message=summary,
        data={
            "step": task.step.value if task.step is not None else None,
            "iteration": task.iteration,
            "role": role.value,
            "summary": summary,
            "question": "",
            "turn": turn,
        },
    )


def _verdict(events: EventLog, task: Task, summary: str, turn: str) -> None:
    events.append(
        EventKind.VERDICT,
        task_id=task.id,
        message=summary,
        data={
            "decision": ACCEPT,
            "summary": summary,
            "issues": [],
            "residual_risks": [],
            "iteration": task.iteration,
            "role": Role.LEADER.value,
            "turn": turn,
        },
    )


def _turn_event(
    events: EventLog, task: Task, kind: EventKind, role: Role, step: Step = Step.INTEGRATE
) -> None:
    events.append(
        kind,
        task_id=task.id,
        message=f"{task.id} {role.value} turn",
        data={"step": step.value, "role": role.value, "iteration": task.iteration},
    )


def _bubbles(
    state: StateReader, project_dir: Path, clock: FakeClock
) -> list[tuple[Role | None, str]]:
    context = widget_context(state, project_dir, clock)
    return [(bubble.role, bubble.text) for bubble in context.views.dialogue()]


async def test_feedback_round_shows_leader_analysis_rework_and_verdict_in_order(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Feedback round")
    TaskStore(connection, clock).apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    events = EventLog(connection, clock)
    _turn_completed(events, task, Role.LEADER, "Analysis: fix the typo", turn="01-integrate-i1")
    _turn_completed(events, task, Role.IMPLEMENTER, "Rework: typo fixed", turn="02-integrate-i1")
    _turn_completed(events, task, Role.LEADER, "Verified: typo fixed", turn="03-integrate-i1")
    _verdict(events, task, "Verified: typo fixed", turn="03-integrate-i1")

    assert _bubbles(reader, project_dir, clock) == [
        (Role.LEADER, "Analysis: fix the typo"),
        (Role.IMPLEMENTER, "Rework: typo fixed"),
        (Role.LEADER, "Verified: typo fixed"),
    ]


async def test_leader_turns_without_verdict_show_once_and_verification_once(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Normal flow")
    events = EventLog(connection, clock)
    _turn_completed(events, task, Role.LEADER, "Triage: small change", turn="01-triage-i0")
    _turn_completed(
        events, task, Role.IMPLEMENTER, "Implemented the change", turn="02-implement-i1"
    )
    _turn_completed(events, task, Role.LEADER, "Verified the change", turn="03-verify-i1")
    _verdict(events, task, "Verified the change", turn="03-verify-i1")
    _turn_completed(events, task, Role.LEADER, "Consult: keep the API", turn="04-consult-i1")

    assert _bubbles(reader, project_dir, clock) == [
        (Role.LEADER, "Triage: small change"),
        (Role.IMPLEMENTER, "Implemented the change"),
        (Role.LEADER, "Verified the change"),
        (Role.LEADER, "Consult: keep the API"),
    ]


@pytest.mark.parametrize(
    ("turn_events", "expected"),
    [
        ([], Role.IMPLEMENTER),
        ([(EventKind.TURN_STARTED, Role.LEADER)], Role.LEADER),
        ([(EventKind.TURN_STARTED, Role.IMPLEMENTER)], Role.IMPLEMENTER),
        (
            [(EventKind.TURN_STARTED, Role.LEADER), (EventKind.TURN_COMPLETED, Role.LEADER)],
            Role.IMPLEMENTER,
        ),
    ],
)
async def test_integrate_acting_role_follows_the_running_turn(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    turn_events: list[tuple[EventKind, Role]],
    expected: Role,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Integrate", mode=Mode.IMPLEMENT)
    TaskStore(connection, clock).apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    events = EventLog(connection, clock)
    for kind, role in turn_events:
        _turn_event(events, task, kind, role)
    views = widget_context(reader, project_dir, clock).views

    detail = await views.task_detail(task.id)
    browser = views.task_browser(
        task_filter=TaskBrowserFilter.ALL,
        search=task.id,
        offset=0,
        limit=1,
        selected_task_id=None,
    )

    queries = OperatorQueries(
        reader,
        project="demo",
        project_root=project_dir,
        engine_up=lambda: False,
        max_parallel=2,
        clock=clock,
    )
    assert detail is not None
    active_panel_roles = [panel.role for panel in detail.progress.panels if panel.role is not None]
    assert detail.acting_role == expected.value
    assert [row.acting_role for row in browser.rows] == [expected.value]
    assert active_panel_roles == [expected]
    assert queries.task_action_role(task.id) is expected
