"""Keeps one pane's `@shk_color`/`@shk_glyph` in sync with its `SessionMonitor` (06 §5.3).

A subscriber of the monitor's fan-out, like any other (`SessionMonitor.subscribe()`); the
monitor itself has no notion of colours or glyphs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.engine.monitor import InterventionSet, PaneDied, Stalled
from shikiru.ui.glyphs import GLYPH_OPERATOR, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.tokens import HOLD_COLOR, ROLE_COLORS

if TYPE_CHECKING:
    import asyncio

    from shikiru.domain.tasks import Role
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.tmux.decorator import PaneDecorator


def _pane_decoration(signal: MonitorSignal, role: Role) -> tuple[str, str] | None:
    if isinstance(signal, Stalled):
        return HOLD_COLOR, GLYPH_STALLED
    if isinstance(signal, InterventionSet):
        return HOLD_COLOR, GLYPH_OPERATOR
    if isinstance(signal, PaneDied):
        return None
    return ROLE_COLORS[role], ROLE_GLYPHS[role]


async def decorate_pane(
    subscription: asyncio.Queue[MonitorSignal],
    *,
    pane_target: str,
    role: Role,
    decorator: PaneDecorator,
) -> None:
    """Keep pane `pane_target`'s `@shk_color`/`@shk_glyph` in sync with `subscription` (06 §5.3).

    Stops decorating once the pane has died: nothing further is worth drawing on it until
    `SessionManager` recreates it.
    """
    await decorator.set_pane_state(pane_target, color=ROLE_COLORS[role], glyph=ROLE_GLYPHS[role])
    while True:
        signal = await subscription.get()
        decoration = _pane_decoration(signal, role)
        if decoration is None:
            return
        color, glyph = decoration
        await decorator.set_pane_state(pane_target, color=color, glyph=glyph)
