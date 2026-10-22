"""Read Shikiru tmux sessions and prepare exact attach targets (06 §5.6)."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from shikiru.domain.tasks import Status
from shikiru.errors import (
    CommandFailed,
    CommandTimeoutError,
    ConfigError,
    DatabaseError,
    UsageError,
)
from shikiru.operator.attachment import admit_attachment
from shikiru.paths import project_config_path, state_db_path
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
ATTACH_COMMAND = "attach"
ATTACH_OPTION_TERMINATOR = "--"
TMUX_ENV_ASSIGNMENT = "TMUX="
TMUX_PANE_ENV_ASSIGNMENT = "TMUX_PANE="
ENV_COMMAND = "env"
ENGINE_UP = "up"
ENGINE_DOWN = "down"
REMOTE_LIST_COMMAND = (SHIKIRU_COMMAND, LS_COMMAND, JSON_OPTION)
NO_SESSIONS_ERROR = "no Shikiru sessions found"
REMOTE_PARTIAL_EXIT_CODE = 1
REMOTE_ORIGIN_HOST_ENV_VAR = "SHIKIRU_REMOTE_ORIGIN_HOST"


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


@dataclass(frozen=True, slots=True)
class SessionRepair:
    label: str
    instruction: str
    command: str


@dataclass(frozen=True, slots=True)
class SessionDiagnostic:
    session: str
    session_id: str | None
    project_dir: str | None
    reason: str
    repairs: tuple[SessionRepair, ...]
    note: str

    @property
    def repair_commands(self) -> tuple[str, ...]:
        return tuple(repair.command for repair in self.repairs)


@dataclass(frozen=True, slots=True)
class SessionListing:
    rows: tuple[SessionRow, ...]
    diagnostics: tuple[SessionDiagnostic, ...] = ()
    remote_diagnostics: str = ""
    remote_exit_code: int = 0


EngineState = Callable[[Path], bool]
SessionPrompt = Callable[[Sequence[SessionRow], str | None], SessionRow]
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

    async def list_sessions(
        self,
        host: str | None = None,
        *,
        project_dir: Path | None = None,
        session_name: str | None = None,
    ) -> SessionListing:
        """Return visible sessions and any per-project discovery failures."""
        if host is not None:
            return await self._list_remote(host)
        return await self._list_local(project_dir=project_dir, session_name=session_name)

    async def _list_local(
        self, *, project_dir: Path | None, session_name: str | None
    ) -> SessionListing:
        try:
            names = await self._tmux.list_sessions()
            rows: list[SessionRow] = []
            diagnostics: list[SessionDiagnostic] = []
            for name in names:
                if not name.startswith(SESSION_PREFIX):
                    continue
                if session_name is not None and not _session_matches_name(name, session_name):
                    continue
                session_project_dir: str | None = None
                try:
                    session_project_dir = await self._tmux.show_session_environment(
                        exact_target(name), PROJECT_DIR_ENV_VAR
                    )
                except ConfigError as error:
                    diagnostics.append(
                        await _session_diagnostic(self._tmux, name, session_project_dir, error)
                    )
                    continue
                if session_project_dir is None:
                    message = f"tmux session {name!r} has no {PROJECT_DIR_ENV_VAR}"
                    diagnostics.append(
                        await _session_diagnostic(self._tmux, name, None, ConfigError(message))
                    )
                    continue
                if not Path(session_project_dir).is_absolute():
                    message = (
                        "tmux session "
                        f"{name!r} has an invalid project directory: {session_project_dir!r}"
                    )
                    diagnostics.append(
                        await _session_diagnostic(
                            self._tmux, name, session_project_dir, ConfigError(message)
                        )
                    )
                    continue
                try:
                    if project_dir is not None and not _same_project(
                        Path(session_project_dir), project_dir
                    ):
                        continue
                    rows.append(self._project_row(name, session_project_dir))
                except (ConfigError, DatabaseError, OSError, ValidationError, ValueError) as error:
                    diagnostics.append(
                        await _session_diagnostic(self._tmux, name, session_project_dir, error)
                    )
        except OSError as error:
            message = f"could not inspect local tmux sessions: {error}"
            raise ConfigError(message) from error
        return SessionListing(tuple(rows), tuple(diagnostics))

    async def _list_remote(self, host: str) -> SessionListing:
        _validate_host(host)
        try:
            result = await self._runner.run(
                (SSH_COMMAND, SSH_OPTION_TERMINATOR, host, *REMOTE_LIST_COMMAND),
                timeout=self._remote_timeout_seconds,
                check=False,
            )
        except OSError as error:
            message = f"could not run ssh for host {host!r}: {error}"
            raise ConfigError(message) from error
        if result.returncode not in {0, REMOTE_PARTIAL_EXIT_CODE}:
            raise CommandFailed(result.argv, result.returncode, result.stderr)
        try:
            rows = _ROWS_ADAPTER.validate_json(result.stdout)
        except ValidationError as error:
            message = f"host {host!r} returned invalid Shikiru session data: {error}"
            raise ConfigError(message) from error
        remote_diagnostics = result.stderr
        if result.returncode == REMOTE_PARTIAL_EXIT_CODE and not remote_diagnostics:
            remote_diagnostics = f"remote ls exited with status {result.returncode}"
        return SessionListing(
            tuple(rows),
            remote_diagnostics=remote_diagnostics,
            remote_exit_code=result.returncode,
        )

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
        except (ValidationError, ValueError) as error:
            message = f"could not read Shikiru progress from {state_db_path(root)}: {error}"
            raise DatabaseError(message) from error
        except OSError as error:
            message = f"could not inspect Shikiru project {root}: {error}"
            raise ConfigError(message) from error
        finally:
            reader.close()


class SessionAttacher:
    """Select a listed row and replace the CLI with its local or remote tmux client."""

    def __init__(self, runner: SubprocessRunner, tmux: Tmux, prompt: SessionPrompt) -> None:
        """Use `runner` for process replacement and `prompt` for interactive selection."""
        self._runner = runner
        self._tmux = tmux
        self._prompt = prompt

    async def attach(
        self,
        rows: Sequence[SessionRow],
        *,
        name: str | None,
        host: str | None,
        inside_tmux: bool,
        current_pane: str | None = None,
    ) -> None:
        """Replace this process with tmux or ssh for one listed session."""
        if host is not None and name is not None:
            self._runner.replace(_remote_attach_argv(host, name))
            return
        row = self._select(rows, name, host)
        if host is None:
            same_client = await admit_attachment(self._tmux, row.session, current_pane)
            if same_client:
                return
            command = self._tmux.guarded_client_argv(row.session, switch=inside_tmux)
        else:
            command = _remote_attach_argv(host, row.session)
        self._runner.replace(command)

    def _select(self, rows: Sequence[SessionRow], name: str | None, host: str | None) -> SessionRow:
        if name is not None:
            matches = [row for row in rows if _session_matches_name(row.session, name)]
            if len(matches) == 1:
                return matches[0]
            message = f"no unique Shikiru session named {name!r}"
            raise UsageError(message)
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise UsageError(NO_SESSIONS_ERROR)
        return self._prompt(rows, host)


def _session_matches_name(session: str, name: str) -> bool:
    return session == name or session.removeprefix(SESSION_PREFIX) == name


def _same_project(session_project: Path, requested_project: Path) -> bool:
    return session_project.resolve() == requested_project.resolve()


def _validate_host(host: str) -> None:
    if not host or host.startswith("-") or any(character.isspace() for character in host):
        message = f"invalid SSH host: {host!r}"
        raise UsageError(message)


def _remote_attach_argv(host: str, name: str) -> tuple[str, ...]:
    _validate_host(host)
    if not name:
        message = "session name cannot be empty"
        raise UsageError(message)
    remote_command = shlex.join(
        (
            ENV_COMMAND,
            f"{REMOTE_ORIGIN_HOST_ENV_VAR}={host}",
            TMUX_ENV_ASSIGNMENT,
            TMUX_PANE_ENV_ASSIGNMENT,
            SHIKIRU_COMMAND,
            ATTACH_COMMAND,
            ATTACH_OPTION_TERMINATOR,
            name,
        )
    )
    return SSH_COMMAND, SSH_TTY_OPTION, SSH_OPTION_TERMINATOR, host, remote_command


async def _session_diagnostic(
    tmux: Tmux,
    session: str,
    project_dir: str | None,
    error: Exception,
) -> SessionDiagnostic:
    try:
        session_id = await tmux.session_id(session)
    except (CommandFailed, CommandTimeoutError, OSError, ValueError) as lookup_error:
        session_id = None
        lookup_failure = (
            f"Tmux session ID lookup failed ({type(lookup_error).__name__}): {lookup_error}"
        )
    else:
        lookup_failure = None
    repairs, note = _session_repairs(session_id, project_dir)
    if lookup_failure is not None:
        note = f"{note} {lookup_failure}"
    return SessionDiagnostic(
        session=session,
        session_id=session_id,
        project_dir=project_dir,
        reason=str(error),
        repairs=repairs,
        note=note,
    )


def _session_repairs(
    session_id: str | None,
    project_dir: str | None,
) -> tuple[tuple[SessionRepair, ...], str]:
    repairs: list[SessionRepair] = []
    if session_id is not None:
        repairs.append(
            SessionRepair(
                "Session",
                "Remove the retained session if it is no longer needed.",
                shlex.join(("tmux", "kill-session", "-t", session_id)),
            )
        )
    if project_dir is None or not Path(project_dir).is_absolute():
        note = "The project path is missing or invalid."
    else:
        root = Path(project_dir)
        if root.is_dir() and not project_config_path(root).exists():
            repairs.append(
                SessionRepair(
                    "Project",
                    "Initialize fresh state if intended; this cannot recover a deleted database.",
                    shlex.join((SHIKIRU_COMMAND, "--project", project_dir, "init")),
                )
            )
            note = "Initialization creates fresh project state."
        elif root.is_dir():
            note = (
                "Removing the retained tmux session does not repair the unavailable project state."
            )
        else:
            note = "The project directory is absent."
    if session_id is None:
        note = f"{note} A stable tmux session ID is unavailable, so no kill command is offered."
    return tuple(repairs), note
