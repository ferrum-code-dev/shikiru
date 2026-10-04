from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from shikiru.domain.engine_uptime import (
    ENGINE_PID_FIELD,
    ENGINE_PROCESS_STARTED_AT_FIELD,
    project_engine_uptime,
)
from shikiru.domain.events import Event, EventKind

START = datetime(2026, 1, 1, tzinfo=UTC)
PID = 4321
PROCESS_STARTED_AT = 1_700_000_000.0


@dataclass(frozen=True)
class _UptimeCase:
    events: list[Event]
    now: datetime
    running: bool
    identity: tuple[int, float] | None
    expected_live: int | None
    expected_previous: int | None


def _lifecycle_event(
    event_id: int,
    kind: EventKind,
    timestamp: datetime,
    *,
    pid: int = PID,
    process_started_at: float = PROCESS_STARTED_AT,
) -> Event:
    return Event(
        id=event_id,
        ts=timestamp,
        kind=kind,
        message=kind.value,
        data={
            ENGINE_PID_FIELD: pid,
            ENGINE_PROCESS_STARTED_AT_FIELD: process_started_at,
        },
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _UptimeCase(
                [_lifecycle_event(1, EventKind.ENGINE_STARTED, START)],
                START + timedelta(seconds=9),
                True,
                (PID, PROCESS_STARTED_AT),
                9,
                None,
            ),
            id="matching-current-lock-holder",
        ),
        pytest.param(
            _UptimeCase(
                [_lifecycle_event(1, EventKind.ENGINE_STARTED, START)],
                START + timedelta(seconds=9),
                True,
                (PID + 1, PROCESS_STARTED_AT),
                None,
                None,
            ),
            id="mismatched-current-lock-holder",
        ),
        pytest.param(
            _UptimeCase(
                [_lifecycle_event(1, EventKind.ENGINE_STARTED, START + timedelta(seconds=9))],
                START,
                True,
                (PID, PROCESS_STARTED_AT),
                None,
                None,
            ),
            id="future-start-observation",
        ),
        pytest.param(
            _UptimeCase(
                [
                    _lifecycle_event(1, EventKind.ENGINE_STARTED, START),
                    _lifecycle_event(2, EventKind.ENGINE_STOPPED, START + timedelta(seconds=9)),
                ],
                START + timedelta(seconds=30),
                False,
                None,
                None,
                9,
            ),
            id="recorded-stop-duration",
        ),
        pytest.param(
            _UptimeCase(
                [
                    _lifecycle_event(
                        2,
                        EventKind.ENGINE_STOPPED,
                        START + timedelta(seconds=9),
                    ),
                    _lifecycle_event(1, EventKind.ENGINE_STARTED, START),
                ],
                START + timedelta(seconds=30),
                False,
                None,
                None,
                9,
            ),
            id="tied-clock-event-order-uses-id",
        ),
        pytest.param(
            _UptimeCase(
                [_lifecycle_event(1, EventKind.ENGINE_STARTED, START)],
                START + timedelta(seconds=30),
                False,
                None,
                None,
                None,
            ),
            id="missing-stop",
        ),
        pytest.param(
            _UptimeCase(
                [
                    _lifecycle_event(1, EventKind.ENGINE_STARTED, START),
                    _lifecycle_event(
                        2,
                        EventKind.ENGINE_STOPPED,
                        START + timedelta(seconds=40),
                    ),
                ],
                START + timedelta(seconds=30),
                False,
                None,
                None,
                None,
            ),
            id="future-stop-observation",
        ),
        pytest.param(
            _UptimeCase(
                [
                    _lifecycle_event(1, EventKind.ENGINE_STARTED, START),
                    _lifecycle_event(
                        2,
                        EventKind.ENGINE_STOPPED,
                        START + timedelta(seconds=9),
                        process_started_at=PROCESS_STARTED_AT + 1,
                    ),
                ],
                START + timedelta(seconds=30),
                False,
                None,
                None,
                None,
            ),
            id="inconsistent-recorded-stop",
        ),
    ],
)
def test_engine_uptime_requires_matching_lifecycle_evidence(
    case: _UptimeCase,
) -> None:
    projection = project_engine_uptime(
        case.events,
        now=case.now,
        running=case.running,
        identity=case.identity,
    )

    assert projection.running_seconds == case.expected_live
    assert projection.previous_run_seconds == case.expected_previous
