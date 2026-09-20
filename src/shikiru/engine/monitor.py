"""`SessionMonitor`: the sole consumer of `AgentSession.signals()` (03 §4.2, §9).

It fans every signal out to subscriber queues (`subscribe()`), so nothing else ever reads
`session.signals()` directly. Alongside the agent's own `AgentSignal`s, the fan-out also
carries four signals the monitor derives itself: `PaneDied` (pane process gone), `Stalled`
(no activity within the configured timeout, or a launch that never reached `SessionStarted`),
`InterventionSet` and `InterventionCleared` (the intervention hold transitions).
`engine.decorations` subscribes to the same fan-out (06 §5.3); the monitor itself knows nothing
about colours or glyphs.
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
from shikiru.errors import CommandFailed

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


@dataclass(frozen=True)
class InterventionCleared:
    """A task intervention hold was cleared by a valid report or operator resume (03 §9)."""


type MonitorSignal = AgentSignal | PaneDied | Stalled | InterventionSet | InterventionCleared

_STOP = object()


async def wait_for_signal(
    signals: asyncio.Queue[MonitorSignal], clock: Clock, timeout_seconds: float | None
) -> MonitorSignal | None:
    """Wait for one monitor signal, or return `None` after `timeout_seconds`."""
    if timeout_seconds is None:
        return await signals.get()
    get_task = asyncio.ensure_future(signals.get())
    sleep_task = asyncio.ensure_future(clock.sleep(timeout_seconds))
    try:
        await asyncio.wait({get_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED)
        if get_task.done():
            return get_task.result()
        return None
    finally:
        if not get_task.done():
            get_task.cancel()
            with suppress(asyncio.CancelledError):
                await get_task
        if not sleep_task.done():
            sleep_task.cancel()
            with suppress(asyncio.CancelledError):
                await sleep_task


class InjectedLines:
    """Lines a `TurnRunner` is currently injecting into one session's pane (03 §9).

    `SessionManager` creates one per live session and hands it to that session's
    `SessionMonitor`, which consumes each matching `PromptSubmitted`. A line remains pending
    after a caller's bounded wait until the agent accepts it or the session terminates.
    """

    def __init__(self) -> None:
        self._lines: dict[str, int] = {}

    def add(self, line: str) -> None:
        """Record `line` as currently being injected."""
        self._lines[line] = self._lines.get(line, 0) + 1

    def discard(self, line: str) -> None:
        """Stop recording one pending `line`, if it was recorded."""
        count = self._lines.get(line)
        if count is None:
            return
        if count == 1:
            del self._lines[line]
        else:
            self._lines[line] = count - 1

    def consume(self, text: str) -> bool:
        """Consume one pending line when the agent confirms its exact text."""
        if text not in self._lines:
            return False
        self.discard(text)
        return True

    def clear(self) -> None:
        """Forget every pending line when its session terminates."""
        self._lines.clear()

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
        self._started_session_id: str | None = None
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

    def clear_intervention(self) -> None:
        """Publish the transition back to the role decoration after an intervention clears."""
        self._publish(InterventionCleared())

    @property
    def started_session_id(self) -> str | None:
        """Return the session id observed from `SessionStarted`, if one was provided."""
        return self._started_session_id

    @property
    def launch_pending(self) -> bool:
        """Return whether this monitor is still waiting for `SessionStarted`."""
        return self._expects_started and not self._started

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
                try:
                    task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    self._publish_pane_died()
                    return
        except asyncio.CancelledError:
            consume.cancel()
            tick.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(consume, tick)
            raise
        except Exception:  # noqa: BLE001
            self._publish_pane_died()

    def _publish(self, signal: MonitorSignal) -> None:
        for queue in self._subscribers:
            queue.put_nowait(signal)

    def _publish_pane_died(self) -> None:
        self._pending_lines.clear()
        self._publish(PaneDied())

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
                self._publish_pane_died()
                return
        self._publish_pane_died()

    def _handle_signal(self, signal: AgentSignal, now: datetime) -> object:
        match signal:
            case SessionStarted():
                self._started = True
                self._started_session_id = signal.session_id
                self._expects_started = False
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
        if self._pending_lines.consume(text):
            return
        if not is_operator_prompt(text, self._run_dir, self._pending_lines):
            return
        self._set_intervention()
        self._publish(InterventionSet())

    async def _tick_loop(self) -> None:
        launch_deadline = self._clock.now() + timedelta(seconds=self._timeouts.launch)
        while True:
            await self._clock.sleep(self._poll_seconds)
            try:
                pane_alive = await self._pane.is_alive()
            except CommandFailed:
                self._publish_pane_died()
                return
            if not pane_alive:
                self._publish_pane_died()
                return
            now = self._clock.now()
            if self._expects_started and not self._started and now >= launch_deadline:
                try:
                    screen = await self._pane.capture_visible()
                except CommandFailed:
                    self._publish_pane_died()
                    return
                self._publish_stalled(screen)
                continue
            await self._persist_facts()
            if is_stalled(self._last_activity, self._tool_running, now, self._timeouts):
                try:
                    screen = await self._pane.capture_visible()
                except CommandFailed:
                    self._publish_pane_died()
                    return
                self._publish_stalled(screen)

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
