"""The only tmux gateway: argv lists through `SubprocessRunner`, exact `=` targets (06 §5.1)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from shikiru.errors import CommandFailed

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shikiru.process import ProcessResult, SubprocessRunner

_TMUX_BIN = "tmux"
_SOCKET_FLAG = "-L"
_CONFIG_FLAG = "-f"
_COMMAND_TIMEOUT_SECONDS = 5.0
_END_OF_OPTIONS = "--"
_ENV_COMMAND = "env"
_PRIVATE_SOCKET_SUFFIX_LENGTH = 8

FORMAT_PANE_ID = "#{pane_id}"
FORMAT_PANE_PID = "#{pane_pid}"
FORMAT_PANE_DEAD = "#{pane_dead}"
FORMAT_WINDOW_ID = "#{window_id}"
FORMAT_SOCKET_PATH = "#{socket_path}"
FORMAT_SESSION_NAME = "#{session_name}"
NO_SERVER_RUNNING = "no server running on "
ERROR_CONNECTING = "error connecting to "
SOCKET_MISSING = "(No such file or directory)"

SplitAxis = Literal["h", "v"]


def private_socket_name(prefix: str) -> str:
    """Return a unique socket name short enough for the OS socket path limit."""
    return f"{prefix}{uuid.uuid4().hex[:_PRIVATE_SOCKET_SUFFIX_LENGTH]}"


def exact_target(name: str) -> str:
    """Build the exact-match target (`=<name>`) tmux uses for a session, window or pane name."""
    return f"={name}"


def _session_scoped(target: str) -> str:
    return target if ":" in target else f"{target}:"


class Tmux:
    """Small, intention-revealing wrapper over the tmux CLI for one server (socket)."""

    def __init__(
        self,
        runner: SubprocessRunner,
        *,
        socket: str | None = None,
        env: Mapping[str, str] | None = None,
        config_file: Path | None = None,
    ) -> None:
        """Use `runner` for every call, scoped to `socket` when one is given.

        A `config_file` replaces the user's tmux configuration when this client starts a server.
        """
        self._runner = runner
        self._socket = socket
        self._config_file = config_file
        self._env = env

    async def new_session(
        self,
        name: str,
        *,
        cwd: Path,
        env: Mapping[str, str],
        window_name: str,
        size: tuple[int, int],
        command: Sequence[str] | None = None,
    ) -> str:
        """Create a detached session `name` sized `size` until a client attaches.

        Returns the id of its first (base) pane.
        """
        columns, rows = size
        args = [
            "new-session",
            "-d",
            "-x",
            str(columns),
            "-y",
            str(rows),
            "-P",
            "-F",
            FORMAT_PANE_ID,
            "-s",
            name,
            "-c",
            str(cwd),
            "-n",
            window_name,
        ]
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        args += self._command_args(command)
        result = await self._run(*args)
        return result.stdout.strip()

    async def target_exists(self, target: str) -> bool:
        """Return whether `target` (a session or a window, exactly) exists.

        `has-session` reports on either: it errors with "can't find window" for a missing
        window target just as it does with "can't find session" for a missing session one.
        """
        result = await self._run("has-session", "-t", target, check=False)
        return result.returncode == 0

    async def show_session_environment(self, target: str, name: str) -> str | None:
        """Read session environment variable `name`, or `None` if unset or removed.

        `show-environment` exits 0 and prints `-<name>` (no `=`) for a variable that was
        *removed* from the session (e.g. via `set-environment -r`), so a missing separator
        means unset just as much as a non-zero exit does.
        """
        result = await self._run("show-environment", "-t", target, name, check=False)
        if result.returncode != 0:
            return None
        _, separator, value = result.stdout.strip().partition("=")
        if not separator:
            return None
        return value

    async def set_session_option(self, target: str, name: str, value: str) -> None:
        """Set session option `name` on `target`.

        `set-option` takes a target-pane, so a session-only `target` (no `:window` part) is
        qualified with a trailing `:` to mean that session's current window; tmux still applies
        a non-pane, non-window option at session scope.
        """
        await self._run("set-option", "-t", _session_scoped(target), name, value)

    async def set_window_option(self, target: str, name: str, value: str) -> None:
        """Set window option `name` on `target`."""
        await self._run("set-option", "-w", "-t", target, name, value)

    async def set_pane_option(self, target: str, name: str, value: str) -> None:
        """Set pane option `name` on `target`."""
        await self._run("set-option", "-p", "-t", target, name, value)

    async def rename_window(self, target: str, name: str) -> None:
        """Rename window `target` to `name`."""
        await self._run("rename-window", "-t", target, name)

    async def set_pane_title(self, target: str, title: str) -> None:
        """Set pane `target`'s title (`#{pane_title}`)."""
        await self._run("select-pane", "-t", target, "-T", title)

    async def select_pane(self, target: str) -> None:
        """Select the window containing pane `target`, then make that pane active."""
        await self._run("select-window", "-t", target)
        await self._run("select-pane", "-t", target)

    async def new_window(
        self,
        target_session: str,
        *,
        name: str,
        cwd: Path | None = None,
        command: Sequence[str] | None = None,
    ) -> str:
        """Create a window named `name` in `target_session`, returning its base pane id."""
        args = ["new-window", "-P", "-F", FORMAT_PANE_ID, "-t", target_session, "-n", name]
        if cwd is not None:
            args += ["-c", str(cwd)]
        args += self._command_args(command)
        result = await self._run(*args)
        return result.stdout.strip()

    async def kill_window(self, target: str) -> None:
        """Kill window `target`."""
        await self._run("kill-window", "-t", target)

    async def kill_session(self, target: str) -> None:
        """Kill session `target`."""
        await self._run("kill-session", "-t", target)

    async def kill_server(self) -> None:
        """Stop this server and remove the socket file tmux leaves; no-op without a server."""
        located = await self._run("display-message", "-p", FORMAT_SOCKET_PATH, check=False)
        if located.returncode != 0:
            return
        await self._run("kill-server")
        await asyncio.to_thread(Path(located.stdout.strip()).unlink, missing_ok=True)

    async def display_message(self, target: str, message: str) -> None:
        """Display `message` in the status line for `target`."""
        await self._run("display-message", "-t", target, message)

    async def split_window(
        self,
        target: str,
        *,
        axis: SplitAxis,
        size_pct: int,
        cwd: Path | None = None,
    ) -> str:
        """Split pane `target`, returning the new pane's id sized `size_pct`% of `target`."""
        axis_flag = "-h" if axis == "h" else "-v"
        args = [
            "split-window",
            axis_flag,
            "-P",
            "-F",
            FORMAT_PANE_ID,
            "-l",
            f"{size_pct}%",
            "-t",
            target,
        ]
        if cwd is not None:
            args += ["-c", str(cwd)]
        result = await self._run(*args)
        return result.stdout.strip()

    async def respawn_pane(
        self,
        target: str,
        argv: Sequence[str],
        *,
        env: Mapping[str, str | None],
        cwd: Path,
    ) -> None:
        """Restart pane `target`'s process, killing any survivor first."""
        args = ["respawn-pane", "-k", "-t", target, "-c", str(cwd)]
        command = [_ENV_COMMAND]
        for key, value in env.items():
            if value is None:
                command += ["-u", key]
        for key, value in env.items():
            if value is not None:
                command.append(f"{key}={value}")
        args += [_END_OF_OPTIONS, *command, *argv]
        await self._run(*args)

    async def is_pane_dead(self, target: str) -> bool:
        """Return whether pane `target`'s process has exited (kept alive by `remain-on-exit`)."""
        if not await self.pane_exists(target):
            return True
        result = await self.display(target, FORMAT_PANE_DEAD)
        return result == "1"

    async def list_panes(self, target: str, fmt: str) -> list[str]:
        """List every pane of `target`, one formatted line per pane."""
        result = await self._run("list-panes", "-t", target, "-F", fmt)
        return result.stdout.splitlines()

    async def list_session_panes(self, target: str, fmt: str) -> list[str]:
        """List every pane across every window of session `target`."""
        result = await self._run("list-panes", "-s", "-t", target, "-F", fmt)
        return result.stdout.splitlines()

    async def list_sessions(self) -> list[str]:
        """List session names, returning an empty list when this server is absent."""
        result = await self._run("list-sessions", "-F", FORMAT_SESSION_NAME, check=False)
        if result.returncode == 0:
            return result.stdout.splitlines()
        error = result.stderr.strip()
        if error.startswith(NO_SERVER_RUNNING) or (
            error.startswith(ERROR_CONNECTING) and error.endswith(SOCKET_MISSING)
        ):
            return []
        raise CommandFailed(result.argv, result.returncode, result.stderr)

    async def pane_exists(self, target: str) -> bool:
        """Return whether an exact pane target exists."""
        result = await self._run(
            "display-message", "-p", "-t", target, "-F", FORMAT_PANE_ID, check=False
        )
        return result.returncode == 0 and result.stdout.strip() == target

    async def window_id(self, target: str) -> str:
        """Return the stable window id for pane or window target `target`."""
        return await self.display(target, FORMAT_WINDOW_ID)

    async def display(self, target: str, fmt: str) -> str:
        """Evaluate format `fmt` in the context of `target` and return it, trimmed."""
        result = await self._run("display-message", "-p", "-t", target, "-F", fmt)
        return result.stdout.strip()

    async def bind_key(self, key: str, *command: str, table: str = "prefix") -> None:
        """Bind `key` in key table `table` to run `command` (a single tmux command line)."""
        await self._run("bind-key", "-T", table, key, *command)

    @staticmethod
    def _command_args(command: Sequence[str] | None) -> list[str]:
        if command is None:
            return []
        return [_END_OF_OPTIONS, *command]

    async def _run(
        self, *args: str, input_text: str | None = None, check: bool = True
    ) -> ProcessResult:
        argv = [_TMUX_BIN]
        if self._socket is not None:
            argv += [_SOCKET_FLAG, self._socket]
        if self._config_file is not None:
            argv += [_CONFIG_FLAG, str(self._config_file)]
        argv += list(args)
        return await self._runner.run(
            argv,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            env=self._env,
            input_text=input_text,
            check=check,
        )
