"""`AgentSession` for the fake agent: tails its own `hooks.jsonl` (04 §1, §3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fake_agent.signals import signal_from_entry, usage_from_entries
from shikiru.agents.sdk.jsonl import JsonlTail

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from shikiru.agents.contracts import AgentSignal, Usage
    from shikiru.agents.sdk.prices import PriceTable
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

POLL_INTERVAL_SECONDS = 0.05
HOOKS_FILENAME = "hooks.jsonl"


class FakeSession:
    """The fake agent's `AgentSession`, backed entirely by its role directory's `hooks.jsonl`."""

    def __init__(
        self,
        run_dir: Path,
        role: str,
        session_id: str,
        *,
        clock: Clock,
        reset_time: ResetTimeParser,
        prices: PriceTable,
        model: str,
    ) -> None:
        """Tail `run_dir/role/hooks.jsonl`, polled by `clock`, pricing tokens under `model`."""
        self._hooks_path = run_dir / role / HOOKS_FILENAME
        self._session_id = session_id
        self._clock = clock
        self._reset_time = reset_time
        self._prices = prices
        self._model = model

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        """Yield every hook entry as it is appended, forever, for this session's one reader."""
        tail = JsonlTail(self._hooks_path)
        offset = 0
        while True:
            advanced = False
            for entry in tail.read_from(offset):
                offset = entry.offset
                advanced = True
                yield signal_from_entry(entry.value, self._reset_time)
            if not advanced:
                await self._clock.sleep(POLL_INTERVAL_SECONDS)

    async def before_turn(self) -> list[str]:
        """Return no pre-turn lines: the fake agent never needs to compact."""
        return []

    async def usage(self) -> Usage:
        """Recompute cumulative usage from every `turn_ended` entry recorded so far."""
        entries = [entry.value for entry in JsonlTail(self._hooks_path).read_from(0)]
        return usage_from_entries(entries, model=self._model, prices=self._prices)

    def snapshot(self) -> Mapping[str, Any]:
        """Return this session's id, the only state `attach()` needs to resume it."""
        return {"session_id": self._session_id}
