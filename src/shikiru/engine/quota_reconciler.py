"""Revalidates persisted quota blocks against fresh evidence from the agent plugins."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import quota_probe_for
from shikiru.domain.events import EventKind
from shikiru.domain.quota_blocks import block_released

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from shikiru.agents.contracts import QuotaWindow
    from shikiru.clock import Clock
    from shikiru.config.settings import QuotaRefreshConfig
    from shikiru.engine.agents import AgentPool
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore

QUOTA_REFRESH_SCOPE = "quota refresh"
QUOTA_RELEASE_REASON = "newer quota reading shows available quota"


class QuotaReconciler:
    """Releases a quota block when a newer applicable reading shows its quota is available.

    A reading counts only when it was observed after the block and covers every window the
    block was exhausted on. Out-of-turn probes run on a bounded interval; an explicit
    requeue forces one, and a probe that fails is recorded as an engine fault.
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
    ) -> None:
        """Probe through `agents`, persist in `quotas` and record outcomes in `events`."""
        self._quotas = quotas
        self._agents = agents
        self._events = events
        self._clock = clock
        self._settings = settings
        self._faults = faults
        self._probed_at: dict[str, datetime] = {}

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

    async def refresh(self, agent: str, *, demanded_at: datetime | None = None) -> None:
        """Probe `agent`'s plugin for fresh quota evidence while its block is in effect.

        Probes at most once per interval, unless `demanded_at`, such as a task requeue, is
        later than the last probe. An agent without the capability keeps its block.
        """
        now = self._clock.now()
        block = self._quotas.block_for(agent)
        if block is None or block.until <= now:
            return
        last = self._probed_at.get(agent)
        interval = timedelta(seconds=self._settings.interval_seconds)
        demanded = demanded_at is not None and (last is None or demanded_at > last)
        if last is not None and now - last < interval and not demanded:
            return
        self._probed_at[agent] = now
        async with self._faults.isolate(f"{QUOTA_REFRESH_SCOPE} {agent}"):
            probe = quota_probe_for(self._agents.get(agent))
            if probe is None:
                return
            async with asyncio.timeout(self._settings.timeout_seconds):
                reading = await probe.read_quota(block.blocked_at)
            if reading is not None:
                self.observe(agent, reading.windows)

    @property
    def interval_seconds(self) -> float:
        """Return the longest an obsolete block can go unnoticed while someone waits on it."""
        return self._settings.interval_seconds
