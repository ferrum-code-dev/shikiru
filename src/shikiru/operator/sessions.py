"""Read Shikiru tmux sessions and prepare exact attach targets (06 §5.6)."""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from shikiru.domain.tasks import Status
from shikiru.errors import ConfigError, DatabaseError, UsageError
from shikiru.paths import state_db_path
from shikiru.store.reader import StateReader
from shikiru.tmux.client import exact_target
from shikiru.tmux.session import PROJECT_DIR_ENV_VAR, SESSION_PREFIX

if TYPE_CHECKING:
    from shikiru.process import SubprocessRunner
    from shikiru.tmux.client import Tmux

SSH_COMMAND = "ssh"
SSH_OPTION_TERMINATOR = "--"
SSH_TTY_OPTION = "-t"
SHIKIRU_COMMAND = "shikiru"
LS_COMMAND = "ls"
JSON_OPTION = "--json"
TMUX_COMMAND = "tmux"
TMUX_TARGET_OPTION = "-t"
ATTACH_COMMAND = "attach"
SWITCH_CLIENT_COMMAND = "switch-client"
ENGINE_UP = "up"
ENGINE_DOWN = "down"
REMOTE_LIST_COMMAND = (SHIKIRU_COMMAND, LS_COMMAND, JSON_OPTION)
NO_SESSIONS_ERROR = "no Shikiru sessions found"


class SessionRow(BaseModel):
    """Progress counts for one live Shikiru tmux session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session: str
    project_dir: str
    engine_state: Literal["up", "down"]
    running: int
    queued: int
    escalated: int


_ROWS_ADAPTER: TypeAdapter[list[SessionRow]] = TypeAdapter(list[SessionRow])


EngineState = Callable[[Path], bool]
SessionPicker = Callable[[Sequence[SessionRow], str | None], Awaitable[SessionRow]]
ReaderFactory = Callable[[Path], StateReader]


class SessionLister:
    """Read local or remote Shikiru sessions without opening a writer connection."""

    def __init__(
        self,
        *,
        tmux: Tmux,
        runner: SubprocessRunner,
        engine_up: EngineState,
        reader_factory: ReaderFactory,
        remote_timeout_seconds: float,
    ) -> None:
        """Inject tmux, process, liveness, database-reader, and timeout boundaries."""
        self._tmux = tmux
        self._runner = runner
        self._engine_up = engine_up
        self._reader_factory = reader_factory
        self._remote_timeout_seconds = remote_timeout_seconds

    async def list_sessions(self, host: str | None = None) -> list[SessionRow]:
        """Return every visible Shikiru session on the local or named SSH host."""
        if host is not None:
            return await self._list_remote(host)
        return await self._list_local()

    async def _list_local(self) -> list[SessionRow]:
        try:
            names = await self._tmux.list_sessions()
            rows: list[SessionRow] = []
            for name in names:
                if not name.startswith(SESSION_PREFIX):
                    continue
                project_dir = await self._tmux.show_session_environment(
                    exact_target(name), PROJECT_DIR_ENV_VAR
                )
                if project_dir is None:
                    message = f"tmux session {name!r} has no {PROJECT_DIR_ENV_VAR}"
                    raise ConfigError(message)
                if not Path(project_dir).is_absolute():
                    message = (
                        f"tmux session {name!r} has an invalid project directory: {project_dir!r}"
                    )
                    raise ConfigError(message)
                rows.append(self._project_row(name, project_dir))
        except OSError as error:
            message = f"could not inspect local tmux sessions: {error}"
            raise ConfigError(message) from error
        return rows

    async def _list_remote(self, host: str) -> list[SessionRow]:
        _validate_host(host)
        try:
            result = await self._runner.run(
                (SSH_COMMAND, SSH_OPTION_TERMINATOR, host, *REMOTE_LIST_COMMAND),
                timeout=self._remote_timeout_seconds,
                check=True,
            )
        except OSError as error:
            message = f"could not run ssh for host {host!r}: {error}"
            raise ConfigError(message) from error
        try:
            return _ROWS_ADAPTER.validate_json(result.stdout)
        except ValidationError as error:
            message = f"host {host!r} returned invalid Shikiru session data: {error}"
            raise ConfigError(message) from error

    def _project_row(self, session: str, project_dir: str) -> SessionRow:
        root = Path(project_dir)
        reader = self._reader_factory(root)
        try:
            tasks = reader.list_tasks()
            engine_state = ENGINE_UP if self._engine_up(root) else ENGINE_DOWN
            return SessionRow(
                session=session,
                project_dir=project_dir,
                engine_state=engine_state,
                running=sum(task.status in {Status.RUNNING, Status.INTEGRATING} for task in tasks),
                queued=sum(task.status is Status.QUEUED for task in tasks),
                escalated=sum(task.status is Status.ESCALATED for task in tasks),
            )
        except (ValidationError, ValueError, TypeError) as error:
            message = f"could not read Shikiru progress from {state_db_path(root)}: {error}"
            raise DatabaseError(message) from error
        except OSError as error:
            message = f"could not inspect Shikiru project {root}: {error}"
            raise ConfigError(message) from error
        finally:
            reader.close()


class SessionAttacher:
    """Select a listed row and replace the CLI with its local or remote tmux client."""

    def __init__(self, runner: SubprocessRunner, picker: SessionPicker) -> None:
        """Use `runner` for process replacement and `picker` for interactive selection."""
        self._runner = runner
        self._picker = picker

    async def attach(
        self,
        rows: Sequence[SessionRow],
        *,
        name: str | None,
        host: str | None,
        inside_tmux: bool,
    ) -> None:
        """Replace this process with tmux or ssh for one listed session."""
        row = await self._select(rows, name, host)
        if host is None:
            command = _local_attach_argv(row.session, inside_tmux)
        else:
            _validate_host(host)
            command = _remote_attach_argv(host, row.session)
        self._runner.replace(command)

    async def _select(
        self, rows: Sequence[SessionRow], name: str | None, host: str | None
    ) -> SessionRow:
        if name is not None:
            matches = [
                row
                for row in rows
                if row.session == name or row.session.removeprefix(SESSION_PREFIX) == name
            ]
            if len(matches) == 1:
                return matches[0]
            message = f"no unique Shikiru session named {name!r}"
            raise UsageError(message)
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise UsageError(NO_SESSIONS_ERROR)
        return await self._picker(rows, host)


def _validate_host(host: str) -> None:
    if not host or host.startswith("-") or any(character.isspace() for character in host):
        message = f"invalid SSH host: {host!r}"
        raise UsageError(message)


def _local_attach_argv(session: str, inside_tmux: bool) -> tuple[str, ...]:
    operation = SWITCH_CLIENT_COMMAND if inside_tmux else ATTACH_COMMAND
    return TMUX_COMMAND, operation, TMUX_TARGET_OPTION, exact_target(session)


def _remote_attach_argv(host: str, session: str) -> tuple[str, ...]:
    _validate_host(host)
    command = shlex.join((TMUX_COMMAND, ATTACH_COMMAND, TMUX_TARGET_OPTION, exact_target(session)))
    return SSH_COMMAND, SSH_TTY_OPTION, SSH_OPTION_TERMINATOR, host, command
