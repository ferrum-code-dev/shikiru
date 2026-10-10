"""The instant shared by every motion callback of one tick."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Hashable
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


class TickMemo:
    """Values built once per tick and data version, never reused outside a tick."""

    def __init__(self, data_version: Callable[[], int]) -> None:
        """Key the memo on the tick and on the `data_version` it reads."""
        self._data_version = data_version
        self._key: tuple[datetime, int] | None = None
        self._values: dict[Hashable, object] = {}

    def get[V](self, name: Hashable, load: Callable[[], V]) -> V:
        """Return the value for `name` built by `load` earlier in this tick, else build it."""
        tick = _TICK_INSTANT.get()
        if tick is None:
            return load()
        key = (tick, self._data_version())
        if key != self._key:
            self._key = key
            self._values = {}
        if name not in self._values:
            self._values[name] = load()
        return cast("V", self._values[name])
