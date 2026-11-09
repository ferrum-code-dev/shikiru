"""One project under soak: its repository, private tmux, fake gh, hook and the real engine."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from fake_agent.scripted import TASK_SCRIPT_DIR_ENV_VAR, task_script_path

from shikiru.clock import SystemClock
from shikiru.engine.daemon import EngineLock
from shikiru.engine.turn_processes import TURN_PID_NAME, read_turn_pid
from shikiru.paths import engine_lock_path, engine_log_path, runs_root, state_db_path, task_root
from shikiru.process import ProcessIdentity, SubprocessRunner
from shikiru.store.commands import CommandQueue
from shikiru.store.database import Database, OperationalDatabaseError
from shikiru.store.reader import StateReader
from tests.soak import hook, processes
from tests.soak.model import IntegrationMode, Scripts
from tests.soak.scenarios import RESETS_AT_PLACEHOLDER, TASK_PLACEHOLDER
from tests.support.cli_project import prepare_project, private_tmux_environment, run_cli
from tests.support.factories import CPU_UNCAPPED_PCT
from tests.support.git_repo import run_git
from tests.support.github import (
    FakeGitHub,
    add_bare_remote,
    install_fake_gh,
    neutral_gh_environment,
    without_gh_credentials,
)
from tests.support.permissions import EXECUTABLE_MODE
from tests.support.waiting import MEDIUM_WAIT_SECONDS

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.commands import Command
    from tests.support.store import Connection

CLOCK = SystemClock()
RUNNER = SubprocessRunner()
STARTS_ATTEMPTS = 5
START_RETRY_SECONDS = 1.0
APPEND_RETRY_SECONDS = 0.5
APPEND_ATTEMPTS = 40
QUOTA_RESET_SECONDS = 3.0
STALL_SECONDS = 20
LADDER = '[{agent="fake",model="fake-1"},{agent="fake",model="fake-2"}]'
HELPER_LADDER = '[{agent="fake",model="fake-1"}]'
GIT_HOOK_NAME = "reference-transaction"
FLAG_NAME = "git-fail-once"
GIT_FAILURES_LOG = "git-failures.log"
USED_SUFFIX = ".used"
_GIT_HOOK = """#!/bin/sh
[ "$1" = prepared ] || exit 0
[ -z "$SHIKIRU_TASK" ] || exit 0
mv "{flag}" "{flag}{used}" 2>/dev/null || exit 0
echo "soak: injected failure of a reference update" >&2
date +%s >> "{log}"
exit 1
"""


@dataclass(frozen=True, slots=True)
class TurnProcess:
    task_id: str
    role: str
    identity: ProcessIdentity


class Arena:
    """A project with its own engine, tmux server and hosting, all under one directory."""

    def __init__(self, directory: Path, name: str, integration: IntegrationMode) -> None:
        self.directory = directory
        self.name = name
        self.integration = integration
        self.root = directory / "project"
        self.remote = directory / "remote.git"
        self.tmpdir = directory / "tmp"
        self.hook_dir = directory / "hook"
        self.scripts_dir = directory / "scripts"
        self.socket = f"shikiru-soak-{uuid.uuid4().hex[:8]}"
        self.github: FakeGitHub | None = None
        self.environment: dict[str, str] = {}
        self.settings: tuple[str, ...] = ()
        self._queue: CommandQueue | None = None
        self._appending = asyncio.Lock()
        self._writer: Connection | None = None
        self._reading: Connection | None = None
        self._reads: CommandQueue | None = None
        self.reader: StateReader | None = None

    @classmethod
    async def create(cls, directory: Path, name: str, integration: IntegrationMode) -> Arena:
        arena = cls(directory, name, integration)
        await arena._prepare()
        return arena

    async def _prepare(self) -> None:
        for path in (self.tmpdir, self.hook_dir, self.scripts_dir):
            path.mkdir(parents=True)
        await prepare_project(self.directory)
        environment = private_tmux_environment(self.directory / "tmux-bin", self.socket)
        environment = without_gh_credentials(environment)
        environment.update(neutral_gh_environment(self.directory))
        environment.update(
            {
                "TMPDIR": str(self.tmpdir),
                "SHELL": "/bin/sh",
                TASK_SCRIPT_DIR_ENV_VAR: str(self.scripts_dir),
            }
        )
        if self.integration is IntegrationMode.PR:
            self.github, gh_bin = install_fake_gh(self.directory / "github")
            environment["PATH"] = f"{gh_bin}{os.pathsep}{environment['PATH']}"
            await add_bare_remote(self.root, self.remote, "origin")
        self.environment = environment
        self._install_git_hook()
        notify = f"{sys.executable} {hook.__file__} {self.hook_dir}"
        self.settings = _settings(self.integration, notify)
        await self.cli("init", "--yes")

    def _install_git_hook(self) -> None:
        path = self.root / ".git" / "hooks" / GIT_HOOK_NAME
        path.write_text(
            _GIT_HOOK.format(
                flag=self.hook_dir / FLAG_NAME,
                used=USED_SUFFIX,
                log=self.hook_dir / GIT_FAILURES_LOG,
            )
        )
        path.chmod(EXECUTABLE_MODE)

    async def cli(self, *arguments: str) -> tuple[int, str, str]:
        return await run_cli(self.root, self.environment, *self.settings, *arguments)

    @property
    def database(self) -> Database:
        return Database(state_db_path(self.root))

    def open_state(self) -> None:
        self._writer = self.database.connect()
        self._queue = CommandQueue(self._writer, CLOCK)
        self._reading = self.database.connect_readonly()
        self._reads = CommandQueue(self._reading, CLOCK)
        self.reader = StateReader(self.database, CLOCK)

    def engine_pid(self) -> int | None:
        return EngineLock.pid_if_running(engine_lock_path(self.root))

    async def start_engine(self) -> None:
        code, stderr = 0, ""
        for _ in range(STARTS_ATTEMPTS):
            code, _, stderr = await self.cli("start")
            if code == 0 and self.engine_pid() is not None:
                return
            await CLOCK.sleep(START_RETRY_SECONDS)
        message = (
            f"{self.name}: the engine did not start after {STARTS_ATTEMPTS} attempts: "
            f"start exited {code}: {stderr.strip()}"
        )
        raise RuntimeError(message)

    async def append(self, command: Command) -> tuple[int, Command]:
        queue = self._queue
        assert queue is not None
        async with self._appending:
            for _ in range(APPEND_ATTEMPTS):
                try:
                    queued = await asyncio.to_thread(queue.append, command)
                except OperationalDatabaseError:
                    await CLOCK.sleep(APPEND_RETRY_SECONDS)
                else:
                    return queued.id, queued.command
        message = f"{self.name}: a command could not be queued, the database stayed locked"
        raise RuntimeError(message)

    def write_script(self, task_id: str, scripts: Scripts) -> None:
        resets_at = (CLOCK.now() + timedelta(seconds=QUOTA_RESET_SECONDS)).isoformat()
        text = (
            json.dumps(scripts)
            .replace(TASK_PLACEHOLDER, task_id)
            .replace(RESETS_AT_PLACEHOLDER, resets_at)
        )
        task_script_path(str(self.scripts_dir), task_id).write_text(text)

    def commands(self) -> CommandQueue:
        assert self._reads is not None
        return self._reads

    def turn_processes(self) -> list[TurnProcess]:
        found: list[TurnProcess] = []
        for path in sorted(runs_root(self.root).glob(f"*/*/{TURN_PID_NAME}")):
            with suppress(FileNotFoundError, json.JSONDecodeError):
                found.append(
                    TurnProcess(path.parents[1].name, path.parent.name, read_turn_pid(path))
                )
        return [turn for turn in found if RUNNER.is_running(turn.identity)]

    def worktree(self, task_id: str) -> Path:
        return task_root(self.root, task_id)

    def notified(self) -> set[int]:
        log = self.hook_dir / hook.LOG_NAME
        if not log.exists():
            return set()
        return {int(line.split()[0]) for line in log.read_text().splitlines() if line}

    def git_failures(self) -> int:
        log = self.hook_dir / GIT_FAILURES_LOG
        return len(log.read_text().splitlines()) if log.exists() else 0

    def engine_log_tail(self, lines: int) -> str:
        path = engine_log_path(self.root)
        return "\n".join(path.read_text().splitlines()[-lines:]) if path.exists() else ""

    async def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return await run_git(RUNNER, cwd or self.root, *arguments)

    async def tmux(self, *arguments: str) -> int:
        result = await RUNNER.run(
            ["tmux", *arguments], timeout=MEDIUM_WAIT_SECONDS, env=self.environment
        )
        return result.returncode

    async def tmux_server_is_gone(self) -> bool:
        return await self.tmux("list-sessions") != 0

    def release_database(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        for connection in (self._writer, self._reading):
            if connection is not None:
                connection.close()
        self._writer = self._reading = None
        self._queue = self._reads = None

    async def kill_everything(self) -> list[int]:
        """End whatever of this arena still runs; return the pids that had to be killed."""
        pid = self.engine_pid()
        if pid is not None:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGCONT)
        await self.tmux("kill-server")
        leftovers = processes.owned_by(self.directory)
        pids = [process.pid for process in leftovers]
        processes.kill_all(leftovers)
        return pids

    def remove(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def _settings(integration: IntegrationMode, notify_command: str) -> tuple[str, ...]:
    pairs = (
        "orchestration.poll_seconds=1",
        f"orchestration.resources.max_cpu_pct={CPU_UNCAPPED_PCT}",
        "orchestration.resources.min_free_ram_gb=0",
        'dashboard.widgets=["header"]',
        f"roles.leader.ladder={LADDER}",
        f"roles.implementer.ladder={LADDER}",
        f"roles.helper.ladder={HELPER_LADDER}",
        "orchestration.max_iterations=3",
        "orchestration.max_parallel=2",
        f"orchestration.timeouts.stall={STALL_SECONDS}",
        f'orchestration.integration="{integration.value}"',
        "orchestration.network.pr_poll_seconds=1",
        "orchestration.faults.backoff_initial_seconds=1",
        "orchestration.faults.backoff_max_seconds=5",
        f'notify.command="{notify_command}"',
    )
    return tuple(part for pair in pairs for part in ("--set", pair))
