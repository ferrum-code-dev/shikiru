"""What the soak says: the seed and schedule up front, a timeline, violations, and the totals."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TextIO

from shikiru.clock import SystemClock

if TYPE_CHECKING:
    from tests.soak.model import ScheduledFault, Tally, Violation
    from tests.soak.scenarios import Schedule

CLOCK = SystemClock()


@dataclass(slots=True)
class ArenaSummary:
    name: str
    tally: Tally
    statuses: dict[str, int] = field(default_factory=dict)
    fault_scopes: dict[str, int] = field(default_factory=dict)
    kept_at: str | None = None
    effects: dict[str, int] = field(default_factory=dict)


class Reporter:
    def __init__(self, seed: str, *, out: TextIO = sys.stdout) -> None:
        self._out = out
        self._started = CLOCK.now()
        self.seed = seed
        self.violations: list[tuple[str, Violation]] = []
        self.summaries: list[ArenaSummary] = []
        self._seen: set[str] = set()

    def say(self, line: str) -> None:
        elapsed = (CLOCK.now() - self._started).total_seconds()
        self._out.write(f"[{elapsed:7.1f}s] {line}\n")
        self._out.flush()

    def announce(self, arena: str, mode: str, schedule: Schedule) -> None:
        self.say(
            f"arena {arena} ({mode}): {len(schedule.submissions)} submissions, "
            f"{len(schedule.operations)} operator operations, {len(schedule.faults)} faults"
        )
        for fault in schedule.faults:
            self.say(
                f"  schedule {arena} +{fault.offset_seconds:6.1f}s {fault.kind.value} "
                f"{fault.duration_seconds:.1f}s"
            )

    def injected(self, arena: str, fault: ScheduledFault, outcome: str) -> None:
        self.say(f"fault {arena} {fault.kind.value}: {outcome}")

    def violation(self, arena: str, violation: Violation) -> None:
        text = f"{arena} {violation}"
        if text in self._seen:
            return
        self._seen.add(text)
        self.violations.append((arena, violation))
        self.say(f"VIOLATION {text}")

    def finish(self) -> bool:
        self.say(f"seed {self.seed}")
        for summary in self.summaries:
            self.say(
                f"{summary.name}: submitted {summary.tally.submitted}, tasks {summary.statuses}, "
                f"commands {summary.tally.commands}, faults {summary.tally.faults}, "
                f"engine fault scopes {summary.fault_scopes}, took effect {summary.effects}"
            )
            if summary.kept_at:
                self.say(f"{summary.name}: kept for diagnosis at {summary.kept_at}")
        self.say(f"{len(self.violations)} violation(s)" if self.violations else "no violations")
        return not self.violations
