"""A seconds-long soak: the real engine, an engine kill, and the invariants at the end."""

from __future__ import annotations

import pytest

from shikiru.domain.tasks import Mode
from tests.soak.driver import SoakResult, Timing, run_soak
from tests.soak.model import (
    FaultKind,
    IntegrationMode,
    ScheduledFault,
    TaskKind,
    TaskPlan,
)
from tests.soak.scenarios import Schedule, Submission

pytestmark = pytest.mark.asyncio

SEED = "smoke"
TIMING = Timing(intake_seconds=6.0, drain_seconds=90.0)
TASKS = 3
RESTART_AT_SECONDS = 2.5
SUBMISSION_GAP_SECONDS = 1.0


def _schedule(_seed: str, _mode: IntegrationMode, _intake: float) -> Schedule:
    plans = [
        TaskPlan(TaskKind.SIMPLE, f"Implement smoke task {index}", Mode.IMPLEMENT, 0, (), {})
        for index in range(TASKS)
    ]
    return Schedule(
        submissions=tuple(
            Submission(index * SUBMISSION_GAP_SECONDS, plan, 1.0)
            for index, plan in enumerate(plans)
        ),
        operations=(),
        faults=(ScheduledFault(RESTART_AT_SECONDS, FaultKind.ENGINE_KILL, 0.0),),
    )


@pytest.mark.timeout(240)
async def test_a_short_soak_with_an_engine_kill_ends_with_every_invariant_held() -> None:
    result: SoakResult = await run_soak(
        SEED, timing=TIMING, modes=(IntegrationMode.MERGE,), schedule_for=_schedule
    )

    assert [str(violation) for _, violation in result.reporter.violations] == []
    statuses = result.reporter.summaries[0].statuses
    assert set(statuses) <= {"done", "cancelled"}
    assert statuses.get("done", 0) >= TASKS - 1
