from shikiru.errors import CommandFailed


def test_command_failed_message_includes_argv_and_stderr() -> None:
    error = CommandFailed(["git", "status"], 128, "not a git repository")

    assert "git" in str(error)
    assert "not a git repository" in str(error)


def test_command_failed_stores_argv_as_a_tuple() -> None:
    error = CommandFailed(["git", "status"], 128, "not a git repository")

    assert error.argv == ("git", "status")
