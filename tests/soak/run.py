"""One arena's whole life: start, workload, faults, drain, stop, and the final checks."""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.domain.events import EventKind
from shikiru.domain.faults import SCOPE_KEY
from tests.soak.chaos import ChaosInjector
from tests.soak.hosting import Hosting
from tests.soak.invariants import TERMINAL, InvariantChecker, Ledger
from tests.soak.model import Tally, Violation
from tests.soak.operators import OperatorDesk
from tests.soak.probe import StatusProbe
from tests.soak.reporter import ArenaSummary
from tests.soak.supervisor import EngineSupervisor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from tests.soak.arena import Arena
    from tests.soak.model import ScheduledOperation, Timed
    from tests.soak.reporter import Reporter
    from tests.soak.scenarios import Schedule, Submission

CLOCK = SystemClock()
SAMPLE_SECONDS = 0.3
PROBE_SECONDS = 4.0
SERVICE_SECONDS = 0.5
DRAIN_POLL_SECONDS = 1.0
DELIVERY_WAIT_SECONDS = 80.0


class ArenaRun:
    def __init__(
        self,
        arena: Arena,
        schedule: Schedule,
        seed: str,
        reporter: Reporter,
        *,
        drain_seconds: float,
    ) -> None:
        self._arena = arena
        self._schedule = schedule
        self._reporter = reporter
        self._drain = timedelta(seconds=drain_seconds)
        self._tally = Tally()
        self._ledger = Ledger()
        rng = random.Random(f"{seed}:{arena.name}:runtime")
        self._supervisor = EngineSupervisor(arena, reporter)
        self._chaos = ChaosInjector(arena, self._supervisor, reporter, self._tally)
        self._desk = OperatorDesk(arena, self._ledger, self._tally, rng)
        self._checker = InvariantChecker(arena, self._ledger)
        self._hosting = Hosting(arena, rng) if arena.github is not None else None
        self._probe = StatusProbe(arena, rng)
        self._stop = asyncio.Event()
        self._intake_over = asyncio.Event()

    async def run(self) -> ArenaSummary:
        self._arena.open_state()
        await self._arena.start_engine()
        started = CLOCK.now()
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._supervisor.watch(self._stop))
                group.create_task(self._services())
                group.create_task(self._sampling())
                group.create_task(self._probing())
                intake = [
                    group.create_task(self._at(self._schedule.submissions, started, self._submit)),
                    group.create_task(self._at(self._schedule.operations, started, self._operate)),
                    group.create_task(
                        self._at(self._schedule.faults, started, self._chaos.inject, overlap=True)
                    ),
                ]
                await asyncio.gather(*intake)
                self._intake_over.set()
                await self._drain_tasks()
                await self._await_delivery()
                self._stop.set()
        finally:
            self._stop.set()
        await self._finish()
        return self._summary()

    async def _at[T: Timed](
        self,
        items: Sequence[T],
        started: datetime,
        act: Callable[[T], Awaitable[object]],
        *,
        overlap: bool = False,
    ) -> None:
        running: list[asyncio.Task[object]] = []
        for item in items:
            due = started + timedelta(seconds=item.offset_seconds)
            await CLOCK.sleep(max(0.0, (due - CLOCK.now()).total_seconds()))
            if overlap:
                running.append(asyncio.ensure_future(act(item)))
            else:
                await act(item)
        await asyncio.gather(*running)

    async def _submit(self, submission: Submission) -> None:
        task_id = await self._desk.submit(submission)
        self._reporter.say(
            f"{self._arena.name}: submitted {task_id} ({submission.plan.kind.value})"
        )

    async def _operate(self, operation: ScheduledOperation) -> None:
        done = await self._desk.operate(operation.kind, operation.pick)
        if done:
            self._reporter.say(f"{self._arena.name}: {done}")

    async def _services(self) -> None:
        while not self._stop.is_set():
            await self._desk.release_due(everything=self._intake_over.is_set())
            await self._desk.resolve_escalations()
            if self._intake_over.is_set():
                await self._desk.release_holds()
            if self._hosting is not None:
                await self._hosting.serve()
            await CLOCK.sleep(SERVICE_SECONDS)

    async def _probing(self) -> None:
        while not self._stop.is_set():
            for violation in await self._probe.check():
                self._reporter.violation(self._arena.name, violation)
            await CLOCK.sleep(PROBE_SECONDS)

    async def _sampling(self) -> None:
        while not self._stop.is_set():
            now = CLOCK.now()
            for violation in self._checker.sample(now, stable=self._supervisor.stable(now)):
                self._reporter.violation(self._arena.name, violation)
            await CLOCK.sleep(SAMPLE_SECONDS)

    async def _drain_tasks(self) -> None:
        deadline = CLOCK.now() + self._drain
        reader = self._arena.reader
        assert reader is not None
        while CLOCK.now() < deadline:
            tasks = reader.list_tasks()
            if (
                tasks
                and all(task.status in TERMINAL for task in tasks)
                and not self._arena.commands().pending()
            ):
                return
            await CLOCK.sleep(DRAIN_POLL_SECONDS)

    async def _await_delivery(self) -> None:
        deadline = CLOCK.now() + timedelta(seconds=DELIVERY_WAIT_SECONDS)
        while CLOCK.now() < deadline and self._checker.undelivered():
            await CLOCK.sleep(DRAIN_POLL_SECONDS)

    async def _finish(self) -> None:
        name = self._arena.name
        code, _, stderr = await self._arena.cli("stop", "--kill-session")
        if code != 0:
            self._reporter.violation(
                name, Violation(0, None, f"stop failed with {code}: {stderr.strip()}")
            )
        if self._arena.engine_pid() is not None:
            self._reporter.violation(
                name, Violation(0, None, "the engine still runs after stop returned")
            )
        for violation in await self._checker.final(terminal_by="at the end of the drain"):
            self._reporter.violation(name, violation)
        for violation in await self._checker.at_rest():
            self._reporter.violation(name, violation)

    def _summary(self) -> ArenaSummary:
        reader = self._arena.reader
        assert reader is not None
        statuses: dict[str, int] = {}
        for task in reader.list_tasks():
            statuses[task.status.value] = statuses.get(task.status.value, 0) + 1
        scopes: dict[str, int] = {}
        for event in reader.events_since(0, kinds=[EventKind.ENGINE_FAULT]):
            scope = str(event.data.get(SCOPE_KEY))
            scopes[scope] = scopes.get(scope, 0) + 1
        effects = {"git_failures": self._arena.git_failures()}
        if self._hosting is not None:
            effects |= {"prs_landed": self._hosting.landed, "prs_closed": self._hosting.closed}
        return ArenaSummary(self._arena.name, self._tally, statuses, scopes, effects=effects)
