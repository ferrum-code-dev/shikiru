"""Keeps one pane's `@shk_color`/`@shk_glyph` in sync with its `SessionMonitor` (06 §5.3).

A subscriber of the monitor's fan-out, like any other (`SessionMonitor.subscribe()`); the
monitor itself has no notion of colours or glyphs.

Decoration rule (03 §9, 06 §5.3): `Stalled` and `InterventionSet` both hold their decoration
(stalled amber, operator magenta) until something clears it. An `Activity` signal clears a
*stalled* decoration back to the role's colour and glyph, since that is the pane coming back to
life; it never clears an *intervention* decoration, because the hold it marks lasts until the
current turn ends with a valid report or the task is resumed (03 §9) — clearing it here is out
of scope; that's Task 13/14's job. `PaneDecorator` is only called when the decoration changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Activity
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.engine.monitor import InterventionCleared, InterventionSet
from shikiru.errors import CommandFailed
from shikiru.ui.glyphs import GLYPH_OPERATOR, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.tokens import HOLD_COLOR, OPERATOR_COLOR, ROLE_COLORS

if TYPE_CHECKING:
    import asyncio

    from shikiru.domain.tasks import Role
    from shikiru.engine.monitor import MonitorSignal
    from shikiru.tmux.decorator import PaneDecorator


class _Mode(Enum):
    ROLE = auto()
    STALLED = auto()
    INTERVENTION = auto()


@dataclass(frozen=True)
class _Decoration:
    mode: _Mode
    color: str
    glyph: str


def _role_decoration(role: Role) -> _Decoration:
    return _Decoration(_Mode.ROLE, ROLE_COLORS[role], ROLE_GLYPHS[role])


def _next_decoration(signal: MonitorSignal, current: _Decoration, role: Role) -> _Decoration | None:
    if isinstance(signal, InterventionSet):
        return _Decoration(_Mode.INTERVENTION, OPERATOR_COLOR, GLYPH_OPERATOR)
    if isinstance(signal, InterventionCleared):
        return _role_decoration(role)
    if current.mode is _Mode.INTERVENTION:
        return None
    if isinstance(signal, Stalled):
        return _Decoration(_Mode.STALLED, HOLD_COLOR, GLYPH_STALLED)
    if current.mode is _Mode.STALLED and isinstance(signal, Activity):
        return _role_decoration(role)
    return None


async def decorate_pane(
    subscription: asyncio.Queue[MonitorSignal],
    *,
    pane_target: str,
    role: Role,
    decorator: PaneDecorator,
) -> None:
    """Keep pane `pane_target`'s `@shk_color`/`@shk_glyph` in sync with `subscription` (06 §5.3).

    Stops decorating once the pane has died or disappeared: nothing further is worth drawing
    on it until `SessionManager` recreates it.
    """
    current = _role_decoration(role)
    try:
        await decorator.set_pane_state(pane_target, color=current.color, glyph=current.glyph)
        while True:
            signal = await subscription.get()
            if isinstance(signal, PaneDied):
                return
            next_decoration = _next_decoration(signal, current, role)
            if next_decoration is None:
                continue
            current = next_decoration
            await decorator.set_pane_state(pane_target, color=current.color, glyph=current.glyph)
    except CommandFailed:
        return
