"""Entry-point doubles for plugin discovery tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeDistribution:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeEntryPoint:
    """An entry point that must never be loaded: it exists to be ambiguous."""

    def __init__(self, name: str, dist_name: str) -> None:
        self.name = name
        self.dist = FakeDistribution(dist_name)

    def load(self) -> None:
        message = "an ambiguous entry point must never be loaded"
        raise AssertionError(message)


def ambiguous_entry_points(name: str, *dist_names: str) -> Callable[..., list[FakeEntryPoint]]:
    entries = [FakeEntryPoint(name, dist_name) for dist_name in dist_names]

    def entry_points(*, group: str) -> list[FakeEntryPoint]:
        del group
        return entries

    return entry_points
