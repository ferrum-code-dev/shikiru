"""Periodic agent quota polling (02 §2, 03 §7)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

QUOTA_POLL_SECONDS = 60

if TYPE_CHECKING:
    from shikiru.agents.contracts import InteractiveAgent
    from shikiru.clock import Clock
    from shikiru.engine.agents import AgentPool
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore


class QuotaPoller:
    """Polls each agent used by a live run and persists its quota windows."""

    def __init__(
        self,
        agents: AgentPool,
        runs: RunStore,
        quotas: QuotaStore,
        clock: Clock,
        *,
        interval_seconds: int = QUOTA_POLL_SECONDS,
    ) -> None:
        """Use `agents`, `runs`, `quotas` and `clock` for periodic quota snapshots."""
        self._agents = agents
        self._runs = runs
        self._quotas = quotas
        self._clock = clock
        self._logger = logging.getLogger(__name__)
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._poll_lock = asyncio.Lock()

    async def poll_once(self) -> None:
        """Poll every distinct agent represented by a live run."""
        async with self._poll_lock:
            names = sorted({run.agent for run in self._runs.list_live()})
            for name in names:
                try:
                    agent = self._agents.get(name)
                    await self._record(name, agent)
                except Exception:
                    self._logger.exception("quota polling failed for agent %s", name)

    async def run(self) -> None:
        """Poll immediately, then continue at the configured interval until cancelled."""
        while True:
            await self.poll_once()
            await self._clock.sleep(self._interval_seconds)

    def start(self) -> asyncio.Task[None]:
        """Start the background poller and return its task."""
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        """Cancel and await the background poller, if started."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _record(self, name: str, agent: InteractiveAgent) -> None:
        for window in await agent.quota():
            self._quotas.record(name, window)
