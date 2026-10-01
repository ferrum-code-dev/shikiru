"""Engine and dashboard process lifecycle service boundaries."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from shikiru.config.loader import load_settings
from shikiru.errors import ShikiruError
from shikiru.operator.commands import Ack, AckStatus
from shikiru.process import TURN_STOP_GRACE
from shikiru.tmux.client import exact_target
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
PALETTE_COMMAND = (sys.executable, "-m", PACKAGE_NAME, CLI_PALETTE_COMMAND)
ENGINE_START_WAIT_SECONDS = 5.0
ENGINE_START_POLL_SECONDS = 0.05


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
    ) -> StartResult:
        """Validate a project and start or reuse its engine and tmux session."""
        settings = load_settings(root, overrides)
        await self._plugin_validator(settings)
        target = exact_target(session_name(settings.project.name))
        await self._session.ensure(
            settings.project.name,
            root,
            home_command=command_with_overrides(home_command(root), overrides),
        )
        await bind_palette_key(self._tmux, PALETTE_COMMAND)
        started = False
        if not self._engine_up(root):
            self._runner.spawn_detached(
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
                )
            )
            started = True
            await self._wait_for_engine(
                root,
                up=True,
                seconds=ENGINE_START_WAIT_SECONDS,
                failure="engine did not acquire its lock after start",
            )
        if attach:
            self._runner.replace(("tmux", "attach-session", "-t", target))
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

    async def _wait_for_engine(self, root: Path, *, up: bool, seconds: float, failure: str) -> None:
        deadline = self._clock.now().timestamp() + seconds
        while self._engine_up(root) != up:
            if self._clock.now().timestamp() >= deadline:
                raise ShikiruError(failure)
            await self._clock.sleep(ENGINE_START_POLL_SECONDS)


def home_command(root: Path) -> tuple[str, ...]:
    """Return the command of the session's home window: the dashboard of the project at `root`."""
    return (sys.executable, "-m", PACKAGE_NAME, "--project", str(root), CLI_DASH_COMMAND, "--home")


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
