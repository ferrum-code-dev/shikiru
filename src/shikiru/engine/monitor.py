"""Consumes one headless turn stream and records its facts and live view (03 §4.2)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    AgentSignal,
    Compacted,
    Message,
    QuotaReported,
    SessionStarted,
    ToolFinished,
    ToolStarted,
    Turn,
    TurnEnded,
    TurnFailed,
    UsageReported,
    role_dir,
)
from shikiru.domain.events import EventKind
from shikiru.domain.recovery import ProtocolError, Stalled
from shikiru.ui.glyphs import GLYPH_STALLED, ROLE_GLYPHS
from shikiru.ui.tokens import HOLD_COLOR, ROLE_COLORS
from shikiru.view_log import (
    VIEW_RUNG_KEY,
    VIEW_SIGNAL_KEY,
    VIEW_TIME_KEY,
    VIEW_TURN_KEY,
    view_log_path,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from shikiru.agents.contracts import Agent, Rung
    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.sessions import SessionManager
    from shikiru.store.events import EventLog
    from shikiru.store.quotas import QuotaStore
    from shikiru.store.runs import RunStore
    from shikiru.tmux.decorator import PaneDecorator

_TURN_PID_NAME = "turn.pid"
_LINE_ENDING = "\n"


class TurnMonitor:
    """Read a turn's signals once, persist run facts and stop a stalled process."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        runs: RunStore,
        quotas: QuotaStore,
        events: EventLog,
        clock: Clock,
        settings: Settings,
        decorator: PaneDecorator,
        refresh_titles: Callable[[Task], Awaitable[None]],
    ) -> None:
        self._sessions = sessions
        self._runs = runs
        self._quotas = quotas
        self._events = events
        self._clock = clock
        self._settings = settings
        self._decorator = decorator
        self._refresh_titles = refresh_titles

    async def run(
        self,
        turn: Turn,
        *,
        agent: Agent,
        task: Task,
        role: Role,
        rung: Rung,
        run_id: int,
        turn_dir: Path,
        run_dir: Path,
    ) -> TurnEnded | TurnFailed | Stalled | ProtocolError:
        """Consume `turn` through its terminal signal, or stop it when it stalls."""
        pid_path = role_dir(run_dir, role.value) / _TURN_PID_NAME
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        iterator = turn.signals()
        last_signal_at = self._clock.now()
        tool_running = False
        pending_signal = asyncio.create_task(_next_signal(iterator))
        try:
            pid_path.write_text(str(turn.pid))
            await self._decorator.set_pane_state(
                task.panes[role], color=ROLE_COLORS[role], glyph=ROLE_GLYPHS[role]
            )
            while True:
                timeout = (
                    self._settings.orchestration.timeouts.tool
                    if tool_running
                    else self._settings.orchestration.timeouts.stall
                )
                elapsed = (self._clock.now() - last_signal_at).total_seconds()
                remaining = timeout - elapsed
                if remaining <= 0:
                    await turn.stop()
                    await self._decorator.set_pane_state(
                        task.panes[role], color=HOLD_COLOR, glyph=GLYPH_STALLED
                    )
                    return Stalled()
                poll_timeout = min(
                    self._settings.orchestration.poll_seconds,
                    remaining,
                )
                signal_ready = await self._wait_for_signal(pending_signal, poll_timeout)
                pid_path.write_text(str(turn.pid))
                if not signal_ready:
                    continue
                signal = pending_signal.result()
                pending_signal = asyncio.create_task(_next_signal(iterator))
                last_signal_at = self._clock.now()
                self._append_view(run_dir, turn_dir, role, rung, signal)
                tool_running, terminal = await self._record_signal(
                    signal,
                    agent=agent,
                    task=task,
                    role=role,
                    run_id=run_id,
                    run_dir=run_dir,
                    tool_running=tool_running,
                )
                if terminal is not None:
                    return terminal
        except StopAsyncIteration:
            return ProtocolError()
        except asyncio.CancelledError:
            await turn.stop()
            raise
        except Exception:
            await turn.stop()
            raise
        finally:
            if not pending_signal.done():
                pending_signal.cancel()
            await asyncio.gather(pending_signal, return_exceptions=True)
            pid_path.unlink(missing_ok=True)

    async def _record_signal(
        self,
        signal: AgentSignal,
        *,
        agent: Agent,
        task: Task,
        role: Role,
        run_id: int,
        run_dir: Path,
        tool_running: bool,
    ) -> tuple[bool, TurnEnded | TurnFailed | None]:
        if isinstance(signal, SessionStarted):
            self._sessions.record_session_started(
                run_id=run_id, agent=agent, session_id=signal.session_id, run_dir=run_dir
            )
            await self._refresh_titles(task)
        elif isinstance(signal, Message):
            self._runs.set_last_message(run_id, signal.text)
        elif isinstance(signal, UsageReported):
            self._runs.record_usage(run_id, signal.usage)
            await self._refresh_titles(task)
        elif isinstance(signal, ToolStarted):
            tool_running = True
        elif isinstance(signal, ToolFinished):
            tool_running = False
        elif isinstance(signal, TurnEnded):
            self._runs.set_last_message(run_id, signal.last_message)
            return tool_running, signal
        elif isinstance(signal, TurnFailed):
            return tool_running, signal
        else:
            await self._record_auxiliary_signal(signal, agent=agent, task=task, role=role)
        return tool_running, None

    async def _record_auxiliary_signal(
        self, signal: AgentSignal, *, agent: Agent, task: Task, role: Role
    ) -> None:
        if isinstance(signal, QuotaReported):
            for window in signal.windows:
                self._quotas.record(agent.name, window)
        elif isinstance(signal, Compacted):
            self._events.append(
                EventKind.COMPACTED,
                task_id=task.id,
                message=f"{task.id} {role.value} compacted context",
                data={
                    "role": role.value,
                    "pre_tokens": signal.pre_tokens,
                    "post_tokens": signal.post_tokens,
                },
            )

    async def _wait_for_signal(
        self, signal_task: asyncio.Task[AgentSignal], timeout: float
    ) -> bool:
        timer_task = asyncio.create_task(self._clock.sleep(timeout))
        try:
            done, _ = await asyncio.wait(
                {signal_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
            )
            return signal_task in done
        finally:
            if not timer_task.done():
                timer_task.cancel()
            await asyncio.gather(timer_task, return_exceptions=True)

    def _append_view(
        self, run_dir: Path, turn_dir: Path, role: Role, rung: Rung, signal: AgentSignal
    ) -> None:
        path = view_log_path(run_dir, role.value)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            VIEW_TIME_KEY: self._clock.now().isoformat(),
            VIEW_TURN_KEY: turn_dir.name,
            VIEW_RUNG_KEY: asdict(rung),
            VIEW_SIGNAL_KEY: type(signal).__name__,
            **asdict(signal),
        }
        with path.open("a") as handle:
            handle.write(json.dumps(row, default=_json_value) + _LINE_ENDING)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    message = f"cannot encode {type(value).__name__} in a turn view"
    raise TypeError(message)


async def _next_signal(signals: AsyncIterator[AgentSignal]) -> AgentSignal:
    return await signals.__anext__()
