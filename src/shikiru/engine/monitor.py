"""`SessionMonitor`: the sole consumer of `AgentSession.signals()` (03 §4.2, §9).

It fans every signal out to subscriber queues (`subscribe()`), so nothing else ever reads
`session.signals()` directly; `PaneDecorator` is one such subscriber (`decorate_pane`, per
06 §5.3). Alongside the agent's own `AgentSignal`s, the fan-out also carries three signals the
monitor derives itself: `PaneDied` (pane process gone), `Stalled` (no activity within the
configured timeout, or a launch that never reached `SessionStarted`) and `InterventionSet` (a
typed prompt just set the intervention hold).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    Activity,
    AttentionNeeded,
    Compacted,
    PromptSubmitted,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
)
from shikiru.domain.activity import is_operator_prompt, is_stalled
from shikiru.domain.tasks import Hold
from shikiru.ui.glyphs import GLYPH_OPERATOR, GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.tokens import HOLD_COLOR, ROLE_COLORS

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import datetime
    from pathlib import Path

    from shikiru.agents.contracts import AgentSession, AgentSignal, PaneHandle
    from shikiru.clock import Clock
    from shikiru.config.settings import TimeoutsConfig
    from shikiru.domain.tasks import Role
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.decorator import PaneDecorator


@dataclass(frozen=True)
class PaneDied:
    """The session's pane process is no longer running (03 §4.2)."""


@dataclass(frozen=True)
class Stalled:
    """No activity within the configured timeout, or a launch that never started (03 §4.2)."""

    screen: str


@dataclass(frozen=True)
class InterventionSet:
    """A typed, non-injected prompt set the intervention hold on this session's task (03 §9)."""


type MonitorSignal = AgentSignal | PaneDied | Stalled | InterventionSet

_STOP = object()


def no_pending_lines() -> Collection[str]:
    """Report no `before_turn` lines pending injection: nothing injects any yet (03 §4.3)."""
    return ()


class SessionMonitor:
    """Polls and fans out one live session's signals, persists its facts, and flags holds.

    `pending_lines` lets a future subscriber (the turn runner, 03 §4.3) report the
    `before_turn` lines it is currently injecting, so a typed prompt arriving in that narrow
    window isn't mistaken for operator input.
    """

    def __init__(
        self,
        session: AgentSession,
        pane: PaneHandle,
        *,
        task_id: str,
        run_id: int,
        run_dir: Path,
        tasks: TaskStore,
        runs: RunStore,
        clock: Clock,
        timeouts: TimeoutsConfig,
        poll_seconds: int,
        expects_started: bool,
        pending_lines: Callable[[], Collection[str]] = no_pending_lines,
    ) -> None:
        """Monitor `session` in `pane`, persisting facts for `run_id` under `tasks`/`runs`."""
        self._session = session
        self._pane = pane
        self._task_id = task_id
        self._run_id = run_id
        self._run_dir = run_dir
        self._tasks = tasks
        self._runs = runs
        self._clock = clock
        self._timeouts = timeouts
        self._poll_seconds = poll_seconds
        self._expects_started = expects_started
        self._pending_lines = pending_lines
        self._subscribers: list[asyncio.Queue[MonitorSignal]] = []
        self._last_activity: datetime = clock.now()
        self._tool_running = False
        self._started = False
        self._last_message: str | None = None

    def subscribe(self) -> asyncio.Queue[MonitorSignal]:
        """Register a new subscriber queue and return it."""
        queue: asyncio.Queue[MonitorSignal] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[MonitorSignal]) -> None:
        """Stop fanning signals out to `queue`."""
        self._subscribers.remove(queue)

    async def run(self) -> None:
        """Consume `session.signals()` and tick until the pane dies or the session ends."""
        consume = asyncio.ensure_future(self._consume_signals())
        tick = asyncio.ensure_future(self._tick_loop())
        try:
            done, pending = await asyncio.wait({consume, tick}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
        except asyncio.CancelledError:
            consume.cancel()
            tick.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(consume, tick)
            raise

    def _publish(self, signal: MonitorSignal) -> None:
        for queue in self._subscribers:
            queue.put_nowait(signal)

    async def _consume_signals(self) -> None:
        async for signal in self._session.signals():
            self._publish(signal)
            if self._handle_signal(signal, self._clock.now()) is _STOP:
                return

    def _handle_signal(self, signal: AgentSignal, now: datetime) -> object:
        match signal:
            case SessionStarted():
                self._started = True
                self._last_activity = now
            case Activity(tool_running=tool_running):
                self._tool_running = tool_running
                self._last_activity = now
            case PromptSubmitted(text=text):
                self._last_activity = now
                self._handle_prompt_submitted(text)
            case TurnEnded(last_message=last_message):
                self._last_activity = now
                self._tool_running = False
                self._last_message = last_message
            case TurnFailed():
                self._last_activity = now
                self._tool_running = False
            case Compacted():
                self._last_activity = now
            case AttentionNeeded(message=message):
                self._publish(Stalled(screen=message))
            case SessionEnded():
                return _STOP
        return None

    def _handle_prompt_submitted(self, text: str) -> None:
        if not is_operator_prompt(text, self._run_dir, self._pending_lines()):
            return
        self._set_intervention()
        self._publish(InterventionSet())

    async def _tick_loop(self) -> None:
        launch_deadline = self._clock.now() + timedelta(seconds=self._timeouts.launch)
        while True:
            await self._clock.sleep(self._poll_seconds)
            if not await self._pane.is_alive():
                self._publish(PaneDied())
                return
            now = self._clock.now()
            if self._expects_started and not self._started and now >= launch_deadline:
                self._publish(Stalled(screen=await self._pane.capture_visible()))
                continue
            self._runs.record_usage(self._run_id, await self._session.usage())
            self._runs.set_agent_state(self._run_id, dict(self._session.snapshot()))
            if self._last_message is not None:
                self._runs.set_last_message(self._run_id, self._last_message)
            if is_stalled(self._last_activity, self._tool_running, now, self._timeouts):
                self._publish(Stalled(screen=await self._pane.capture_visible()))

    def _set_intervention(self) -> None:
        task = self._tasks.get(self._task_id)
        if task is None or Hold.INTERVENTION in task.holds:
            return
        self._tasks.set_holds(self._task_id, task.holds | {Hold.INTERVENTION})


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
