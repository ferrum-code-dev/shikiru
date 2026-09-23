"""Live agent contract probes used by the operator diagnostics."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    AgentSignal,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
    shikiru_command,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.config.loader import SHIKIRU_DIR_NAME
from shikiru.domain.reports import (
    CURRENT_TURN_LINK_NAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    REPORT_SCHEMA_FILENAME,
    ReportKind,
    schema_for,
)
from shikiru.errors import ShikiruError
from shikiru.tmux.client import Tmux, exact_target
from shikiru.tmux.pane import TmuxPane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from shikiru.agents.contracts import InteractiveAgent
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner


PROBE_COMMAND_TIMEOUT_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 90.0
PROBE_SCREEN_TAIL_LINES = 6
PROBE_TMUX_COMMAND = "tmux"
PROBE_TMUX_SESSION_PREFIX = "shikiru-doctor-"
PROBE_TMUX_SESSION_NAME = "probe"
PROBE_ROLE = "implementer"
PROBE_TURN_NAME = "01-doctor"
PROBE_REPORT_FILE = "probe-report.json"
PROBE_PROMPT = (
    "This is a Shikiru live contract probe. Read the current turn report.schema.json, "
    f"write {PROBE_REPORT_FILE} with status completed, summary probe, commits [], tests [], "
    f"and trackers_updated false, then run {shikiru_command('report', '--file')} "
    f"{PROBE_REPORT_FILE}. Answer exactly SHIKIRU_PROBE_OK after the report command succeeds. "
    "Do not change files outside the current probe repository."
)
PROBE_MARKER = "SHIKIRU_PROBE_OK"
PROBE_REQUIRED_SIGNALS = (SessionStarted, PromptSubmitted)
PROBE_REMAIN_ON_EXIT_OPTION = "remain-on-exit"
PROBE_REMAIN_ON_EXIT_VALUE = "on"
PROBE_DIRECTORY = Path(SHIKIRU_DIR_NAME) / "probe"
PROBE_REPO_DIRECTORY = "repo"
PROBE_RUN_DIRECTORY = "run"
PROBE_GIT_DIRECTORY = ".git"
PROBE_REPO_README_FILE = "README.md"
PROBE_REPO_README = "Shikiru doctor probe\n"
PROBE_REPO_BRANCH = "doctor-probe"
PROBE_GIT_INIT_ARGS = ("git", "init", "-q", "-b", PROBE_REPO_BRANCH)
PROBE_REPORT_DIRECTORY = "turns"
PROBE_REPORT_MISSING_MESSAGE = "probe turn ended without a report"
PROBE_MARKER_MISSING_MESSAGE = "probe turn did not return its marker"
PROBE_PANE_MISSING_MESSAGE = "probe tmux session did not return a pane"
PROBE_TURN_TIMEOUT_MESSAGE = "timed out waiting for probe turn completion"
PROBE_STARTUP_EXIT_MESSAGE = "agent pane exited during probe startup"
_TMUX_KILL_ARGS = (PROBE_TMUX_COMMAND, "-L")


class DoctorProbeError(ShikiruError):
    """A live agent probe did not satisfy its contract."""


class AgentProbe:
    """Exercise the launch and first-turn portions of an agent contract in the probe directory."""

    def __init__(
        self,
        project_root: Path,
        agent: InteractiveAgent,
        runner: SubprocessRunner,
        clock: Clock,
        rung: Rung,
    ) -> None:
        """Store probe inputs and injected process boundaries."""
        self._directory = project_root / PROBE_DIRECTORY
        self._agent = agent
        self._runner = runner
        self._clock = clock
        self._rung = rung

    async def run(self) -> None:
        """Launch a private session in the reused probe repository and require a report turn."""
        repo = self._directory / PROBE_REPO_DIRECTORY
        run_dir = self._directory / PROBE_RUN_DIRECTORY
        await self._prepare_repository(repo)
        await asyncio.to_thread(self._prepare_turn, run_dir)
        socket_name = f"{PROBE_TMUX_SESSION_PREFIX}{uuid.uuid4().hex}"
        tmux = Tmux(self._runner, socket=socket_name)
        session_name = f"{PROBE_TMUX_SESSION_NAME}-{uuid.uuid4().hex[:8]}"
        try:
            await self._execute_probe(tmux, socket_name, session_name, repo, run_dir)
        except BaseException as error:
            try:
                await kill_tmux_server(self._runner, socket_name)
            except (OSError, ShikiruError) as cleanup_error:
                error.add_note(f"probe cleanup failed: {cleanup_error}")
            raise
        else:
            await kill_tmux_server(self._runner, socket_name)

    async def _execute_probe(
        self,
        tmux: Tmux,
        socket_name: str,
        session_name: str,
        repo: Path,
        run_dir: Path,
    ) -> None:
        pane_id = await self._new_probe_session(socket_name, session_name, repo, run_dir)
        if not pane_id:
            raise DoctorProbeError(PROBE_PANE_MISSING_MESSAGE)
        await tmux.set_window_option(
            f"{exact_target(session_name)}:{PROBE_TMUX_SESSION_NAME}",
            PROBE_REMAIN_ON_EXIT_OPTION,
            PROBE_REMAIN_ON_EXIT_VALUE,
        )
        pane = TmuxPane(tmux, pane_id, self._clock)
        spec = LaunchSpec(
            rung=self._rung,
            role=PROBE_ROLE,
            cwd=repo,
            run_dir=run_dir,
            add_dirs=(),
            env=_probe_environment(run_dir),
            resume_session_id=None,
        )
        session = await self._agent.launch(spec, pane)
        if not await pane.is_alive():
            raise DoctorProbeError(PROBE_STARTUP_EXIT_MESSAGE)
        await pane.paste_line(PROBE_PROMPT)
        try:
            signal, seen = await self._await_turn(session.signals(), pane)
        except DoctorProbeError as error:
            hooks = role_dir(run_dir, PROBE_ROLE) / HOOKS_FILENAME
            report = role_dir(run_dir, PROBE_ROLE) / CURRENT_TURN_LINK_NAME / REPORT_FILENAME
            detail = (
                f"{error}; hook_bytes={hooks.stat().st_size if hooks.is_file() else 0}; "
                f"report_exists={report.is_file()}"
            )
            raise DoctorProbeError(detail) from error
        if isinstance(signal, TurnFailed):
            message = f"probe turn failed: {signal.message}"
            raise DoctorProbeError(message)
        missing = [kind.__name__ for kind in PROBE_REQUIRED_SIGNALS if kind not in seen]
        if missing:
            message = f"probe turn ended without {', '.join(missing)}"
            raise DoctorProbeError(message)
        report = role_dir(run_dir, PROBE_ROLE) / CURRENT_TURN_LINK_NAME / REPORT_FILENAME
        if not report.is_file():
            raise DoctorProbeError(PROBE_REPORT_MISSING_MESSAGE)
        if PROBE_MARKER not in signal.last_message:
            raise DoctorProbeError(PROBE_MARKER_MISSING_MESSAGE)

    async def _new_probe_session(
        self, socket_name: str, session_name: str, repo: Path, run_dir: Path
    ) -> str:
        environment = _probe_environment(run_dir)
        result = await self._runner.run(
            [
                PROBE_TMUX_COMMAND,
                "-L",
                socket_name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                session_name,
                "-c",
                str(repo),
                "-n",
                PROBE_TMUX_SESSION_NAME,
            ],
            timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
            env=environment,
            check=True,
        )
        return result.stdout.strip()

    async def _prepare_repository(self, repo: Path) -> None:
        await asyncio.to_thread((repo / PROBE_REPORT_FILE).unlink, missing_ok=True)
        if await asyncio.to_thread((repo / PROBE_GIT_DIRECTORY).exists):
            return
        await asyncio.to_thread(repo.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((repo / PROBE_REPO_README_FILE).write_text, PROBE_REPO_README)
        await self._runner.run(
            PROBE_GIT_INIT_ARGS,
            timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
            cwd=repo,
            check=True,
        )

    def _prepare_turn(self, run_dir: Path) -> None:
        turn_dir = run_dir / PROBE_REPORT_DIRECTORY / PROBE_TURN_NAME
        if turn_dir.exists():
            shutil.rmtree(turn_dir)
        turn_dir.mkdir(parents=True)
        (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
        (turn_dir / REPORT_SCHEMA_FILENAME).write_text(
            json.dumps(schema_for(ReportKind.IMPLEMENTATION))
        )
        role_path = role_dir(run_dir, PROBE_ROLE)
        role_path.mkdir(parents=True, exist_ok=True)
        link = role_path / CURRENT_TURN_LINK_NAME
        link.unlink(missing_ok=True)
        link.symlink_to(turn_dir)

    async def _await_turn(
        self, signals: AsyncIterator[AgentSignal], pane: TmuxPane
    ) -> tuple[TurnEnded | TurnFailed, set[type[AgentSignal]]]:
        seen: set[type[AgentSignal]] = set()
        while True:
            try:
                signal = await asyncio.wait_for(signals.__anext__(), timeout=PROBE_TIMEOUT_SECONDS)
            except TimeoutError as error:
                visible = await pane.capture_visible()
                alive = await pane.is_alive()
                message = f"{PROBE_TURN_TIMEOUT_MESSAGE}; alive={alive}; screen: {_tail(visible)}"
                raise DoctorProbeError(message) from error
            if isinstance(signal, (TurnEnded, TurnFailed)):
                return signal, seen
            seen.add(type(signal))


def _tail(visible: str) -> str:
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    return " | ".join(lines[-PROBE_SCREEN_TAIL_LINES:])


async def kill_tmux_server(runner: SubprocessRunner, socket_name: str) -> None:
    """Stop a private tmux server used by a diagnostic probe."""
    result = await runner.run(
        [*_TMUX_KILL_ARGS, socket_name, "kill-server"],
        timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        message = f"could not stop private tmux server: {detail}"
        raise DoctorProbeError(message)


def _probe_environment(run_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            RUN_DIR_ENV_VAR: str(run_dir),
            ROLE_ENV_VAR: PROBE_ROLE,
        }
    )
    return environment
