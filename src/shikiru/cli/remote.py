"""Remote host dispatch for the global `--host` option."""

from __future__ import annotations

import shlex
import sys
from typing import NoReturn

from shikiru.errors import UsageError
from shikiru.process import SubprocessRunner

SSH_COMMAND = "ssh"
SSH_TTY_OPTION = "-t"
SSH_OPTION_TERMINATOR = "--"
SHIKIRU_COMMAND = "shikiru"
HOST_OPTION_NAME = "--host"
HOST_OPTION_ASSIGNMENT_PREFIX = "--host="
MCP_COMMAND = "mcp"
JSON_OPTION = "--json"
ATTACH_COMMAND = "attach"
INIT_COMMAND = "init"
START_COMMAND = "start"
STATUS_COMMAND = "status"
YES_OPTION = "--yes"
ATTACH_OPTION = "--attach"
WATCH_OPTION = "--watch"
GLOBAL_VALUE_OPTIONS = ("--project", "--set")
GLOBAL_VALUE_PREFIXES = ("--project=", "--set=")
DASH = "-"
DASH_PREFIX = "-"


def validate_host(host: str) -> None:
    """Reject empty, option-like or whitespace-containing hosts before ssh."""
    if not host or host.startswith("-") or any(character.isspace() for character in host):
        message = f"invalid SSH host: {host!r}"
        raise UsageError(message)


def strip_host_arguments(argv: list[str]) -> list[str]:
    """Remove global `--host` before the command; preserve everything after it."""
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == SSH_OPTION_TERMINATOR:
            forwarded.append(argument)
            forwarded.extend(argv[index + 1 :])
            return forwarded
        if argument == HOST_OPTION_NAME:
            if index + 1 >= len(argv):
                message = "--host requires a value"
                raise UsageError(message)
            index += 1
            index += 1
            continue
        if argument.startswith(HOST_OPTION_ASSIGNMENT_PREFIX):
            index += 1
            continue
        if argument in GLOBAL_VALUE_OPTIONS:
            forwarded.append(argument)
            if index + 1 < len(argv):
                forwarded.append(argv[index + 1])
                index += 1
            index += 1
            continue
        if argument.startswith(GLOBAL_VALUE_PREFIXES):
            forwarded.append(argument)
            index += 1
            continue
        if argument.startswith(DASH_PREFIX) and argument != DASH:
            forwarded.append(argument)
            index += 1
            continue
        forwarded.append(argument)
        forwarded.extend(argv[index + 1 :])
        return forwarded
    return forwarded


def _command_at(forwarded: list[str]) -> tuple[str | None, int]:
    index = 0
    while index < len(forwarded):
        argument = forwarded[index]
        if argument == SSH_OPTION_TERMINATOR:
            if index + 1 < len(forwarded):
                return forwarded[index + 1], index + 1
            return None, index
        if argument in GLOBAL_VALUE_OPTIONS:
            index += 1
            index += 1
            continue
        if argument.startswith(GLOBAL_VALUE_PREFIXES):
            index += 1
            continue
        if argument.startswith(DASH_PREFIX) and argument != DASH:
            index += 1
            continue
        return argument, index
    return None, len(forwarded)


def _has_flag(arguments: list[str], flag: str) -> bool:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == SSH_OPTION_TERMINATOR:
            return False
        if argument in GLOBAL_VALUE_OPTIONS:
            index += 1
            index += 1
            continue
        if argument.startswith(GLOBAL_VALUE_PREFIXES):
            index += 1
            continue
        if argument == flag:
            return True
        index += 1
    return False


def _has_option(forwarded: list[str], flag: str) -> bool:
    _, command_index = _command_at(forwarded)
    return _has_flag(forwarded[:command_index], flag) or _has_flag(
        forwarded[command_index + 1 :], flag
    )


def should_use_tty(forwarded: list[str]) -> bool:
    """Return whether the remote invocation needs a pseudo-terminal.

    `attach` and `start --attach` always do. `init` and `status --watch` do only when both
    local stdin and stdout are terminals, so redirected output and errors stay separate.
    Everything else gets none.
    """
    command, _ = _command_at(forwarded)
    if command == MCP_COMMAND or _has_option(forwarded, JSON_OPTION):
        return False
    if command == ATTACH_COMMAND:
        return True
    if command == START_COMMAND and _has_option(forwarded, ATTACH_OPTION):
        return True
    if command == INIT_COMMAND and not _has_option(forwarded, YES_OPTION):
        return sys.stdin.isatty() and sys.stdout.isatty()
    if command == STATUS_COMMAND and _has_option(forwarded, WATCH_OPTION):
        return sys.stdin.isatty() and sys.stdout.isatty()
    return False


def remote_command(forwarded: list[str]) -> str:
    """Build the single shell-quoted remote command line."""
    return shlex.join((SHIKIRU_COMMAND, *forwarded))


def ssh_argv(host: str, forwarded: list[str], *, use_tty: bool) -> tuple[str, ...]:
    """Build the `ssh` argv that runs the remote `shikiru` command."""
    validate_host(host)
    parts: list[str] = [SSH_COMMAND]
    if use_tty:
        parts.append(SSH_TTY_OPTION)
    parts.extend((SSH_OPTION_TERMINATOR, host, remote_command(forwarded)))
    return tuple(parts)


def dispatch_to_host(host: str, argv: list[str]) -> NoReturn:
    """Forward `argv` without `--host` over ssh, replacing this process."""
    forwarded = strip_host_arguments(argv[1:] if argv else [])
    command = ssh_argv(host, forwarded, use_tty=should_use_tty(forwarded))
    SubprocessRunner().replace(command)
