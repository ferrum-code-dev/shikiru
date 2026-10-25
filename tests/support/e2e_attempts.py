"""One real-agent E2E attempt as the results artifact records it."""

from __future__ import annotations

from dataclasses import dataclass

ATTEMPT_PASSED = "passed"
ATTEMPT_FAILED = "failed"


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    status: str
    failure_class: str | None
    rerun_reason: str | None
    cost: float | None
    duration_seconds: float
    diagnostics: str | None


def total_cost(attempts: list[AttemptRecord]) -> float | None:
    """Return the summed cost of the attempts that reported one, or None when none did."""
    costs = [record.cost for record in attempts if record.cost is not None]
    return sum(costs) if costs else None
