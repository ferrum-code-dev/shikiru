"""Read Shikiru tmux sessions and prepare exact attach targets."""

from __future__ import annotations

import shlex
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from shikiru.domain.quota_waits import current_turn_quota_wait
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
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.domain.tasks import Task
    from shikiru.process import SubprocessRunner
    from shikiru.tmux.client import Tmux

SSH_COMMAND = "ssh"
SSH_OPTION_TERMINATOR = "--"
SSH_TTY_OPTION = "-t"
SHIKIRU_COMMAND = "shikiru"
LS_COMMAND = "ls"
JSON_OPTION = "--json"
ATTACH_COMMAND = "attach"
PROJECT_OPTION = "--project"
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
    waiting: int
    queued: int
    escalated: int


_ROWS_ADAPTER: TypeAdapter[list[SessionRow]] = TypeAdapter(list[SessionRow])
_TURN_STATUSES = frozenset((Status.RUNNING, Status.INTEGRATING))


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
        clock: Clock,
        remote_timeout_seconds: float,
    ) -> None:
        """Inject tmux, process, liveness, reader, clock and timeout boundaries."""
        self._tmux = tmux
        self._runner = runner
        self._engine_up = engine_up
        self._reader_factory = reader_factory
        self._clock = clock
        self._remote_timeout_seconds = remote_timeout_seconds

    async def list_sessions(
        self,
        host: str | None = None,
        *,
        project_dir: Path | None = None,
    ) -> SessionListing:
        """Return visible sessions and any per-project discovery failures."""
        if host is not None:
            return await self._list_remote(host)
        return await self._list_local(project_dir=project_dir)

    async def resolve_project(self, name: str) -> Path | None:
        """Resolve an exact project name to its directory among live Shikiru sessions.

        The name is compared with each live project's configured `[project] name`. A session
        whose project cannot be verified raises `ConfigError`, because the name could belong to
        it: no match or uniqueness is claimed while any session is unverified. Returns `None`
        only when every session was verified and none carries the name.
        """
        if not name:
            message = "project name cannot be empty"
            raise UsageError(message)
        try:
            names = await self._tmux.list_sessions()
        except OSError as error:
            message = f"could not inspect local tmux sessions: {error}"
            raise ConfigError(message) from error
        matches: list[Path] = []
        unverified: list[str] = []
        for session in names:
            if not session.startswith(SESSION_PREFIX):
                continue
            try:
                project_dir = _session_project_dir(
                    session,
                    await self._tmux.show_session_environment(
                        exact_target(session), PROJECT_DIR_ENV_VAR
                    ),
                )
                configured = _configured_project_name(project_dir)
            except ConfigError as error:
                unverified.append(str(error))
                continue
            if configured == name:
                matches.append(project_dir)
        if unverified:
            details = "; ".join(unverified)
            message = f"cannot establish whether project {name!r} is live: {details}"
            raise ConfigError(message)
        unique = list(dict.fromkeys(matches))
        if len(unique) > 1:
            message = (
                f"multiple Shikiru projects are named {name!r}; "
                "use --project PATH to select one by path"
            )
            raise UsageError(message)
        return unique[0] if unique else None

    async def _list_local(self, *, project_dir: Path | None) -> SessionListing:
        try:
            names = await self._tmux.list_sessions()
            rows: list[SessionRow] = []
            diagnostics: list[SessionDiagnostic] = []
            for name in names:
                if not name.startswith(SESSION_PREFIX):
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
            turns = [task for task in tasks if task.status in _TURN_STATUSES]
            waiting = self._count_waiting_turns(reader, turns)
            return SessionRow(
                session=session,
                project_dir=project_dir,
                engine_state=engine_state,
                running=len(turns) - waiting,
                waiting=waiting,
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

    def _count_waiting_turns(self, reader: StateReader, turns: Sequence[Task]) -> int:
        if not turns:
            return 0
        now = self._clock.now()
        return sum(_turn_is_waiting(task, reader, now) for task in turns)


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
        host: str | None,
        inside_tmux: bool,
        current_pane: str | None = None,
    ) -> None:
        """Replace this process with tmux or ssh for one listed session."""
        row = self._select(rows, host)
        if host is None:
            same_client = await admit_attachment(self._tmux, row.session, current_pane)
            if same_client:
                return
            command = self._tmux.guarded_client_argv(row.session, switch=inside_tmux)
        else:
            command = _remote_attach_argv(host, row.project_dir)
        self._runner.replace(command)

    def _select(self, rows: Sequence[SessionRow], host: str | None) -> SessionRow:
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise UsageError(NO_SESSIONS_ERROR)
        return self._prompt(rows, host)


def _session_project_dir(session: str, project_dir: str | None) -> Path:
    if project_dir is None:
        message = f"tmux session {session!r} has no {PROJECT_DIR_ENV_VAR}"
        raise ConfigError(message)
    if not Path(project_dir).is_absolute():
        message = f"tmux session {session!r} has an invalid project directory: {project_dir!r}"
        raise ConfigError(message)
    return Path(project_dir)


def _configured_project_name(root: Path) -> str | None:
    """Read the `[project] name` configured for `root`.

    Returns `None` when the project has no config file or no name. A config that exists but
    cannot be read or parsed raises `ConfigError`, since the project's name is then unknown.
    """
    path = project_config_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        message = f"could not read {path}: {error}"
        raise ConfigError(message) from error
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        message = f"could not parse {path}: {error}"
        raise ConfigError(message) from error
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    name = cast("dict[str, Any]", project).get("name")
    return name if isinstance(name, str) else None


def _turn_is_waiting(task: Task, reader: StateReader, now: datetime) -> bool:
    if task.held or task.wait_reason is not None:
        return True
    turn_wait = current_turn_quota_wait(task, reader.latest_events)
    if turn_wait is None:
        return False
    block = reader.agent_block(turn_wait.agent)
    return block is not None and block.until > now


def _same_project(session_project: Path, requested_project: Path) -> bool:
    return session_project.resolve() == requested_project.resolve()


def _validate_host(host: str) -> None:
    if not host or host.startswith("-") or any(character.isspace() for character in host):
        message = f"invalid SSH host: {host!r}"
        raise UsageError(message)


def _remote_attach_argv(host: str, project_dir: str) -> tuple[str, ...]:
    _validate_host(host)
    if not project_dir:
        message = "project directory cannot be empty"
        raise UsageError(message)
    remote_command = shlex.join(
        (
            ENV_COMMAND,
            f"{REMOTE_ORIGIN_HOST_ENV_VAR}={host}",
            TMUX_ENV_ASSIGNMENT,
            TMUX_PANE_ENV_ASSIGNMENT,
            SHIKIRU_COMMAND,
            PROJECT_OPTION,
            project_dir,
            ATTACH_COMMAND,
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
