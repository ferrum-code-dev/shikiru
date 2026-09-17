"""The Shikiru error hierarchy and its exit codes (docs/design/06-operator.md §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_GENERIC_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_NOT_INITIALISED = 3
EXIT_COMMAND_REJECTED = 4


class ShikiruError(Exception):
    """Base class for all errors the CLI maps to a process exit code."""

    exit_code: int = EXIT_GENERIC_ERROR


class UsageError(ShikiruError):
    """The operator invoked a command incorrectly."""

    exit_code = EXIT_USAGE_ERROR


class NotInitialisedError(ShikiruError):
    """No `.shikiru/` project was found where one was required."""

    exit_code = EXIT_NOT_INITIALISED


class CommandRejectedError(ShikiruError):
    """The engine rejected an operator command."""

    exit_code = EXIT_COMMAND_REJECTED


class ConfigError(ShikiruError):
    """Project or default configuration is missing or invalid."""


class CommandFailed(ShikiruError):
    """A subprocess run through `process.SubprocessRunner` exited non-zero."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        """Record the failed command's `argv`, exit code and captured stderr."""
        argv_tuple = tuple(argv)
        super().__init__(f"command {argv_tuple} exited {returncode}: {stderr}")
        self.argv = argv_tuple
        self.returncode = returncode
        self.stderr = stderr


class CommandTimeoutError(ShikiruError):
    """A subprocess run through `process.SubprocessRunner` was killed after its timeout elapsed."""

    def __init__(self, argv: Sequence[str], timeout: float) -> None:
        """Record the timed-out command's `argv` and its `timeout` in seconds."""
        argv_tuple = tuple(argv)
        super().__init__(f"command {argv_tuple} timed out after {timeout}s")
        self.argv = argv_tuple
        self.timeout = timeout
