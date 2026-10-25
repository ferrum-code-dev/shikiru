"""The Shikiru error hierarchy and its exit codes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

EXIT_GENERIC_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_NOT_INITIALISED = 3
EXIT_COMMAND_REJECTED = 4


class ShikiruError(Exception):
    """Base class for all errors the CLI maps to a process exit code."""

    exit_code: int = EXIT_GENERIC_ERROR


class InvariantError(ShikiruError):
    """An invariant the engine relies on does not hold: a logic bug, not an environmental fault."""


class UsageError(ShikiruError):
    """The operator invoked a command incorrectly."""

    exit_code = EXIT_USAGE_ERROR


class NotInitialisedError(ShikiruError):
    """No `.shikiru/` project was found where one was required."""

    def __init__(
        self,
        message: str,
        *,
        project_root: Path | None = None,
        missing_path: Path | None = None,
    ) -> None:
        """Record the missing project location when the CLI can identify it."""
        super().__init__(message)
        self.project_root = project_root
        self.missing_path = missing_path

    exit_code = EXIT_NOT_INITIALISED


class CommandRejectedError(ShikiruError):
    """The engine rejected an operator command."""

    exit_code = EXIT_COMMAND_REJECTED


class StaleTaskBranchError(ShikiruError):
    """A task branch left by an earlier state already exists and is not at the task's base."""


class ConfigError(ShikiruError):
    """Project or default configuration is missing or invalid."""


class ProjectNotRepositoryRootError(ConfigError):
    """The selected project directory is outside or below a Git repository root."""

    def __init__(
        self,
        message: str,
        *,
        project_root: Path,
        repository_root: Path | None,
    ) -> None:
        """Record both the selected directory and an enclosing repository, when available."""
        super().__init__(message)
        self.project_root = project_root
        self.repository_root = repository_root


class RepositoryHasNoCommitsError(ConfigError):
    """The selected Git repository does not have a commit at HEAD."""

    def __init__(self, message: str, *, project_root: Path) -> None:
        """Record the repository whose first commit is required."""
        super().__init__(message)
        self.project_root = project_root


class HeadlessOutputError(ShikiruError):
    """An agent's headless response did not contain valid structured output."""


class DatabaseError(ShikiruError):
    """The state database could not be opened, migrated or queried."""


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


PROCESS_FAILURES = (CommandFailed, CommandTimeoutError)
"""A subprocess call that exited non-zero or timed out."""


class DetachedProcessError(ShikiruError):
    """A detached process could not be launched."""

    def __init__(self, argv: Sequence[str], error: OSError) -> None:
        """Record the detached command and operating-system error."""
        argv_tuple = tuple(argv)
        super().__init__(f"could not launch detached command {argv_tuple}: {error}")
        self.argv = argv_tuple
        self.error = error


class ProcessReplacementError(ShikiruError):
    """A process could not replace the current process."""

    def __init__(self, argv: Sequence[str], error: OSError) -> None:
        """Record the replacement command and operating-system error."""
        argv_tuple = tuple(argv)
        super().__init__(f"could not replace process with {argv_tuple}: {error}")
        self.argv = argv_tuple
        self.error = error
