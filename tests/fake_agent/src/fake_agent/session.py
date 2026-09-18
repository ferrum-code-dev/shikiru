"""`AgentSession` for the fake agent: tails its own `hooks.jsonl` (04 §1, §3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fake_agent.signals import signal_from_entry, usage_from_entries
from shikiru.agents.contracts import role_dir
from shikiru.agents.sdk.hooks import HookRecorder

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from shikiru.agents.contracts import AgentSignal, Usage
    from shikiru.agents.sdk.prices import PriceTable
    from shikiru.agents.sdk.reset_time import ResetTimeParser
    from shikiru.clock import Clock

POLL_INTERVAL_SECONDS = 0.05


class FakeSession:
    """The fake agent's `AgentSession`, backed entirely by its role directory's `hooks.jsonl`.

    `signals()` yields only entries appended from `start_offset` onward, so a `launch()`ed
    session never replays whatever an earlier session already wrote to the same role
    directory, and an `attach()`ed session never replays what it already consumed itself
    before a prior `snapshot()`. `usage()` still sums this session id's `turn_ended` entries
    across the *whole* file, so a resumed session's usage includes its pre-exit turns.
    """

    def __init__(
        self,
        run_dir: Path,
        role: str,
        session_id: str,
        start_offset: int,
        *,
        clock: Clock,
        reset_time: ResetTimeParser,
        prices: PriceTable,
        model: str,
    ) -> None:
        """Tail `run_dir/role/hooks.jsonl` from `start_offset`, polled by `clock`."""
        self._recorder = HookRecorder(role_dir(run_dir, role))
        self._session_id = session_id
        self._offset = start_offset
        self._clock = clock
        self._reset_time = reset_time
        self._prices = prices
        self._model = model

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        """Yield every hook entry appended since this session's start, for its one reader."""
        while True:
            advanced = False
            for entry in self._recorder.tail(self._offset):
                self._offset = entry.offset
                advanced = True
                yield signal_from_entry(entry.value, self._reset_time)
            if not advanced:
                await self._clock.sleep(POLL_INTERVAL_SECONDS)

    async def before_turn(self) -> list[str]:
        """Return no pre-turn lines: the fake agent never needs to compact."""
        return []

    async def usage(self) -> Usage:
        """Recompute this session id's cumulative usage from the whole hooks file."""
        entries = [entry.value for entry in self._recorder.tail(0)]
        return usage_from_entries(
            entries, session_id=self._session_id, model=self._model, prices=self._prices
        )

    def snapshot(self) -> Mapping[str, Any]:
        """Return this session's id and its current read offset, for `attach()` to resume from."""
        return {"session_id": self._session_id, "offset": self._offset}
