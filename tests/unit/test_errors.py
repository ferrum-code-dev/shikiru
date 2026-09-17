import pytest

from shikiru.errors import (
    CommandFailed,
    CommandRejectedError,
    CommandTimeoutError,
    ConfigError,
    NotInitialisedError,
    ShikiruError,
    UsageError,
)


@pytest.mark.parametrize(
    ("error", "expected_exit_code"),
    [
        (ShikiruError("boom"), 1),
        (ConfigError("bad config"), 1),
        (UsageError("bad usage"), 2),
        (NotInitialisedError("no project"), 3),
        (CommandRejectedError("rejected"), 4),
        (CommandFailed(["git", "status"], 1, "fatal"), 1),
        (CommandTimeoutError(["sleep", "10"], 0.1), 1),
    ],
)
def test_exit_code_matches_docs_operator_section_2(
    error: ShikiruError, expected_exit_code: int
) -> None:
    assert error.exit_code == expected_exit_code


def test_command_failed_message_includes_argv_and_stderr() -> None:
    error = CommandFailed(["git", "status"], 128, "not a git repository")
    assert "git" in str(error)
    assert "not a git repository" in str(error)
