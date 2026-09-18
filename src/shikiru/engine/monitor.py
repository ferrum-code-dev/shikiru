"""`SessionMonitor`: the sole consumer of `AgentSession.signals()` (03 §4.2, §9).

It fans every signal out to subscriber queues (`subscribe()`), so nothing else ever reads
`session.signals()` directly. Alongside the agent's own `AgentSignal`s, the fan-out also
carries three signals the monitor derives itself: `PaneDied` (pane process gone), `Stalled`
(no activity within the configured timeout, or a launch that never reached `SessionStarted`)
and `InterventionSet` (a typed prompt just set the intervention hold). `engine.decorations`
subscribes to the same fan-out (06 §5.3); the monitor itself knows nothing about colours or
glyphs.
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
from shikiru.domain.recovery import PaneDied, Stalled
from shikiru.domain.tasks import Hold

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime
    from pathlib import Path
    from typing import Any

    from shikiru.agents.contracts import AgentSession, AgentSignal, PaneHandle, Usage
    from shikiru.clock import Clock
    from shikiru.config.settings import TimeoutsConfig
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore


@dataclass(frozen=True)
class InterventionSet:
    """A typed, non-injected prompt set the intervention hold on this session's task (03 §9)."""


type MonitorSignal = AgentSignal | PaneDied | Stalled | InterventionSet

_STOP = object()


class InjectedLines:
    """Lines a `TurnRunner` is currently injecting into one session's pane (03 §9).

    `SessionManager` creates one per live session and hands it to that session's
    `SessionMonitor`, which checks arriving prompts against it. `TurnRunner` records a line
    with `add()` before injecting it and `discard()`s it once its `PromptSubmitted` arrives or
    the wait for it ends, so a line pending on one session is never visible to another
    session's monitor.
    """

    def __init__(self) -> None:
        self._lines: set[str] = set()

    def add(self, line: str) -> None:
        """Record `line` as currently being injected."""
        self._lines.add(line)

    def discard(self, line: str) -> None:
        """Stop recording `line`, if it was recorded."""
        self._lines.discard(line)

    def __contains__(self, line: object) -> bool:
        return line in self._lines

    def __iter__(self) -> Iterator[str]:
        return iter(self._lines)

    def __len__(self) -> int:
        return len(self._lines)


class SessionMonitor:
    """Polls and fans out one live session's signals, persists its facts, and flags holds.

    `pending_lines` is the `InjectedLines` of whichever `TurnRunner` turn is currently live on
    this session, so a typed prompt matching a line it is injecting isn't mistaken for
    operator input.
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
        pending_lines: InjectedLines | None = None,
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
        self._pending_lines = pending_lines if pending_lines is not None else InjectedLines()
        self._subscribers: list[asyncio.Queue[MonitorSignal]] = []
        self._last_activity: datetime = clock.now()
        self._tool_running = False
        self._started = False
        self._stalled = False
        self._last_message: str | None = None
        self._persisted_usage: Usage | None = None
        self._persisted_snapshot: Mapping[str, Any] | None = None
        self._persisted_message: str | None = None

    def subscribe(self) -> asyncio.Queue[MonitorSignal]:
        """Register a new subscriber queue and return it."""
        queue: asyncio.Queue[MonitorSignal] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[MonitorSignal]) -> None:
        """Stop fanning signals out to `queue`."""
        self._subscribers.remove(queue)

    def rearm_stall_detection(self) -> None:
        """Start a fresh stall deadline from the clock's current time."""
        self._last_activity = self._clock.now()
        self._stalled = False

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

    def _publish_stalled(self, screen: str) -> None:
        if self._stalled:
            return
        self._stalled = True
        self._publish(Stalled(screen=screen))

    def _mark_activity(self, now: datetime) -> None:
        self._last_activity = now
        self._stalled = False

    async def _consume_signals(self) -> None:
        async for signal in self._session.signals():
            self._publish(signal)
            if self._handle_signal(signal, self._clock.now()) is _STOP:
                return

    def _handle_signal(self, signal: AgentSignal, now: datetime) -> object:
        match signal:
            case SessionStarted():
                self._started = True
                self._mark_activity(now)
            case Activity(tool_running=tool_running):
                self._tool_running = tool_running
                self._mark_activity(now)
            case PromptSubmitted(text=text):
                self._mark_activity(now)
                self._handle_prompt_submitted(text)
            case TurnEnded(last_message=last_message):
                self._mark_activity(now)
                self._tool_running = False
                self._last_message = last_message
            case TurnFailed():
                self._mark_activity(now)
                self._tool_running = False
            case Compacted():
                self._mark_activity(now)
            case AttentionNeeded(message=message):
                self._publish_stalled(message)
            case SessionEnded():
                return _STOP
        return None

    def _handle_prompt_submitted(self, text: str) -> None:
        if not is_operator_prompt(text, self._run_dir, self._pending_lines):
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
                self._publish_stalled(await self._pane.capture_visible())
                continue
            await self._persist_facts()
            if is_stalled(self._last_activity, self._tool_running, now, self._timeouts):
                self._publish_stalled(await self._pane.capture_visible())

    async def _persist_facts(self) -> None:
        usage = await self._session.usage()
        if usage != self._persisted_usage:
            self._runs.record_usage(self._run_id, usage)
            self._persisted_usage = usage
        snapshot = dict(self._session.snapshot())
        if snapshot != self._persisted_snapshot:
            self._runs.set_agent_state(self._run_id, snapshot)
            self._persisted_snapshot = snapshot
        if self._last_message is not None and self._last_message != self._persisted_message:
            self._runs.set_last_message(self._run_id, self._last_message)
            self._persisted_message = self._last_message

    def _set_intervention(self) -> None:
        task = self._tasks.get(self._task_id)
        if task is None:
            message = f"{self._task_id} has a live session but no task row"
            raise RuntimeError(message)
        if Hold.INTERVENTION in task.holds:
            return
        self._tasks.set_holds(self._task_id, task.holds | {Hold.INTERVENTION})
