"""Engine and dashboard process lifecycle service boundaries."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from shikiru.config.loader import load_settings
from shikiru.domain.tasks import Status
from shikiru.errors import EXIT_COMMAND_REJECTED, ShikiruError
from shikiru.operator.attachment import admit_attachment
from shikiru.operator.commands import Ack, AckStatus
from shikiru.paths import engine_output_path
from shikiru.private import make_private_dir
from shikiru.process import TURN_STOP_GRACE
from shikiru.tmux.client import FORMAT_PANE_ID, exact_target
from shikiru.tmux.session import bind_palette_key, session_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.config.settings import Settings
    from shikiru.operator.services import OperatorServices
    from shikiru.process import SubprocessRunner
    from shikiru.tmux.client import Tmux
    from shikiru.tmux.session import SessionView


if TYPE_CHECKING:
    OperatorFactory = Callable[[Path, Settings], OperatorServices]
    PluginValidator = Callable[[Settings], Awaitable[None]]
    EngineState = Callable[[Path], bool]

PACKAGE_NAME = "shikiru"
CLI_ENGINE_COMMAND = "engine"
CLI_DASH_COMMAND = "dash"
CLI_PALETTE_COMMAND = "palette"
CLI_PALETTE_ORIGIN_PANE_OPTION = "--origin-pane"
PALETTE_COMMAND = (
    sys.executable,
    "-m",
    PACKAGE_NAME,
    CLI_PALETTE_COMMAND,
    CLI_PALETTE_ORIGIN_PANE_OPTION,
    FORMAT_PANE_ID,
)
ENGINE_START_WAIT_SECONDS = 5.0
ENGINE_START_POLL_SECONDS = 0.05
ENGINE_OUTPUT_TAIL_BYTES = 2000
ENGINE_EXITED_MESSAGE = "the engine process exited before it acquired its lock"
RESTART_BLOCKING_STATUSES = (Status.RUNNING, Status.INTEGRATING)
STOP_NOT_CONFIRMED_MESSAGE = "the engine did not confirm the stop"


class EngineExitedError(ShikiruError):
    """The engine process started by `start` ended without ever holding its lock."""


class RestartRefusedError(ShikiruError):
    """A restart would interrupt running tasks and was not forced."""

    exit_code = EXIT_COMMAND_REJECTED

    def __init__(self, task_ids: Sequence[str]) -> None:
        """Record the running tasks that block the restart."""
        super().__init__(
            f"restart refused while tasks are running: {', '.join(task_ids)}; "
            "wait for them to finish or run restart with --force"
        )
        self.task_ids = tuple(task_ids)


class ProjectLifecycle:
    """Run a project's engine and session through injected process boundaries."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner,
        tmux: Tmux,
        session: SessionView,
        clock: Clock,
        operator_factory: OperatorFactory,
        plugin_validator: PluginValidator,
        engine_up: EngineState,
    ) -> None:
        """Store process, tmux, state-service and validation boundaries."""
        self._runner = runner
        self._tmux = tmux
        self._session = session
        self._clock = clock
        self._operator_factory = operator_factory
        self._plugin_validator = plugin_validator
        self._engine_up = engine_up

    async def start(
        self,
        root: Path,
        overrides: Sequence[str],
        *,
        attach: bool,
        inside_tmux: bool = False,
        current_pane: str | None = None,
    ) -> StartResult:
        """Validate a project and start or reuse its engine and tmux session."""
        settings = load_settings(root, overrides)
        name = session_name(settings.project.name)
        target = exact_target(name)
        same_client = await admit_attachment(self._tmux, name, current_pane) if attach else False
        await self._plugin_validator(settings)
        await self._session.ensure(
            settings.project.name,
            root,
            home_command=command_with_overrides(home_command(root), overrides),
        )
        await bind_palette_key(self._tmux, PALETTE_COMMAND)
        started = False
        if not self._engine_up(root):
            output = engine_output_path(root)
            make_private_dir(output.parent)
            pid = self._runner.spawn_detached(
                command_with_overrides(
                    (
                        sys.executable,
                        "-m",
                        PACKAGE_NAME,
                        "--project",
                        str(root),
                        CLI_ENGINE_COMMAND,
                    ),
                    overrides,
                ),
                output=output,
            )
            started = True
            await self._wait_for_engine(
                root,
                up=True,
                seconds=ENGINE_START_WAIT_SECONDS,
                failure="engine did not acquire its lock after start",
                spawned=pid,
                output=output,
            )
        if attach and not same_client:
            self._runner.replace(self._tmux.guarded_client_argv(name, switch=inside_tmux))
        return StartResult(started=started, engine="up", session=target)

    async def stop(
        self,
        root: Path,
        overrides: Sequence[str],
        *,
        kill_session: bool,
    ) -> Ack:
        """Stop a running engine and optionally remove its tmux session."""
        settings = load_settings(root, overrides)
        target = exact_target(session_name(settings.project.name))
        if not self._engine_up(root):
            if kill_session and await self._tmux.target_exists(target):
                await self._tmux.kill_session(target)
            return Ack(
                id=0,
                status=AckStatus.DONE,
                result={"stopped": False},
                message="engine already stopped",
            )
        services = self._operator_factory(root, settings)
        try:
            ack = await services.commands.stop_engine()
        finally:
            services.close()
        if ack.status is AckStatus.DONE:
            await self._wait_for_engine(
                root,
                up=False,
                seconds=settings.orchestration.shutdown_seconds + TURN_STOP_GRACE,
                failure="engine did not release its lock after stop",
            )
        if kill_session and ack.status is AckStatus.DONE and await self._tmux.target_exists(target):
            await self._tmux.kill_session(target)
        return ack

    async def restart(
        self,
        root: Path,
        overrides: Sequence[str],
        *,
        force: bool,
    ) -> RestartResult:
        """Stop a running engine, refusing while tasks run unless forced, then start it again."""
        settings = load_settings(root, overrides)
        restarted = self._engine_up(root)
        if restarted:
            if not force:
                running = self._running_task_ids(root, settings)
                if running:
                    raise RestartRefusedError(running)
            ack = await self.stop(root, overrides, kill_session=False)
            if ack.status is not AckStatus.DONE:
                raise ShikiruError(ack.message or STOP_NOT_CONFIRMED_MESSAGE)
        started = await self.start(root, overrides, attach=False)
        return RestartResult(restarted=restarted, engine=started.engine, session=started.session)

    def _running_task_ids(self, root: Path, settings: Settings) -> list[str]:
        services = self._operator_factory(root, settings)
        try:
            tasks = services.queries.list_tasks(statuses=RESTART_BLOCKING_STATUSES)
        finally:
            services.close()
        return [task.id for task in tasks]

    async def _wait_for_engine(
        self,
        root: Path,
        *,
        up: bool,
        seconds: float,
        failure: str,
        spawned: int | None = None,
        output: Path | None = None,
    ) -> None:
        deadline = self._clock.now().timestamp() + seconds
        while self._engine_up(root) != up:
            if spawned is not None and self._runner.identify(spawned) is None:
                raise EngineExitedError(_with_output(ENGINE_EXITED_MESSAGE, output))
            if self._clock.now().timestamp() >= deadline:
                raise ShikiruError(_with_output(failure, output))
            await self._clock.sleep(ENGINE_START_POLL_SECONDS)


