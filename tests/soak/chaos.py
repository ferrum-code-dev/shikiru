"""Injects the scheduled faults into one arena and records what each did."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.clock import SystemClock
from shikiru.domain.tasks import Hold, Status
from shikiru.process import SubprocessRunner
from shikiru.store.database import transaction
from tests.soak import hook
from tests.soak.arena import FLAG_NAME
from tests.soak.model import FaultKind, ScheduledFault, Tally

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.soak.arena import Arena
    from tests.soak.reporter import Reporter
    from tests.soak.supervisor import EngineSupervisor

CLOCK = SystemClock()
RUNNER = SubprocessRunner()
TURN_WAIT_SECONDS = 15.0
PHASE_WAIT_SECONDS = 25.0
PHASE_POLL_SECONDS = 0.01
TURN_POLL_SECONDS = 0.2
GH_SUBCOMMANDS = ("create", "view", "list", "close")
GH_FAILURES = (1, 2, 3)
MILLIS = 1000
AWAY_SUFFIX = ".away"
DEFAULT_GRACE_SECONDS = 6.0
GRACE_SECONDS = {
    FaultKind.TMUX_LOSS: 5.0,
    FaultKind.TURN_KILL: 3.0,
    FaultKind.DB_LOCK: 3.0,
    FaultKind.GH_FAIL: 2.0,
    FaultKind.GIT_HOOK_FAIL: 2.0,
    FaultKind.HOOK_STUCK: 3.0,
    FaultKind.ENGINE_FREEZE: 4.0,
}


class ChaosInjector:
    def __init__(
        self, arena: Arena, supervisor: EngineSupervisor, reporter: Reporter, tally: Tally
    ) -> None:
        self._arena = arena
        self._supervisor = supervisor
        self._reporter = reporter
        self._tally = tally

    async def inject(self, fault: ScheduledFault) -> None:
        grace = GRACE_SECONDS.get(fault.kind, DEFAULT_GRACE_SECONDS) + fault.duration_seconds
        self._supervisor.disturb(grace)
        outcome = await self._perform(fault)
        if outcome is not None:
            self._tally.fault(fault.kind)
        self._reporter.injected(self._arena.name, fault, outcome or "skipped")

    async def _perform(self, fault: ScheduledFault) -> str | None:
        seconds = fault.duration_seconds
        actions: dict[FaultKind, Callable[[], Awaitable[str | None]]] = {
            FaultKind.ENGINE_KILL: lambda: self._signal_engine(signal.SIGKILL),
            FaultKind.ENGINE_KILL_LOCKED: lambda: self._kill_and_lock(seconds),
            FaultKind.ENGINE_KILL_INTEGRATING: self._kill_when_integrating,
            FaultKind.ENGINE_KILL_ESCALATING: self._kill_when_escalating,
            FaultKind.ENGINE_TERM: lambda: self._signal_engine(signal.SIGTERM),
            FaultKind.ENGINE_INT: lambda: self._signal_engine(signal.SIGINT),
            FaultKind.ENGINE_STOP_START: self._stop_and_start,
            FaultKind.ENGINE_FREEZE: lambda: self._freeze(seconds),
            FaultKind.TMUX_LOSS: self._lose_tmux,
            FaultKind.TMUX_LOSS_TAKEOVER: self._lose_tmux_in_takeover,
            FaultKind.REMOTE_DOWN: lambda: self._take_remote_down(seconds),
            FaultKind.TURN_KILL: self._kill_turn,
            FaultKind.DB_LOCK: lambda: self._lock_database(seconds),
            FaultKind.GH_FAIL: lambda: self._fail_gh(fault),
            FaultKind.GIT_HOOK_FAIL: self._fail_git,
            FaultKind.HOOK_STUCK: lambda: self._flag_hook(hook.STUCK_NAME, seconds),
            FaultKind.HOOK_FAIL: lambda: self._flag_hook(hook.FAILING_NAME, seconds),
        }
        return await actions[fault.kind]()

    async def _kill_when_integrating(self) -> str | None:
        reader = self._arena.reader
        assert reader is not None
        return await self._kill_when(
            lambda: any(task.status is Status.INTEGRATING for task in reader.list_tasks())
        )

    async def _kill_when_escalating(self) -> str | None:
        reader = self._arena.reader
        assert reader is not None
        known = {escalation.id for escalation in reader.list_open_escalations()}
        return await self._kill_when(
            lambda: any(e.id not in known for e in reader.list_open_escalations())
        )

    async def _kill_when(self, reached: Callable[[], bool]) -> str | None:
        deadline = CLOCK.now() + timedelta(seconds=PHASE_WAIT_SECONDS)
        while CLOCK.now() < deadline:
            if reached():
                return await self._signal_engine(signal.SIGKILL)
            await CLOCK.sleep(PHASE_POLL_SECONDS)
        return None

    async def _lose_tmux_in_takeover(self) -> str | None:
        reader = self._arena.reader
        assert reader is not None
        deadline = CLOCK.now() + timedelta(seconds=PHASE_WAIT_SECONDS)
        while CLOCK.now() < deadline:
            if any(Hold.INTERVENTION in task.holds for task in reader.list_tasks()):
                return await self._lose_tmux()
            await CLOCK.sleep(PHASE_POLL_SECONDS)
        return None

    async def _take_remote_down(self, seconds: float) -> str | None:
        away = self._arena.remote.with_name(f"{self._arena.remote.name}{AWAY_SUFFIX}")
        if not self._arena.remote.exists():
            return None
        self._arena.remote.rename(away)
        try:
            await CLOCK.sleep(seconds)
        finally:
            away.rename(self._arena.remote)
        return f"the remote unreachable for {seconds:.1f}s"

    async def _lose_tmux(self) -> str:
        return f"kill-server returned {await self._arena.tmux('kill-server')}"

    async def _fail_git(self) -> str:
        (self._arena.hook_dir / FLAG_NAME).touch()
        return "the next reference update of the engine fails"

    async def _signal_engine(self, signum: signal.Signals) -> str | None:
        pid = self._arena.engine_pid()
        if pid is None:
            return None
        self._supervisor.expect_exit()
        os.kill(pid, signum)
        return f"{signum.name} to pid {pid}"

    async def _stop_and_start(self) -> str | None:
        if self._arena.engine_pid() is None:
            return None
        self._supervisor.expect_exit()
        code, _, stderr = await self._arena.cli("stop")
        return f"stop returned {code} {stderr.strip()}"

    async def _freeze(self, seconds: float) -> str | None:
        pid = self._arena.engine_pid()
        if pid is None:
            return None
        os.kill(pid, signal.SIGSTOP)
        try:
            await CLOCK.sleep(seconds)
        finally:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGCONT)
        return f"pid {pid} stopped for {seconds:.1f}s"

    async def _kill_turn(self) -> str | None:
        deadline = CLOCK.now().timestamp() + TURN_WAIT_SECONDS
        while CLOCK.now().timestamp() < deadline:
            turns = self._arena.turn_processes()
            if turns:
                turn = turns[0]
                RUNNER.signal_group(turn.identity, signal.SIGKILL)
                return f"{turn.task_id} {turn.role} turn group {turn.identity.pid}"
            await CLOCK.sleep(TURN_POLL_SECONDS)
        return None

    async def _kill_and_lock(self, seconds: float) -> str | None:
        killed = await self._signal_engine(signal.SIGKILL)
        if killed is None:
            return None
        return f"{killed}, then {await self._lock_database(seconds)}"

    async def _lock_database(self, seconds: float) -> str:
        def hold() -> None:
            connection = self._arena.database.connect()
            try:
                with transaction(connection, immediate=True):
                    threading.Event().wait(seconds)
            finally:
                connection.close()

        await asyncio.to_thread(hold)
        return f"write lock held {seconds:.1f}s"

    async def _fail_gh(self, fault: ScheduledFault) -> str | None:
        github = self._arena.github
        if github is None:
            return None
        key = int(fault.offset_seconds * MILLIS)
        subcommand = GH_SUBCOMMANDS[key % len(GH_SUBCOMMANDS)]
        calls = GH_FAILURES[key % len(GH_FAILURES)]
        github.fail_next(subcommand, calls)
        return f"gh pr {subcommand} fails {calls} time(s)"

    async def _flag_hook(self, name: str, seconds: float) -> str:
        flag = self._arena.hook_dir / name
        flag.touch()
        try:
            await CLOCK.sleep(seconds)
        finally:
            flag.unlink(missing_ok=True)
        return f"notification hook {name} for {seconds:.1f}s"
