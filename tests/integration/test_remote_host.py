from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from typer.testing import CliRunner

from shikiru.cli.app import app
from shikiru.cli.remote import (
    remote_command,
    should_use_tty,
    ssh_argv,
    strip_host_arguments,
    validate_host,
)
from shikiru.errors import EXIT_GENERIC_ERROR, EXIT_USAGE_ERROR, UsageError
from shikiru.process import SubprocessRunner
from tests.support.waiting import MEDIUM_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

CAPTURE_ENV = "SHIKIRU_TEST_CAPTURE"
SSH_TTY_FLAG = "-t"
SHELL_EXECUTABLE = "sh"
SHELL_COMMAND_FLAG = "-c"


def _write_executable(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / name
    executable.write_text(f"#!{sys.executable}\n{source}")
    executable.chmod(0o755)
    return executable


def _test_env(directory: Path, capture: Path) -> dict[str, str]:
    path = os.environ.get("PATH", "")
    return {
        **os.environ,
        "PATH": f"{directory}{os.pathsep}{path}",
        CAPTURE_ENV: str(capture),
        "NO_COLOR": "1",
        "COLUMNS": "80",
    }


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["--host", "buildbox", "--project", "/srv/app", "status"],
            ["--project", "/srv/app", "status"],
        ),
        (
            ["--host=buildbox", "--project", "/srv/app", "status"],
            ["--project", "/srv/app", "status"],
        ),
        (
            ["--project", "/srv/app", "--host", "buildbox", "status"],
            ["--project", "/srv/app", "status"],
        ),
        (["--host", "buildbox", "--json", "ls"], ["--json", "ls"]),
        (["--host", "buildbox", "--host", "other", "ls"], ["ls"]),
        (["--json", "ls"], ["--json", "ls"]),
        (
            ["--host", "buildbox", "submit", "--", "--host", "important"],
            ["submit", "--", "--host", "important"],
        ),
        (
            ["--host", "buildbox", "ls", "--host", "other"],
            ["ls", "--host", "other"],
        ),
        (
            ["--host", "buildbox", "submit", "fix a bug", "--host", "x"],
            ["submit", "fix a bug", "--host", "x"],
        ),
        (
            ["--host", "buildbox", "--", "ls"],
            ["--", "ls"],
        ),
    ],
)
def test_strip_host_arguments(argv: list[str], expected: list[str]) -> None:
    assert strip_host_arguments(argv) == expected


def test_strip_host_arguments_missing_value() -> None:
    with pytest.raises(UsageError, match="--host requires a value"):
        strip_host_arguments(["--host"])


@pytest.mark.parametrize("host", ["", "-oProxyCommand=foo", "bad host", "host with space"])
def test_validate_host_rejects_invalid(host: str) -> None:
    with pytest.raises(UsageError, match="invalid SSH host"):
        validate_host(host)


def test_validate_host_accepts_valid() -> None:
    validate_host("buildbox")
    validate_host("user@buildbox")
    validate_host("buildbox.example.com")


def test_remote_command_quotes_safely() -> None:
    forwarded = ["--project", "/srv/app with spaces", "status"]
    command = remote_command(forwarded)
    assert shlex.split(command) == ["shikiru", *forwarded]
    injection = ["--project", "/srv/app'; touch /tmp/pwn;#", "status"]
    command = remote_command(injection)
    assert shlex.split(command) == ["shikiru", *injection]


def test_ssh_argv_builds_single_quoted_remote() -> None:
    argv = ssh_argv("buildbox", ["ls", "--json"], use_tty=False)
    assert argv == ("ssh", "--", "buildbox", "shikiru ls --json")
    argv = ssh_argv("buildbox", ["attach"], use_tty=True)
    assert argv == ("ssh", "-t", "--", "buildbox", "shikiru attach")


