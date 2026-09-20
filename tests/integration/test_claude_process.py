from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import AgentContext
from shikiru.clock import SystemClock
from shikiru.plugins.agents.claude.agent import create
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

_API_KEY_ENV = "SHIKIRU_CLAUDE_TEST_KEY"


def _install_fake_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls"
    environment_path = tmp_path / "environment"
    executable = bin_dir / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$SHIKIRU_CLAUDE_CALLS"\n'
        'printf "%s\\n" "${ANTHROPIC_API_KEY-}" >> "$SHIKIRU_CLAUDE_ENV"\n'
        'printf "%s\\n" "${ANTHROPIC_AUTH_TOKEN-}" >> "$SHIKIRU_CLAUDE_ENV"\n'
        'if [ "$1" = "--version" ]; then printf "Claude Code 2.1.278\\n"; exit 0; fi\n'
        'if [ "$1" = "auth" ]; then '
        'printf "{\\"loggedIn\\":%s}\\n" "$SHIKIRU_CLAUDE_LOGGED_IN"; '
        'exit "$SHIKIRU_CLAUDE_AUTH_EXIT"; fi\n'
        'printf "{\\"structured_output\\":{\\"ok\\":true}}\\n"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SHIKIRU_CLAUDE_CALLS", str(calls_path))
    monkeypatch.setenv("SHIKIRU_CLAUDE_ENV", str(environment_path))
    return bin_dir, calls_path, environment_path


async def test_api_key_auth_removes_parent_token_from_headless_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bin_dir, calls_path, environment_path = _install_fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv(_API_KEY_ENV, "test-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-token")
    monkeypatch.setenv("SHIKIRU_CLAUDE_LOGGED_IN", "true")
    monkeypatch.setenv("SHIKIRU_CLAUDE_AUTH_EXIT", "0")
    agent = create(
        AgentContext(
            options={"auth": "api_key", "api_key_env": _API_KEY_ENV},
            runner=SubprocessRunner(),
            clock=SystemClock(),
        )
    )

    headless = agent.headless()
    assert headless is not None
    assert await headless.ask(
        "ping",
        {"type": "object"},
        "haiku",
        None,
        tmp_path,
        timedelta(seconds=5),
    ) == {"ok": True}
    checks = await agent.doctor()

    assert all(check.ok for check in checks)
    environment_lines = environment_path.read_text(encoding="utf-8").splitlines()
    assert all(
        environment_lines[index : index + 2] == ["test-key", ""]
        for index in range(0, len(environment_lines), 2)
    )
    calls = calls_path.read_text(encoding="utf-8").splitlines()
    assert calls.count("auth status --json") == 1


@pytest.mark.parametrize(
    ("logged_in", "auth_exit", "expected"),
    [("true", "0", True), ("false", "0", False), ("true", "7", False)],
)
async def test_doctor_requires_successful_logged_in_auth_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: str,
    auth_exit: str,
    expected: bool,
) -> None:
    _bin_dir, calls_path, _environment_path = _install_fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv("SHIKIRU_CLAUDE_LOGGED_IN", logged_in)
    monkeypatch.setenv("SHIKIRU_CLAUDE_AUTH_EXIT", auth_exit)
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

    checks = await agent.doctor()

    assert checks[-1].ok is expected
    calls = calls_path.read_text(encoding="utf-8").splitlines()
    assert calls[-1] == "auth status --json"
    assert not any(call.startswith("-p .") for call in calls)
