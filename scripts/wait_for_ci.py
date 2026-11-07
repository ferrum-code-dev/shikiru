"""Wait until CI passes on a commit; the release workflow publishes only after this succeeds."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CI_WORKFLOW = "ci.yml"
LIST_COMMAND = ("gh", "run", "list", "--workflow", CI_WORKFLOW, "--commit")
LIST_FIELDS = "status,conclusion"
GH_TIMEOUT_SECONDS = 60
POLL_SECONDS = 30
START_WAIT_SECONDS = 300
TOTAL_WAIT_SECONDS = 3600
COMPLETED = "completed"
SUCCESS = "success"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1


class GhError(Exception):
    """The gh command could not list the CI runs; the message says why."""


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass(frozen=True)
class Run:
    status: str
    conclusion: str


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str = ""


def decide(runs: Sequence[Run], elapsed_seconds: float) -> Verdict:
    if any(run.status == COMPLETED and run.conclusion == SUCCESS for run in runs):
        return Verdict(Outcome.PASSED)
    if any(run.status != COMPLETED for run in runs):
        if elapsed_seconds >= TOTAL_WAIT_SECONDS:
            return Verdict(Outcome.FAILED, f"CI did not finish within {TOTAL_WAIT_SECONDS} seconds")
        return Verdict(Outcome.WAITING)
    if runs:
        conclusions = ", ".join(sorted({run.conclusion for run in runs}))
        return Verdict(Outcome.FAILED, f"CI concluded with {conclusions} for this commit")
    if elapsed_seconds >= START_WAIT_SECONDS:
        return Verdict(Outcome.FAILED, "CI did not start for this commit")
    return Verdict(Outcome.WAITING)


def wait_for_ci(
    sha: str,
    list_runs: Callable[[str], Sequence[Run]],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> Verdict:
    started = monotonic()
    while True:
        verdict = decide(list_runs(sha), monotonic() - started)
        if verdict.outcome is not Outcome.WAITING:
            return verdict
        sleep(POLL_SECONDS)


def gh_runs(sha: str) -> list[Run]:
    completed = subprocess.run(
        (*LIST_COMMAND, sha, "--json", LIST_FIELDS),
        capture_output=True,
        text=True,
        check=False,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "gh run list failed"
        raise GhError(message)
    return [Run(item["status"], item["conclusion"] or "") for item in json.loads(completed.stdout)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait until CI passes on a commit.")
    parser.add_argument("sha")
    arguments = parser.parse_args(argv)
    try:
        verdict = wait_for_ci(arguments.sha, gh_runs, time.monotonic, time.sleep)
    except GhError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FAILURE
    if verdict.outcome is Outcome.FAILED:
        sys.stderr.write(f"{verdict.reason}\n")
        return EXIT_FAILURE
    sys.stdout.write(f"CI passed for {arguments.sha}\n")
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