@pytest.mark.asyncio
async def test_global_host_forwards_project_and_status(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("remote status output")
print("remote status stderr", file=sys.stderr)
sys.exit(7)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", "--project", "/srv/app", "status"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 7
    assert "remote status output" in result.stdout
    assert "remote status stderr" in result.stderr
    argv = json.loads(capture.read_text())
    assert argv[0:2] == ["--", "buildbox"]
    assert shlex.split(argv[2]) == ["shikiru", "--project", "/srv/app", "status"]


@pytest.mark.asyncio
async def test_global_host_ls_and_json_use_no_tty(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("[]")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    for forwarded, label in (
        (["--host", "buildbox", "ls"], "ls"),
        (["--host", "buildbox", "--json", "ls"], "json-ls"),
        (["--host", "buildbox", "mcp"], "mcp"),
    ):
        result = await SubprocessRunner().run(
            [sys.executable, "-m", "shikiru", *forwarded],
            timeout=MEDIUM_WAIT_SECONDS,
            env=env,
            check=False,
        )
        argv = json.loads(capture.read_text())
        assert argv[0] == "--"
        assert argv[1] == "buildbox"
        assert "-t" not in argv
        if label == "ls":
            assert shlex.split(argv[2]) == ["shikiru", "ls"]
        elif label == "json-ls":
            assert shlex.split(argv[2]) == ["shikiru", "--json", "ls"]
        elif label == "mcp":
            assert shlex.split(argv[2]) == ["shikiru", "mcp"]
        assert result.returncode == 0


@pytest.mark.asyncio
async def test_global_host_attach_uses_tty(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("attached")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", "attach"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    argv = json.loads(capture.read_text())
    assert argv[0] == "-t"
    assert argv[1:3] == ["--", "buildbox"]
    assert shlex.split(argv[3]) == ["shikiru", "attach"]
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_global_host_preserves_stdin_and_status_watch(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
sys.stdin.read()
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("echoed")
sys.exit(3)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [
            sys.executable,
            "-m",
            "shikiru",
            "--host",
            "buildbox",
            "--project",
            "/srv/app",
            "status",
            "--watch",
        ],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        input_text="hello stdin",
        check=False,
    )
    assert result.returncode == 3
    assert "echoed" in result.stdout
    argv = json.loads(capture.read_text())
    assert shlex.split(argv[-1]) == ["shikiru", "--project", "/srv/app", "status", "--watch"]


@pytest.mark.asyncio
async def test_global_host_quotes_project_with_spaces_and_ones(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    project = "/srv/O'Brien /spaced"
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", "--project", project, "status"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    argv = json.loads(capture.read_text())
    assert shlex.split(argv[-1]) == ["shikiru", "--project", project, "status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["-oProxyCommand=foo", "bad host", ""])
async def test_global_host_invalid_host_rejected_before_ssh(tmp_path: Path, host: str) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("should-not-run")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", host, "ls"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == EXIT_USAGE_ERROR
    assert "invalid SSH host" in result.stderr
    assert not capture.exists()


@pytest.mark.asyncio
async def test_global_host_ssh_failure_preserves_error(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    env = _test_env(bin_dir, tmp_path / "capture.json")
    env["PATH"] = f"{bin_dir}{os.pathsep}/nonexistent"
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", "status"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == EXIT_GENERIC_ERROR
    assert "ssh" in result.stderr.lower() or "could not replace" in result.stderr.lower()


@pytest.mark.asyncio
async def test_breaking_ls_host_option_removed() -> None:
    runner = CliRunner()
    result = await asyncio.to_thread(runner.invoke, app, ["ls", "--host", "buildbox"])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "No such option: --host" in unstyle(result.output)


@pytest.mark.asyncio
async def test_breaking_attach_host_option_removed() -> None:
    runner = CliRunner()
    result = await asyncio.to_thread(runner.invoke, app, ["attach", "myproj", "--host", "buildbox"])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "No such option: --host" in unstyle(result.output)


@pytest.mark.parametrize(
    ("forwarded", "stdin_tty", "stdout_tty", "expected"),
    [
        (["attach"], False, False, True),
        (["attach", "mcp"], False, False, True),
        (["--project", "/srv/mcp", "attach"], False, False, True),
        (["--", "attach"], False, False, True),
        (["status", "--watch"], True, True, True),
        (["--project", "/srv/mcp", "status", "--watch"], True, True, True),
        (["init"], True, True, True),
        (["init"], True, False, False),
        (["init", "--yes"], True, True, False),
        (["init", "--json"], True, True, False),
        (["start", "--attach"], False, False, True),
        (["start"], True, True, False),
        (["status", "--watch"], True, False, False),
        (["status", "--watch"], False, True, False),
        (["status", "--watch", "--json"], True, True, False),
        (["--json", "status", "--watch"], True, True, False),
        (["status"], True, True, False),
        (["ls"], True, True, False),
        (["submit", "--", "--watch"], True, True, False),
        (["mcp"], True, True, False),
        (["--", "ls", "--json"], True, True, False),
    ],
)
def test_should_use_tty_by_command_and_terminals(
    monkeypatch: pytest.MonkeyPatch,
    forwarded: list[str],
    stdin_tty: bool,
    stdout_tty: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout_tty)
    assert should_use_tty(forwarded) is expected


@pytest.mark.asyncio
async def test_global_host_equals_form_forwarded(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("ok")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host=buildbox", "ls", "--json"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    argv = json.loads(capture.read_text())
    assert argv[1] == "buildbox"
    assert shlex.split(argv[-1]) == ["shikiru", "ls", "--json"]


@pytest.mark.asyncio
async def test_global_host_preserves_posargs_after_dash(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("ok")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [
            sys.executable,
            "-m",
            "shikiru",
            "--host",
            "buildbox",
            "submit",
            "--",
            "--host",
            "important",
        ],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    argv = json.loads(capture.read_text())
    assert shlex.split(argv[-1]) == ["shikiru", "submit", "--", "--host", "important"]


@pytest.mark.asyncio
async def test_global_host_attach_mcp_uses_tty(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("ok")
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    result = await SubprocessRunner().run(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", "attach", "mcp"],
        timeout=MEDIUM_WAIT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    argv = json.loads(capture.read_text())
    assert argv[0] == "-t"
    assert shlex.split(argv[-1]) == ["shikiru", "attach", "mcp"]


@pytest.mark.parametrize(
    ("arguments", "uses_tty"),
    [
        (["attach"], True),
        (["--", "attach"], True),
        (["status", "--watch"], True),
        (["init"], True),
        (["init", "--yes"], False),
        (["start", "--attach"], True),
        (["start"], False),
        (["status"], False),
        (["--", "ls", "--json"], False),
        (["submit", "--", "--json"], False),
        (["submit", "--", "--watch"], False),
    ],
)
@pytest.mark.asyncio
async def test_global_host_remote_tty_follows_command_on_terminal(
    tmp_path: Path, arguments: list[str], uses_tty: bool
) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    process = await SubprocessRunner().spawn_terminal(
        [sys.executable, "-m", "shikiru", "--host", "buildbox", *arguments],
        env=env,
    )
    await wait_until(lambda: process.returncode is not None, timeout=MEDIUM_WAIT_SECONDS)
    assert process.returncode == 0
    argv = json.loads(capture.read_text())
    assert (SSH_TTY_FLAG in argv) is uses_tty


@pytest.mark.parametrize("arguments", [["status"], ["status", "--watch"]])
@pytest.mark.asyncio
async def test_global_host_redirected_output_keeps_streams_separate(
    tmp_path: Path, arguments: list[str]
) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "capture.json"
    stdout_file = tmp_path / "status.txt"
    stderr_file = tmp_path / "errors.txt"
    _write_executable(
        bin_dir,
        "ssh",
        """
import json, os, sys
from pathlib import Path
Path(os.environ["SHIKIRU_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
print("remote out")
print("remote err", file=sys.stderr)
sys.exit(0)
""",
    )
    env = _test_env(bin_dir, capture)
    command = shlex.join([sys.executable, "-m", "shikiru", "--host", "buildbox", *arguments])
    redirected = f"{command} >{shlex.quote(str(stdout_file))} 2>{shlex.quote(str(stderr_file))}"
    process = await SubprocessRunner().spawn_terminal(
        [SHELL_EXECUTABLE, SHELL_COMMAND_FLAG, redirected], env=env
    )
    await wait_until(lambda: process.returncode is not None, timeout=MEDIUM_WAIT_SECONDS)
    assert process.returncode == 0
    argv = json.loads(capture.read_text())
    assert SSH_TTY_FLAG not in argv
    assert stdout_file.read_text() == "remote out\n"
    assert stderr_file.read_text() == "remote err\n"
