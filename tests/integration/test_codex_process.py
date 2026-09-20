from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import AgentContext
from shikiru.clock import SystemClock
from shikiru.plugins.agents.codex.agent import create
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_API_KEY_ENV = "SHIKIRU_CODEX_TEST_KEY"


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
        '"text":"{\\"ok\\":true}"}}\'\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SHIKIRU_CODEX_CALLS", str(calls_path))
    monkeypatch.setenv("SHIKIRU_CODEX_ENV", str(environment_path))
    return bin_dir, calls_path, environment_path


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
