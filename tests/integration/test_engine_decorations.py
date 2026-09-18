"""`decorate_pane`: pane decoration rule against real `@shk_color`/`@shk_glyph` (06 §5.3)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Activity
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.domain.tasks import Role
from shikiru.engine.decorations import InterventionSet, decorate_pane
from shikiru.errors import CommandFailed
from shikiru.tmux.decorator import PANE_COLOR_OPTION, PANE_GLYPH_OPTION, PaneDecorator
from shikiru.ui.glyphs import GLYPH_OPERATOR, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.tokens import HOLD_COLOR, OPERATOR_COLOR, ROLE_COLORS
from tests.support.engine import build_tmux, new_agent_pane
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.engine.monitor import MonitorSignal
    from shikiru.tmux.client import Tmux
    from tests.support.tmux import TmuxServer

_WAIT_TIMEOUT = 15.0
_ROLE = Role.IMPLEMENTER
_ROLE_COLOR = ROLE_COLORS[_ROLE]
_ROLE_GLYPH = ROLE_GLYPHS[_ROLE]


async def _decoration(tmux: Tmux, pane_target: str) -> tuple[str, str]:
    color = await tmux.show_pane_option(pane_target, PANE_COLOR_OPTION)
    glyph = await tmux.show_pane_option(pane_target, PANE_GLYPH_OPTION)
    return color, glyph


async def _decorated_as(tmux: Tmux, pane_target: str, color: str, glyph: str) -> bool:
    try:
        decoration = await _decoration(tmux, pane_target)
    except CommandFailed:
        return False
    return decoration == (color, glyph)


def _run(tmux: Tmux, pane_target: str) -> tuple[asyncio.Queue[MonitorSignal], asyncio.Task[None]]:
    queue: asyncio.Queue[MonitorSignal] = asyncio.Queue()
    task = asyncio.ensure_future(
        decorate_pane(queue, pane_target=pane_target, role=_ROLE, decorator=PaneDecorator(tmux))
    )
    return queue, task


async def test_role_decoration_is_set_as_soon_as_decorating_starts(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    pane_target = await new_agent_pane(tmux, tmp_path)
    _queue, task = _run(tmux, pane_target)
    try:
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, _ROLE_COLOR, _ROLE_GLYPH),
            timeout=_WAIT_TIMEOUT,
        )
    finally:
        task.cancel()


async def test_stall_holds_its_decoration_until_the_next_activity(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    pane_target = await new_agent_pane(tmux, tmp_path)
    queue, task = _run(tmux, pane_target)
    try:
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, _ROLE_COLOR, _ROLE_GLYPH),
            timeout=_WAIT_TIMEOUT,
        )

        queue.put_nowait(Stalled(screen="stuck"))
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, HOLD_COLOR, GLYPH_STALLED),
            timeout=_WAIT_TIMEOUT,
        )

        queue.put_nowait(Activity(tool_running=False))
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, _ROLE_COLOR, _ROLE_GLYPH),
            timeout=_WAIT_TIMEOUT,
        )
    finally:
        task.cancel()


async def test_intervention_decoration_survives_activity_until_something_else_clears_it(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    pane_target = await new_agent_pane(tmux, tmp_path)
    queue, task = _run(tmux, pane_target)
    try:
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, _ROLE_COLOR, _ROLE_GLYPH),
            timeout=_WAIT_TIMEOUT,
        )

        queue.put_nowait(InterventionSet())
        await wait_until(
            lambda: _decorated_as(tmux, pane_target, OPERATOR_COLOR, GLYPH_OPERATOR),
            timeout=_WAIT_TIMEOUT,
        )

        queue.put_nowait(Activity(tool_running=False))
        queue.put_nowait(Stalled(screen="stuck"))
        await wait_until(queue.empty, timeout=_WAIT_TIMEOUT)
        assert await _decoration(tmux, pane_target) == (OPERATOR_COLOR, GLYPH_OPERATOR)
    finally:
        task.cancel()


async def test_pane_died_stops_decorating(tmux_server: TmuxServer, tmp_path: Path) -> None:
    tmux = build_tmux(tmux_server)
    pane_target = await new_agent_pane(tmux, tmp_path)
    queue, task = _run(tmux, pane_target)

    queue.put_nowait(PaneDied())
    await asyncio.wait_for(task, timeout=_WAIT_TIMEOUT)
