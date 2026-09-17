"""The only tmux gateway: argv lists through `SubprocessRunner`, exact `=` targets (06 §5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from shikiru.process import ProcessResult, SubprocessRunner

_TMUX_BIN = "tmux"
_SOCKET_FLAG = "-L"
_COMMAND_TIMEOUT_SECONDS = 5.0
_END_OF_OPTIONS = "--"

FORMAT_PANE_ID = "#{pane_id}"
FORMAT_PANE_DEAD = "#{pane_dead}"

SplitAxis = Literal["h", "v"]


def exact_target(name: str) -> str:
    """Build the exact-match target (`=<name>`) tmux uses for a session, window or pane name."""
    return f"={name}"


def _session_scoped(target: str) -> str:
    return target if ":" in target else f"{target}:"


class Tmux:
    """Small, intention-revealing wrapper over the tmux CLI for one server (socket)."""

    def __init__(self, runner: SubprocessRunner, *, socket: str | None = None) -> None:
        """Use `runner` for every call, scoped to `socket` when one is given (tests only)."""
        self._runner = runner
        self._socket = socket

    async def new_session(
        self,
        name: str,
        *,
        cwd: Path,
        env: Mapping[str, str],
        window_name: str,
        command: Sequence[str] | None = None,
    ) -> str:
        """Create a detached session `name`, returning the id of its first (base) pane."""
        args = [
            "new-session",
            "-d",
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

    async def list_sessions(self, fmt: str) -> list[str]:
        """List every session on this server, one formatted line per session."""
        result = await self._run("list-sessions", "-F", fmt)
        return result.stdout.splitlines()

    async def show_session_environment(self, target: str, name: str) -> str | None:
        """Read session environment variable `name`, or `None` if it isn't set."""
        result = await self._run("show-environment", "-t", target, name, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip().split("=", 1)[1]

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

    async def show_window_option(self, target: str, name: str) -> str:
        """Read window option `name` on `target`."""
        result = await self._run("show-options", "-w", "-v", "-t", target, name)
        return result.stdout.strip()

    async def set_pane_option(self, target: str, name: str, value: str) -> None:
        """Set pane option `name` on `target`."""
        await self._run("set-option", "-p", "-t", target, name, value)

    async def show_pane_option(self, target: str, name: str) -> str:
        """Read pane option `name` on `target`."""
        result = await self._run("show-options", "-p", "-v", "-t", target, name)
        return result.stdout.strip()

    async def rename_window(self, target: str, name: str) -> None:
        """Rename window `target` to `name`."""
        await self._run("rename-window", "-t", target, name)

    async def set_pane_title(self, target: str, title: str) -> None:
        """Set pane `target`'s title (`#{pane_title}`)."""
        await self._run("select-pane", "-t", target, "-T", title)

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
        for key, value in env.items():
            args += ["-e", f"{key}={value}" if value is not None else f"{key}"]
        args += [_END_OF_OPTIONS, *argv]
        await self._run(*args)

    async def capture_pane(self, target: str) -> str:
        """Return the visible text of pane `target`."""
        result = await self._run("capture-pane", "-p", "-t", target)
        return result.stdout

    async def is_pane_dead(self, target: str) -> bool:
        """Return whether pane `target`'s process has exited (kept alive by `remain-on-exit`)."""
        result = await self.display(target, FORMAT_PANE_DEAD)
        return result == "1"

    async def list_panes(self, target: str, fmt: str) -> list[str]:
        """List every pane of window `target`, one formatted line per pane."""
        result = await self._run("list-panes", "-t", target, "-F", fmt)
        return result.stdout.splitlines()

    async def list_windows(self, target: str, fmt: str) -> list[str]:
        """List every window of session `target`, one formatted line per window."""
        result = await self._run("list-windows", "-t", target, "-F", fmt)
        return result.stdout.splitlines()

    async def display(self, target: str, fmt: str) -> str:
        """Evaluate format `fmt` in the context of `target` and return it, trimmed."""
        result = await self._run("display-message", "-p", "-t", target, "-F", fmt)
        return result.stdout.strip()

    async def send_keys(self, target: str, *keys: str) -> None:
        """Send `keys` to pane `target`."""
        await self._run("send-keys", "-t", target, *keys)

    async def load_buffer(self, name: str, text: str) -> None:
        """Load `text` into paste buffer `name`."""
        await self._run("load-buffer", "-b", name, "-", input_text=text)

    async def paste_buffer(self, target: str, name: str) -> None:
        """Paste buffer `name` into pane `target`, in bracketed-paste mode."""
        await self._run("paste-buffer", "-p", "-b", name, "-t", target)

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
        argv += list(args)
        return await self._runner.run(
            argv, timeout=_COMMAND_TIMEOUT_SECONDS, input_text=input_text, check=check
        )
