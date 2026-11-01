"""Revalidates persisted quota blocks against fresh evidence from the agent plugins."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import quota_probe_for
from shikiru.domain.events import EventKind
from shikiru.domain.quota_blocks import block_released
from shikiru.domain.quota_waits import QuotaAction, ScheduledQuotaAction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import QuotaProbe, QuotaReading, QuotaWindow
    from shikiru.clock import Clock
    from shikiru.config.settings import QuotaRefreshConfig
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.engine.quota_schedule import QuotaSchedule
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import AgentBlock, QuotaStore

QUOTA_REFRESH_SCOPE = "quota refresh"
QUOTA_RELEASE_REASON = "newer quota reading shows available quota"


@dataclass(frozen=True, slots=True)
class _Probe:
    blocked_at: datetime
    task: asyncio.Task[None]


class QuotaReconciler:
    """Releases a quota block when a newer applicable reading shows its quota is available.

    A reading counts only when it was observed after the block and covers every window the
    block was exhausted on. Out-of-turn probes run beside the caller on a bounded interval, one
    per block at a time and each for a bounded time; an explicit requeue forces one, and a probe
    that fails or times out is recorded as an engine fault. The result of a probe whose block
    has since changed is dropped.
    """

    def __init__(
        self,
        *,
        quotas: QuotaStore,
        agents: AgentPool,
        events: EventLog,
        clock: Clock,
        settings: QuotaRefreshConfig,
        faults: FaultReporter,
        schedule: QuotaSchedule,
    ) -> None:
        """Probe through `agents`, persist in `quotas`, record outcomes in `events`.

        The next examination or retry of each block is recorded in `schedule`.
        """
        self._quotas = quotas
        self._agents = agents
        self._events = events
        self._clock = clock
        self._settings = settings
        self._faults = faults
        self._probed_at: dict[str, datetime] = {}
        self._probes: dict[str, _Probe] = {}
        self._schedule = schedule

    def observe(self, agent: str, windows: Sequence[QuotaWindow]) -> None:
        """Record `windows` for `agent`, first releasing its block when they prove it obsolete."""
        now = self._clock.now()
        block = self._quotas.block_for(agent)
        if (
            block is not None
            and block.until > now
            and block_released(
                blocked_at=block.blocked_at,
                stored=self._quotas.windows_for(agent),
                reading=windows,
                now=now,
            )
        ):
            self._quotas.clear_block(agent)
            self._events.append(
                EventKind.QUOTA_CLEARED,
                message=f"{agent} quota block cleared",
                data={"agent": agent, "reason": QUOTA_RELEASE_REASON},
            )
        for window in windows:
            self._quotas.record(agent, window)

    def request_refresh(
        self, agent: str, *, demanded_at: datetime | None = None
    ) -> asyncio.Task[None] | None:
        """Start a supervised probe of `agent`'s plugin while its block is in effect.

        Never waits for the probe. Probes at most once per interval, unless `demanded_at`, such
        as a task requeue, is later than the last probe, and never twice at once for the same
        block; a probe of a block since replaced is cancelled. Returns the running probe, or
        `None` when nothing was started or the block is not examined.
        """
        now = self._clock.now()
        block = self._quotas.block_for(agent)
        if block is None or block.until <= now:
            return None
        running = self._probes.get(agent)
        if running is not None and not running.task.done():
            if running.blocked_at == block.blocked_at:
                return running.task
            running.task.cancel()
        last = self._probed_at.get(agent)
        interval = timedelta(seconds=self._settings.interval_seconds)
        demanded = demanded_at is not None and (last is None or demanded_at > last)
        if last is not None and now - last < interval and not demanded:
            return None
        self._probed_at[agent] = now
        task = asyncio.Task(self._probe(agent, block, now + interval), eager_start=True)
        self._probes[agent] = _Probe(block.blocked_at, task)
        return task

    async def refresh(self, agent: str, *, demanded_at: datetime | None = None) -> None:
        """Probe `agent`'s plugin as `request_refresh` does and wait until the probe ends."""
        task = self.request_refresh(agent, demanded_at=demanded_at)
        if task is not None:
            await asyncio.wait({task})

    @property
    def probing(self) -> bool:
        """Return whether any probe is still running."""
        return any(not probe.task.done() for probe in self._probes.values())

    async def shutdown(self) -> None:
        """Cancel every probe in flight and wait until each has ended."""
        tasks = [probe.task for probe in self._probes.values()]
        self._probes.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe(self, agent: str, block: AgentBlock, next_check: datetime) -> None:
        async with self._faults.isolate(f"{QUOTA_REFRESH_SCOPE} {agent}"):
            self.expect_check(agent, next_check)
            probe = quota_probe_for(self._agents.get(agent))
            if probe is None:
                return
            reading = await self._read(probe, block.blocked_at)
            current = self._quotas.block_for(agent)
            if (
                reading is not None
                and current is not None
                and current.blocked_at == block.blocked_at
            ):
                self.observe(agent, reading.windows)

    async def _read(self, probe: QuotaProbe, blocked_at: datetime) -> QuotaReading | None:
        timeout = self._settings.timeout_seconds
        read = asyncio.create_task(probe.read_quota(blocked_at))
        timer = asyncio.create_task(self._clock.sleep(timeout))
        try:
            await asyncio.wait({read, timer}, return_when=asyncio.FIRST_COMPLETED)
            if not read.done():
                message = f"quota probe did not answer within {timeout} seconds"
                raise TimeoutError(message)
            return read.result()
        finally:
            read.cancel()
            timer.cancel()
            await asyncio.gather(read, timer, return_exceptions=True)

    def expect_check(self, agent: str, at: datetime) -> None:
        """Record that `agent`'s block is next examined at `at`, or retried when it lifts first.

        An agent whose plugin cannot probe is not examined: its block is retried when it lifts.
        """
        block = self._quotas.block_for(agent)
        if block is None:
            return
        if block.until <= at or quota_probe_for(self._agents.get(agent)) is None:
            scheduled = ScheduledQuotaAction(QuotaAction.RETRY, block.until, block.blocked_at)
        else:
            scheduled = ScheduledQuotaAction(QuotaAction.NEXT_CHECK, at, block.blocked_at)
        self._schedule.record(agent, scheduled)

    @property
    def interval_seconds(self) -> float:
        """Return the longest an obsolete block can go unnoticed while someone waits on it."""
        return self._settings.interval_seconds
