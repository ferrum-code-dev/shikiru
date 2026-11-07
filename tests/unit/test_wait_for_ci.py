"""The release waits for a passing CI run on its commit; red, missing or endless CI fails it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts import wait_for_ci
from scripts.wait_for_ci import COMPLETED, SUCCESS, Outcome, Run

if TYPE_CHECKING:
    from collections.abc import Sequence

SHA = "0123456789abcdef0123456789abcdef01234567"
IN_PROGRESS = "in_progress"
QUEUED = "queued"
FAILURE = "failure"
BEFORE_START = wait_for_ci.START_WAIT_SECONDS - 1
AFTER_START = wait_for_ci.START_WAIT_SECONDS
BEFORE_TOTAL = wait_for_ci.TOTAL_WAIT_SECONDS - 1
AFTER_TOTAL = wait_for_ci.TOTAL_WAIT_SECONDS


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.parametrize(
    ("runs", "elapsed", "outcome"),
    [
        ([Run(COMPLETED, SUCCESS)], BEFORE_START, Outcome.PASSED),
        ([Run(COMPLETED, FAILURE)], BEFORE_START, Outcome.FAILED),
        ([Run(COMPLETED, FAILURE), Run(QUEUED, "")], BEFORE_START, Outcome.WAITING),
        ([], BEFORE_START, Outcome.WAITING),
        ([], AFTER_START, Outcome.FAILED),
        ([Run(IN_PROGRESS, "")], BEFORE_TOTAL, Outcome.WAITING),
        ([Run(IN_PROGRESS, "")], AFTER_TOTAL, Outcome.FAILED),
        ([Run(COMPLETED, FAILURE), Run(COMPLETED, SUCCESS)], AFTER_TOTAL, Outcome.PASSED),
    ],
)
def test_decides_on_the_runs_of_the_commit(
    runs: Sequence[Run], elapsed: float, outcome: Outcome
) -> None:
    assert wait_for_ci.decide(runs, elapsed).outcome is outcome


def test_a_failed_run_is_reported_with_its_conclusion() -> None:
    verdict = wait_for_ci.decide([Run(COMPLETED, FAILURE)], BEFORE_START)

    assert FAILURE in verdict.reason


def test_polls_every_poll_interval_until_the_commit_has_a_verdict() -> None:
    clock = FakeTime()
    answers = iter([[], [Run(IN_PROGRESS, "")], [Run(COMPLETED, SUCCESS)]])

    verdict = wait_for_ci.wait_for_ci(
        SHA, lambda _sha: list(next(answers)), clock.monotonic, clock.sleep
    )

    assert verdict.outcome is Outcome.PASSED
    assert clock.slept == [wait_for_ci.POLL_SECONDS, wait_for_ci.POLL_SECONDS]
