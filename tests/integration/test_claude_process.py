from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    AgentContext,
    LaunchSpec,
    Rung,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.clock import SystemClock
from shikiru.errors import HeadlessOutputError
from shikiru.plugins.agents.claude.agent import STARTUP_DIALOGS, create
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.pane import TmuxPane

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_API_KEY_ENV = "SHIKIRU_CLAUDE_TEST_KEY"
_DIALOG_QUESTION_ENV = "SHIKIRU_CLAUDE_DIALOG_QUESTION"
_DIALOG_ANSWER_ENV = "SHIKIRU_CLAUDE_DIALOG_ANSWER"


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
        'if [ -n "${SHIKIRU_CLAUDE_AUTH_PAYLOAD-}" ]; then '
        'printf "%s\\n" "$SHIKIRU_CLAUDE_AUTH_PAYLOAD"; '
        'else printf "{\\"loggedIn\\":%s}\\n" "$SHIKIRU_CLAUDE_LOGGED_IN"; fi; '
        'exit "$SHIKIRU_CLAUDE_AUTH_EXIT"; fi\n'
        'if [ "${SHIKIRU_CLAUDE_MALFORMED-}" = "1" ]; then printf "not-json\\n"; exit 0; fi\n'
        'printf "{\\"structured_output\\":{\\"ok\\":true}}\\n"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SHIKIRU_CLAUDE_CALLS", str(calls_path))
    monkeypatch.setenv("SHIKIRU_CLAUDE_ENV", str(environment_path))
    return bin_dir, calls_path, environment_path


def _install_dialog_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "dialog-bin"
    bin_dir.mkdir()
    executable = bin_dir / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$SHIKIRU_CLAUDE_DIALOG_QUESTION" "$SHIKIRU_CLAUDE_DIALOG_ANSWER"\n'
        'hooks="$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/hooks.jsonl"\n'
        "IFS= read -r confirmation || exit 1\n"
        'mkdir -p "$(dirname "$hooks")"\n'
        'printf \'{\\"hook_event_name\\":\\"SessionStart\\"}\\n\' > "$hooks"\n'
        "while IFS= read -r input; do :; done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return executable


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


async def test_headless_malformed_output_raises_a_dedicated_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bin_dir, _calls_path, _environment_path = _install_fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv("SHIKIRU_CLAUDE_MALFORMED", "1")
    agent = create(
        AgentContext(
            options={},
            runner=SubprocessRunner(),
            clock=SystemClock(),
        )
    )
    headless = agent.headless()
    assert headless is not None

    with pytest.raises(HeadlessOutputError):
        await headless.ask(
            "ping",
            {"type": "object"},
            "haiku",
            None,
            tmp_path,
            timedelta(seconds=5),
        )


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


async def test_doctor_login_detail_does_not_expose_auth_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bin_dir, _calls_path, _environment_path = _install_fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "SHIKIRU_CLAUDE_AUTH_PAYLOAD",
        '{"loggedIn":true,"email":"private@example.invalid","oauthToken":"secret"}',
    )
    monkeypatch.setenv("SHIKIRU_CLAUDE_AUTH_EXIT", "0")
    agent = create(AgentContext(options={}, runner=SubprocessRunner(), clock=SystemClock()))

    checks = await agent.doctor()

    assert checks[-1].ok is True
    assert checks[-1].detail == "authenticated"
    assert "private@example.invalid" not in checks[-1].detail
    assert "secret" not in checks[-1].detail


@pytest.mark.parametrize(("question", "answer"), STARTUP_DIALOGS)
async def test_launch_answers_every_startup_dialog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_server: TmuxServer,
    question: str,
    answer: str,
) -> None:
    _install_dialog_claude(tmp_path, monkeypatch)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repository = tmp_path / "repo"
    repository.mkdir()
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux_server.run("display-message", "-p", "#{pane_id}")
    pane = TmuxPane(tmux, pane_id.stdout.strip(), SystemClock())
    agent = create(AgentContext(options={}, runner=tmux_server.runner, clock=SystemClock()))
    spec = LaunchSpec(
        rung=Rung(agent="claude", model="haiku", effort="low"),
        role="leader",
        cwd=repository,
        run_dir=run_dir,
        add_dirs=(),
        env={
            RUN_DIR_ENV_VAR: str(run_dir),
            ROLE_ENV_VAR: "leader",
            "PATH": os.environ["PATH"],
            _DIALOG_QUESTION_ENV: question,
            _DIALOG_ANSWER_ENV: answer,
        },
        resume_session_id=None,
    )

    await agent.launch(spec, pane)

    assert (run_dir / "leader" / HOOKS_FILENAME).is_file()
    assert await pane.is_alive()
