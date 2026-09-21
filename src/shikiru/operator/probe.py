"""Live agent contract probes used by the operator diagnostics."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    AgentSignal,
    LaunchSpec,
    ProbeMode,
    PromptSubmitted,
    Rung,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    role_dir,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
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

    from shikiru.agents.contracts import ProbeAgent
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner


PROBE_COMMAND_TIMEOUT_SECONDS = 60.0
PROBE_TIMEOUT_SECONDS = 90.0
PROBE_TMUX_COMMAND = "tmux"
PROBE_TMUX_SESSION_PREFIX = "shikiru-doctor-"
PROBE_TMUX_SESSION_NAME = "probe"
PROBE_PATH_ENV_VAR = "PATH"
PROBE_ROLE = "implementer"
PROBE_TURN_NAME = "01-doctor"
PROBE_PROMPT = (
    "This is a Shikiru live contract probe. Read the current turn report.schema.json, "
    "write a valid completed implementation report with shikiru report, then answer "
    "exactly SHIKIRU_PROBE_OK. Do not change files outside the current temporary project."
)
PROBE_MARKER = "SHIKIRU_PROBE_OK"
PROBE_REMAIN_ON_EXIT_OPTION = "remain-on-exit"
PROBE_REMAIN_ON_EXIT_VALUE = "on"
PROBE_REPO_README = "Shikiru doctor probe\n"
PROBE_REPO_BRANCH = "doctor-probe"
PROBE_GIT_INIT_ARGS = ("git", "init", "-q", "-b", PROBE_REPO_BRANCH)
PROBE_REPORT_DIRECTORY = "turns"
PROBE_REPORT_MISSING_MESSAGE = "probe turn ended without a report"
PROBE_MARKER_MISSING_MESSAGE = "probe turn did not return its marker"
PROBE_PANE_MISSING_MESSAGE = "probe tmux session did not return a pane"
PROBE_TURN_TIMEOUT_MESSAGE = "timed out waiting for probe turn completion"
PROBE_STARTUP_EXIT_MESSAGE = "agent pane exited during probe startup"
PROBE_PERMISSION_MARKERS = ("Allow", "permission", "trust")
PROBE_ERROR_MARKERS = ("error", "failed", "not found")
_TMUX_KILL_ARGS = (PROBE_TMUX_COMMAND, "-L")


class DoctorProbeError(ShikiruError):
    """A live agent probe did not satisfy its contract."""


class AgentProbe:
    """Exercise the launch and first-turn portions of an agent contract in scratch state."""

    def __init__(
        self,
        agent: ProbeAgent,
        runner: SubprocessRunner,
        clock: Clock,
        rung: Rung,
    ) -> None:
        """Store probe inputs and injected process boundaries."""
        self._agent = agent
        self._runner = runner
        self._clock = clock
        self._rung = rung

    async def run(self) -> None:
        """Launch a temporary session, submit a report turn and require its completion."""
        with tempfile.TemporaryDirectory(prefix="shikiru-doctor-probe-") as directory:
            root = Path(directory)
            repo = root / "repo"
            run_dir = root / "run"
            await self._prepare_repository(repo)
            self._prepare_turn(run_dir)
            socket_name = f"{PROBE_TMUX_SESSION_PREFIX}{uuid.uuid4().hex}"
            tmux = Tmux(self._runner, socket=socket_name)
            session_name = f"{PROBE_TMUX_SESSION_NAME}-{uuid.uuid4().hex[:8]}"
            try:
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
                    probe=True,
                )
                session = await self._agent.launch(spec, pane)
                probe_mode = await self._agent.prepare_probe(spec, pane)
                if not await pane.is_alive():
                    raise DoctorProbeError(PROBE_STARTUP_EXIT_MESSAGE)
                signals = session.signals()
                pointer_before_session = probe_mode is ProbeMode.POINTER_BEFORE_SESSION
                if pointer_before_session:
                    await pane.paste_line(PROBE_PROMPT)
                await self._await_signal(signals, SessionStarted, pane, run_dir)
                if not pointer_before_session:
                    await pane.paste_line(PROBE_PROMPT)
                await self._await_signal(signals, PromptSubmitted, pane, run_dir)
                try:
                    signal = await self._await_turn(signals, pane)
                except DoctorProbeError as error:
                    hooks = role_dir(run_dir, PROBE_ROLE) / HOOKS_FILENAME
                    report = (
                        role_dir(run_dir, PROBE_ROLE) / CURRENT_TURN_LINK_NAME / REPORT_FILENAME
                    )
                    detail = (
                        f"{error}; hook_bytes={hooks.stat().st_size if hooks.is_file() else 0}; "
                        f"report_exists={report.is_file()}"
                    )
                    raise DoctorProbeError(detail) from error
                if isinstance(signal, TurnFailed):
                    message = f"probe turn failed: {signal.message}"
                    raise DoctorProbeError(message)
                report = role_dir(run_dir, PROBE_ROLE) / CURRENT_TURN_LINK_NAME / REPORT_FILENAME
                if not report.is_file():
                    raise DoctorProbeError(PROBE_REPORT_MISSING_MESSAGE)
                if PROBE_MARKER not in signal.last_message:
                    raise DoctorProbeError(PROBE_MARKER_MISSING_MESSAGE)
            finally:
                await kill_tmux_server(self._runner, socket_name)

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
        for target_args in (("-g",), ("-t", exact_target(session_name))):
            await self._runner.run(
                [
                    PROBE_TMUX_COMMAND,
                    "-L",
                    socket_name,
                    "set-environment",
                    *target_args,
                    PROBE_PATH_ENV_VAR,
                    environment[PROBE_PATH_ENV_VAR],
                ],
                timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
                check=True,
            )
        return result.stdout.strip()

    async def _prepare_repository(self, repo: Path) -> None:
        await asyncio.to_thread(repo.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((repo / "README.md").write_text, PROBE_REPO_README)
        await self._runner.run(
            PROBE_GIT_INIT_ARGS,
            timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
            cwd=repo,
            check=True,
        )

    def _prepare_turn(self, run_dir: Path) -> None:
        turn_dir = run_dir / PROBE_REPORT_DIRECTORY / PROBE_TURN_NAME
        turn_dir.mkdir(parents=True, exist_ok=True)
        (turn_dir / REPORT_KIND_FILENAME).write_text(ReportKind.IMPLEMENTATION.value)
        (turn_dir / REPORT_SCHEMA_FILENAME).write_text(
            json.dumps(schema_for(ReportKind.IMPLEMENTATION))
        )
        role_path = role_dir(run_dir, PROBE_ROLE)
        role_path.mkdir(parents=True, exist_ok=True)
        link = role_path / CURRENT_TURN_LINK_NAME
        link.unlink(missing_ok=True)
        link.symlink_to(turn_dir)

    async def _await_signal(
        self,
        signals: AsyncIterator[AgentSignal],
        signal_type: type[AgentSignal],
        pane: TmuxPane,
        run_dir: Path,
    ) -> AgentSignal:
        while True:
            try:
                signal = await asyncio.wait_for(signals.__anext__(), timeout=PROBE_TIMEOUT_SECONDS)
            except TimeoutError as error:
                visible = await pane.capture_visible()
                alive = await pane.is_alive()
                prompt_visible = "Yes, I trust this folder" in visible
                hooks = role_dir(run_dir, PROBE_ROLE) / HOOKS_FILENAME
                message = (
                    f"timed out waiting for {signal_type.__name__}; alive={alive}; "
                    f"visible_chars={len(visible)}; trust_visible={prompt_visible}; "
                    f"hook_bytes={hooks.stat().st_size if hooks.is_file() else 0}"
                )
                raise DoctorProbeError(message) from error
            if isinstance(signal, signal_type):
                return signal

    async def _await_turn(
        self, signals: AsyncIterator[AgentSignal], pane: TmuxPane
    ) -> TurnEnded | TurnFailed:
        while True:
            try:
                signal = await asyncio.wait_for(signals.__anext__(), timeout=PROBE_TIMEOUT_SECONDS)
            except TimeoutError as error:
                visible = await pane.capture_visible()
                alive = await pane.is_alive()
                permission_visible = any(marker in visible for marker in PROBE_PERMISSION_MARKERS)
                error_visible = any(marker in visible.lower() for marker in PROBE_ERROR_MARKERS)
                message = (
                    f"{PROBE_TURN_TIMEOUT_MESSAGE}; alive={alive}; "
                    f"visible_chars={len(visible)}; permission_visible={permission_visible}; "
                    f"error_visible={error_visible}"
                )
                raise DoctorProbeError(message) from error
            if isinstance(signal, (TurnEnded, TurnFailed)):
                return signal


async def kill_tmux_server(runner: SubprocessRunner, socket_name: str) -> None:
    """Stop a private tmux server used by a diagnostic probe."""
    try:
        await runner.run(
            [*_TMUX_KILL_ARGS, socket_name, "kill-server"],
            timeout=PROBE_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, ShikiruError:
        return


def _probe_environment(run_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    executable_directory = Path(sys.executable).parent
    current_path = environment.get(PROBE_PATH_ENV_VAR, "")
    path_parts = [str(executable_directory)]
    if current_path:
        path_parts.append(current_path)
    path = os.pathsep.join(path_parts)
    environment.update(
        {
            RUN_DIR_ENV_VAR: str(run_dir),
            ROLE_ENV_VAR: PROBE_ROLE,
            PROBE_PATH_ENV_VAR: path,
        }
    )
    return environment