def _with_output(message: str, output: Path | None) -> str:
    if output is None:
        return message
    with output.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - ENGINE_OUTPUT_TAIL_BYTES))
        tail = handle.read().decode(errors="replace").strip()
    detail = f"engine output ({output}):\n{tail}" if tail else f"engine printed nothing ({output})"
    return f"{message}\n{detail}"


def home_command(root: Path) -> tuple[str, ...]:
    """Return the command of the session's home window: the dashboard of the project at `root`."""
    return (sys.executable, "-m", PACKAGE_NAME, "--project", str(root), CLI_DASH_COMMAND, "--home")


def task_dashboard_command(root: Path, task_id: str) -> tuple[str, ...]:
    """Return the explicit task dashboard command for `task_id` in `root`."""
    return (
        sys.executable,
        "-m",
        PACKAGE_NAME,
        "--project",
        str(root),
        CLI_DASH_COMMAND,
        "--task",
        task_id,
    )


def command_with_overrides(command: tuple[str, ...], overrides: Sequence[str]) -> tuple[str, ...]:
    """Insert global `--set` values before the hidden engine or dashboard command."""
    command_index = next(
        index
        for index, part in enumerate(command)
        if part in {CLI_ENGINE_COMMAND, CLI_DASH_COMMAND}
    )
    override_parts = tuple(part for override in overrides for part in ("--set", override))
    return (*command[:command_index], *override_parts, *command[command_index:])


class StartResult(BaseModel):
    """The started state and tmux target returned by a project start request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    started: bool
    engine: Literal["up"]
    session: str


class RestartResult(BaseModel):
    """Whether a running engine was stopped, and the engine and session a restart left up."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    restarted: bool
    engine: Literal["up"]
    session: str
