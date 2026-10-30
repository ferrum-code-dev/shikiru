"""A repeating widget timer that restarts when its configured period changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from textual.timer import Timer
    from textual.widget import Widget


class LiveInterval:
    """Run `tick` every `period()` seconds, restarting the timer only when that period changes."""

    def __init__(
        self,
        widget: Widget,
        period: Callable[[], float],
        tick: Callable[[], Awaitable[None]],
    ) -> None:
        """Hold the timer target; nothing runs until `apply`."""
        self._widget = widget
        self._period = period
        self._tick = tick
        self._applied: float | None = None
        self._timer: Timer | None = None

    def apply(self) -> None:
        """Tick at the current period, replacing the running timer when the period differs."""
        period = self._period()
        if period == self._applied:
            return
        if self._timer is not None:
            self._timer.stop()
        self._applied = period
        self._timer = self._widget.set_interval(period, self._tick)

    async def reapply(self) -> None:
        """Apply the current period again; a state change calls this to pick up new settings."""
        self.apply()
