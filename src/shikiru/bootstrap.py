"""The composition root. Assembles concrete components for each entry point (02 §4)."""

from __future__ import annotations

from shikiru.clock import Clock, SystemClock


def build_clock() -> Clock:
    """Return the `Clock` implementation used by every real entry point."""
    return SystemClock()
