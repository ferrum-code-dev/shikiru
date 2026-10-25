"""Paged window over the most recent terminal tasks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecentWindow:
    """A fixed-size window at `offset` over `total` most recent tasks, newest first."""

    offset: int
    size: int
    total: int

    @property
    def shown(self) -> int:
        """Return how many tasks the window holds; the oldest page may be short."""
        return max(min(self.size, self.total - self.offset), 0)

    @property
    def paged(self) -> bool:
        """Return whether more tasks exist than one window holds."""
        return self.total > self.size

    @property
    def has_later(self) -> bool:
        """Return whether a newer page exists."""
        return self.offset > 0

    @property
    def has_earlier(self) -> bool:
        """Return whether an older page exists."""
        return self.offset + self.size < self.total

    def at_page_of(self, position: int) -> RecentWindow:
        """Return the window whose page holds the task at absolute `position`."""
        return RecentWindow(position // self.size * self.size, self.size, self.total)

    def clamped(self) -> RecentWindow:
        """Return the window moved to the last page when `offset` lies past the end."""
        return self.at_page_of(max(min(self.offset, self.total - 1), 0))


@dataclass(frozen=True, slots=True)
class StepTarget:
    """Where Up/Down lands: a visible row, or a Recent task on another page."""

    visible_index: int | None = None
    recent_position: int | None = None


def step_selection(
    active_count: int,
    window: RecentWindow,
    current: int | None,
    step: int,
) -> StepTarget:
    """Step one row through active rows then the visible Recent rows, paging at window edges.

    `current` indexes that visible order, or is `None` when the selection is not visible.
    Entering Recent from the active rows never pages; leaving the window pages by one task.
    """
    count = active_count + window.shown
    if current is None:
        return StepTarget(visible_index=0 if step > 0 else count - 1)
    if window.shown:
        if current == count - 1 and step > 0:
            if window.has_earlier:
                return StepTarget(recent_position=window.offset + window.shown)
            if active_count == 0:
                return StepTarget(recent_position=0)
        if current == active_count and step < 0:
            if window.has_later:
                return StepTarget(recent_position=window.offset - 1)
            if active_count == 0:
                return StepTarget(recent_position=window.total - 1)
    return StepTarget(visible_index=(current + step) % count)
