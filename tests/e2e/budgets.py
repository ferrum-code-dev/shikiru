"""How long each E2E scenario attempt and the whole invocation may take (07 §5)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.clock import Clock

ATTEMPT_BUDGETS = {
    "fake": timedelta(seconds=45),
    "e1": timedelta(minutes=10),
    "e2": timedelta(minutes=10),
    "e3": timedelta(minutes=20),
    "e4": timedelta(minutes=10),
    "e5": timedelta(minutes=10),
    "e6": timedelta(minutes=12),
}
INVOCATION_BUDGET = timedelta(minutes=60)
AUTOMATIC_RERUNS = 1
CLI_TIMEOUT_SECONDS = 60.0
CLEANUP_TIMEOUT_SECONDS = 60.0
WAIT_CEILING_SECONDS = max(budget.total_seconds() for budget in ATTEMPT_BUDGETS.values())


class InvocationBudgetExhausted(AssertionError):
    """The whole E2E invocation used up its time before an attempt could start."""


def outer_timeout_seconds(scenario: str) -> float:
    """Return the pytest timeout of `scenario`: every allowed attempt, its cleanup and the CLI."""
    attempt = ATTEMPT_BUDGETS[scenario].total_seconds()
    return (AUTOMATIC_RERUNS + 1) * (attempt + CLEANUP_TIMEOUT_SECONDS) + CLI_TIMEOUT_SECONDS


class InvocationBudget:
    """The time left of one E2E invocation, shared by every attempt of every scenario."""

    def __init__(self, clock: Clock, total: timedelta) -> None:
        """Start counting `total` from now on `clock`."""
        self._clock = clock
        self._deadline = clock.now() + total

    def attempt_timeout_seconds(self, scenario: str) -> float:
        """Return `scenario`'s attempt budget, cut to what the invocation has left."""
        remaining = (self._deadline - self._clock.now()).total_seconds()
        if remaining <= 0:
            message = f"the {INVOCATION_BUDGET} invocation budget is used up before {scenario}"
            raise InvocationBudgetExhausted(message)
        return min(ATTEMPT_BUDGETS[scenario].total_seconds(), remaining)
