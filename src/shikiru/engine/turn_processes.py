"""The `turn.pid` record of each running turn, and ending the ones a dead engine left (03 §7)."""

from __future__ import annotations

import json
import signal
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import role_dir
from shikiru.paths import RUNS_DIR_NAME, project_state_dir
from shikiru.process import TURN_STOP_GRACE, ProcessIdentity

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.tasks import Role
    from shikiru.process import SubprocessRunner

TURN_PID_NAME = "turn.pid"
EXIT_POLL_SECONDS = 0.1
_STAGED_SUFFIX = ".staged"


def turn_pid_path(run_dir: Path, role: Role) -> Path:
    """Return the file recording the process that runs `role`'s current turn."""
    return role_dir(run_dir, role.value) / TURN_PID_NAME


def read_turn_pid(path: Path) -> ProcessIdentity:
    """Read the process identity recorded in a `turn.pid` file."""
    return ProcessIdentity(**json.loads(path.read_text()))


class TurnProcesses:
    """Record the process running each role's turn and stop orphans after an engine restart."""

    def __init__(self, *, runner: SubprocessRunner, clock: Clock, project_root: Path) -> None:
        """Identify and signal processes through `runner`, waiting on `clock`."""
        self._runner = runner
        self._clock = clock
        self._project_root = project_root

    def record(self, run_dir: Path, role: Role, pid: int) -> None:
        """Write `role`'s `turn.pid` with the identity of `pid`, or of its group once it left."""
        path = turn_pid_path(run_dir, role)
        identity = self._runner.identify(pid)
        if identity is None and self._runner.group_is_running(pid):
            identity = ProcessIdentity(pid, None)
        if identity is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_suffix(_STAGED_SUFFIX)
        staged.write_text(json.dumps(asdict(identity)))
        staged.replace(path)

    @staticmethod
    def clear(run_dir: Path, role: Role) -> None:
        """Remove `role`'s `turn.pid` once its turn process has ended."""
        turn_pid_path(run_dir, role).unlink(missing_ok=True)

    async def end_orphans(self) -> None:
        """Stop every recorded turn process that still runs, then drop its `turn.pid`."""
        runs_root = project_state_dir(self._project_root) / RUNS_DIR_NAME
        for path in sorted(runs_root.glob(f"*/*/{TURN_PID_NAME}")):
            await self._stop(read_turn_pid(path))
            path.unlink()

    async def _stop(self, identity: ProcessIdentity) -> None:
        if not self._runner.signal_group(identity, signal.SIGTERM):
            return
        deadline = self._clock.now() + timedelta(seconds=TURN_STOP_GRACE)
        while self._runner.group_is_running(identity.pid) and self._clock.now() < deadline:
            await self._clock.sleep(EXIT_POLL_SECONDS)
        self._runner.signal_group(identity, signal.SIGKILL)
