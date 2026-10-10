"""The instant shared by every motion callback of one tick."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime

    from shikiru.clock import Clock

_TICK_INSTANT: ContextVar[datetime | None] = ContextVar("tick_instant", default=None)


def current_tick() -> datetime | None:
    """Return the instant of the tick the caller runs in, or None outside any tick."""
    return _TICK_INSTANT.get()


def tick_now(clock: Clock) -> datetime:
    """Return the tick's instant inside a tick, otherwise the clock's current time."""
    instant = _TICK_INSTANT.get()
    return instant if instant is not None else clock.now()


@contextmanager
def pinned_tick(instant: datetime) -> Generator[None]:
    """Make tasks created inside the block see `instant` as their tick."""
    token = _TICK_INSTANT.set(instant)
    try:
        yield
    finally:
        _TICK_INSTANT.reset(token)
