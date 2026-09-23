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
from shikiru.plugins.agents.codex.agent import TRUST_PROMPT_MARKERS, create
from shikiru.process import SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.pane import TmuxPane

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer

_API_KEY_ENV = "SHIKIRU_CODEX_TEST_KEY"
_TRUST_PROMPT_ENV = "SHIKIRU_CODEX_TRUST_PROMPT"
_CODEX_HOME_RECORD_ENV = "SHIKIRU_CODEX_HOME_RECORD"


def _install_fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls"
    environment_path = tmp_path / "environment"
    executable = bin_dir / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$SHIKIRU_CODEX_CALLS"\n'
        'printf "%s\\n" "${CODEX_API_KEY-}" >> "$SHIKIRU_CODEX_ENV"\n'
        'printf "%s\\n" "${OPENAI_API_KEY-}" >> "$SHIKIRU_CODEX_ENV"\n'
        'if [ "$1" = "--version" ]; then printf "codex-cli 0.155.0\\n"; exit 0; fi\n'
        'if [ "$1" = "login" ]; then printf "Logged in\\n"; exit 0; fi\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"{\\"ok\\":false}"}}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"{\\"ok\\":true}"}}\'\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SHIKIRU_CODEX_CALLS", str(calls_path))
    monkeypatch.setenv("SHIKIRU_CODEX_ENV", str(environment_path))
    return bin_dir, calls_path, environment_path


def _install_trust_prompt_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "trust-prompt-bin"
    bin_dir.mkdir()
    executable = bin_dir / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "${CODEX_HOME-}" > "$SHIKIRU_CODEX_HOME_RECORD"\n'
        'printf "%s\\n" "$SHIKIRU_CODEX_TRUST_PROMPT" "1. Yes, trust this folder"\n'
        'hooks="$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/hooks.jsonl"\n'
        "IFS= read -r confirmation || exit 1\n"
        'mkdir -p "$(dirname "$hooks")"\n'
        'printf \'{"hook_event_name":"SessionStart"}\\n\' > "$hooks"\n'
        'printf "%s\\n" "Ask Codex to do anything"\n'
        "while IFS= read -r input; do :; done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return executable


async def test_api_key_auth_sets_codex_key_for_headless_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bin_dir, calls_path, environment_path = _install_fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv(_API_KEY_ENV, "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "parent-token")
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
        {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "gpt-5.6-sol",
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
    assert calls.count("login status") == 1


@pytest.mark.parametrize("trust_prompt", TRUST_PROMPT_MARKERS)
async def test_launch_accepts_supported_trust_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmux_server: TmuxServer,
    trust_prompt: str,
) -> None:
    _install_trust_prompt_codex(tmp_path, monkeypatch)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    codex_home_record = tmp_path / "codex-home-record"
    monkeypatch.setenv(_CODEX_HOME_RECORD_ENV, str(codex_home_record))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repository = tmp_path / "repo"
    repository.mkdir()
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    pane_id = await tmux_server.run("display-message", "-p", "#{pane_id}")
    pane = TmuxPane(tmux, pane_id.stdout.strip(), SystemClock())
    agent = create(AgentContext(options={}, runner=tmux_server.runner, clock=SystemClock()))
    spec = LaunchSpec(
        rung=Rung(agent="codex", model="gpt-5.6-sol", effort="low"),
        role="implementer",
        cwd=repository,
        run_dir=run_dir,
        add_dirs=(),
        env={
            RUN_DIR_ENV_VAR: str(run_dir),
            ROLE_ENV_VAR: "implementer",
            "PATH": os.environ["PATH"],
            _TRUST_PROMPT_ENV: trust_prompt,
            _CODEX_HOME_RECORD_ENV: str(codex_home_record),
        },
        resume_session_id=None,
    )

    await agent.launch(spec, pane)

    assert (run_dir / "implementer" / HOOKS_FILENAME).is_file()
    assert codex_home_record.read_text(encoding="utf-8").strip() == str(codex_home)
    assert await pane.is_alive()
