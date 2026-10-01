"""Open fault streaks are folded from fault and recovery events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shikiru.domain.events import Event, EventKind
from shikiru.domain.faults import open_fault_streaks

START = datetime(2026, 1, 1, tzinfo=UTC)


def _event(
    number: int, kind: EventKind, scope: str | None = "tick", task_id: str | None = None
) -> Event:
    data = {} if scope is None else {"scope": scope}
    if kind is EventKind.ENGINE_FAULT:
        data |= {"error": "Boom", "recoverable": True, "repeat": 2}
    return Event(
        id=number,
        ts=START + timedelta(minutes=number),
        task_id=task_id,
        kind=kind,
        message="m",
        data=data,
    )


@pytest.mark.parametrize(
    ("events", "open_keys"),
    [
        ([], []),
        ([_event(1, EventKind.ENGINE_FAULT)], [("tick", None)]),
        ([_event(1, EventKind.ENGINE_FAULT), _event(2, EventKind.ENGINE_RECOVERED)], []),
        (
            [
                _event(1, EventKind.ENGINE_FAULT),
                _event(2, EventKind.ENGINE_RECOVERED),
                _event(3, EventKind.ENGINE_FAULT),
            ],
            [("tick", None)],
        ),
        (
            [
                _event(1, EventKind.ENGINE_FAULT, task_id="T-1"),
                _event(2, EventKind.ENGINE_FAULT, task_id="T-2"),
                _event(3, EventKind.ENGINE_RECOVERED, task_id="T-1"),
            ],
            [("tick", "T-2")],
        ),
        ([_event(1, EventKind.ENGINE_FAULT, scope=None)], []),
    ],
    ids=["none", "open", "recovered", "reopened", "per task", "one-off fault without scope"],
)
def test_a_streak_is_open_from_its_fault_until_its_recovery(
    events: list[Event], open_keys: list[tuple[str, str | None]]
) -> None:
    assert [(s.scope, s.task_id) for s in open_fault_streaks(events)] == open_keys


def test_a_streak_keeps_its_first_sighting_and_adds_up_its_repeats() -> None:
    (streak,) = open_fault_streaks(
        [_event(1, EventKind.ENGINE_FAULT), _event(5, EventKind.ENGINE_FAULT)]
    )

    assert streak.first_seen == START + timedelta(minutes=1)
    assert streak.last_seen == START + timedelta(minutes=5)
    assert streak.repeat == 4
