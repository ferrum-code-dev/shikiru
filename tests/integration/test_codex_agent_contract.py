"""Codex satisfies the agent plugin contract with committed captures (04 §3)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    Activity,
    AgentFactory,
    FailureKind,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
    shikiru_command,
)
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.agents.testing import (
    AgentContractSuite,
    LaunchCase,
    RecordingPaneHandle,
    SampleCase,
    TmuxSocket,
)
from shikiru.agents.testing.resume import RESUME_NONCE_PROMPT, RESUME_RECALL_PROMPT
from shikiru.plugins.agents.codex.agent import create
from shikiru.plugins.agents.codex.options import CONFIG_FLAG, DEFAULT_ARGS, HOOK_EVENTS
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from typing import Any

    import pytest

    from shikiru.agents.contracts import AgentSession, AgentSignal, InteractiveAgent
    from shikiru.tmux.pane import TmuxPane

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "codex"
_MODEL = "gpt-5.6-sol"
_EFFORT = "low"
_ROLE = "implementer"
_FULL_TURN_SESSION_ID = "cbb5d575-41f3-4e6b-a24f-483c51c0a097"
_TWO_TURNS_SESSION_ID = "20633415-9075-4c25-a7e9-d007543e2297"
_ERROR_SESSION_ID = "9747b608-0cc0-4641-b010-ce2bc30c2d89"
_RESET = datetime.fromtimestamp(1789948365, tz=UTC)

_FULL_TURN_USAGE = Usage(
    input_tokens=14829,
    output_tokens=232,
    cache_read_tokens=11008,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=9.998839009287925,
)
_TWO_TURNS_USAGE = Usage(
    input_tokens=30205,
    output_tokens=326,
    cache_read_tokens=22016,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=10.210526315789474,
)
_NO_TURNS_USAGE = Usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)

_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0
_LIVE_PRIME_PROMPT = "reply with exactly READY"
_INHERITED_CODEX_ENV_VARS = (
    "CODEX_API_KEY",
    "CODEX_CI",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_SANDBOX",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "OPENAI_API_KEY",
)


class _AskResult(BaseModel):
    ok: bool


class TestCodexAgentContract(AgentContractSuite):
    """Exercise Codex's offline contract and gate live checks behind SHIKIRU_E2E."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False
    _live_codex_home: Path | None = None
    _live_hook_command: str | None = None
    _live_path: str | None = None
    _live_prompt: str | None = None

    def agent_factory(self) -> AgentFactory:
        return create

    def agent_options(self) -> Mapping[str, Any]:
        if self._live_hook_command is None:
            return {}
        return {
            "args": [
                *DEFAULT_ARGS,
                *_hook_config_overrides(self._live_hook_command),
                *([self._live_prompt] if self._live_prompt is not None else []),
            ]
        }

    def build_spec(self, tmp_path: Path, *, resume_session_id: str | None) -> LaunchSpec:
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = tmp_path / "run"
        return LaunchSpec(
            rung=Rung(agent="codex", model=_MODEL, effort=_EFFORT),
            role=_ROLE,
            cwd=repo,
            run_dir=run_dir,
            add_dirs=(),
            env=self._launch_environment(run_dir),
            resume_session_id=resume_session_id,
        )

    def _launch_environment(self, run_dir: Path) -> dict[str, str]:
        environment = {RUN_DIR_ENV_VAR: str(run_dir), ROLE_ENV_VAR: _ROLE}
        if self._live_codex_home is not None:
            environment["CODEX_HOME"] = str(self._live_codex_home)
        if self._live_path is not None:
            environment["PATH"] = self._live_path
        return environment

    def _build_agent(self, options: Mapping[str, Any] | None = None) -> InteractiveAgent:
        if self._live_codex_home is None:
            return super()._build_agent(options)
        with _codex_home_environment(self._live_codex_home):
            return super()._build_agent(options)

    def headless_ask_schema_model(self) -> type[BaseModel]:
        return _AskResult

    def headless_ask_model(self) -> str:
        return _MODEL

    def hook_signal_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="full turn",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_full_turn.jsonl",
                    "rollout_full_turn.jsonl": _FIXTURES_DIR / "rollout_full_turn.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_FULL_TURN_SESSION_ID),
                    PromptSubmitted(text="prime hooks"),
                    Activity(tool_running=True),
                    Activity(tool_running=False),
                    TurnEnded(last_message="Hooks primed."),
                ],
                expected_usage=_FULL_TURN_USAGE,
                session_id=_FULL_TURN_SESSION_ID,
            )
        ]

    def usage_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="two turns",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_two_turns.jsonl",
                    "rollout_two_turns.jsonl": _FIXTURES_DIR / "rollout_two_turns.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_TWO_TURNS_SESSION_ID),
                    PromptSubmitted(text="prime hooks"),
                    TurnEnded(last_message="First turn complete."),
                    PromptSubmitted(
                        text="Remember this nonce for later in this session: fixture-nonce"
                    ),
                    TurnEnded(last_message="Second turn complete."),
                ],
                expected_usage=_TWO_TURNS_USAGE,
                session_id=_TWO_TURNS_SESSION_ID,
            )
        ]

    def error_cases(self) -> list[SampleCase]:
        return [
            SampleCase(
                name="quota failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_quota_error.jsonl",
                    "rollout_quota_error.jsonl": _FIXTURES_DIR / "rollout_quota_error.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.QUOTA,
                        message="usage limit reached",
                        resets_at=_RESET,
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
            SampleCase(
                name="unavailable failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_unavailable_error.jsonl",
                    "rollout_unavailable_error.jsonl": _FIXTURES_DIR
                    / "rollout_unavailable_error.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.UNAVAILABLE,
                        message="service overloaded",
                        resets_at=None,
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
            SampleCase(
                name="auth failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_auth_error.jsonl",
                    "rollout_auth_error.jsonl": _FIXTURES_DIR / "rollout_auth_error.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.AUTH, message="authentication failed", resets_at=None
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
            SampleCase(
                name="context failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_context_error.jsonl",
                    "rollout_context_error.jsonl": _FIXTURES_DIR / "rollout_context_error.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.CONTEXT, message="context exceeded", resets_at=None
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
            SampleCase(
                name="other failure",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_other_error.jsonl",
                    "rollout_other_error.jsonl": _FIXTURES_DIR / "rollout_other_error.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.OTHER, message="unexpected failure", resets_at=None
                    ),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
            SampleCase(
                name="turn correlation",
                files={
                    HOOKS_FILENAME: _FIXTURES_DIR / "hooks_turn_correlation.jsonl",
                    "rollout_turn_correlation.jsonl": _FIXTURES_DIR
                    / "rollout_turn_correlation.jsonl",
                },
                expected_signals=[
                    SessionStarted(session_id=_ERROR_SESSION_ID),
                    TurnFailed(
                        kind=FailureKind.OTHER,
                        message="request failed",
                        resets_at=None,
                    ),
                    TurnEnded(last_message="Successful turn."),
                ],
                expected_usage=_NO_TURNS_USAGE,
                session_id=_ERROR_SESSION_ID,
            ),
        ]

    def launch_cases(self, tmp_path: Path) -> list[LaunchCase]:
        del tmp_path
        return []

    async def _launch_live(
        self, tmux_server: TmuxSocket, tmp_path: Path, *, prepare_turn: bool
    ) -> tuple[InteractiveAgent, LaunchSpec, TmuxPane, AgentSession]:
        self._require_live()
        repo = tmp_path / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        hook_home, codex_home = await asyncio.to_thread(_prepare_live_environment, repo, tmp_path)
        self._live_codex_home = codex_home
        self._live_hook_command = f"{hook_home / '.local' / 'bin' / 'shikiru'} hook"
        codex_path = shutil.which("codex")
        if codex_path is None:
            message = "codex executable is required for the live Codex check"
            raise AssertionError(message)
        live_path = (
            f"{hook_home / '.local' / 'bin'}{os.pathsep}"
            f"{Path(codex_path).parent}{os.pathsep}{os.defpath}"
        )
        self._live_path = live_path
        self._live_prompt = _LIVE_PRIME_PROMPT
        environment = {
            "HOME": str(hook_home),
            "CODEX_HOME": str(codex_home),
            "PATH": live_path,
            "SHELL": "/bin/sh",
        }
        for name, value in environment.items():
            for target in (("-g",), ("-t", tmux_server.session)):
                await tmux_server.runner.run(
                    [
                        "tmux",
                        "-L",
                        tmux_server.socket_name,
                        "set-environment",
                        *target,
                        name,
                        value,
                    ],
                    timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
                    check=True,
                )
        for name in _INHERITED_CODEX_ENV_VARS:
            for target in (("-g",), ("-t", tmux_server.session)):
                await tmux_server.runner.run(
                    [
                        "tmux",
                        "-L",
                        tmux_server.socket_name,
                        "set-environment",
                        *target,
                        "-r",
                        name,
                    ],
                    timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
                    check=False,
                )
        agent, spec, pane, session = await super()._launch_live(
            tmux_server, tmp_path, prepare_turn=prepare_turn
        )
        return agent, spec, pane, session

    async def _prime_live_session(
        self, tmux_server: TmuxSocket, tmp_path: Path, *, prepare_turn: bool
    ) -> tuple[
        InteractiveAgent,
        LaunchSpec,
        TmuxPane,
        AgentSession,
        AsyncIterator[AgentSignal],
        SessionStarted,
    ]:
        agent, spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=prepare_turn
        )
        signals = session.signals()
        try:
            started = await _next_signal_for_test(signals, SessionStarted, timeout=20.0)
            await _next_signal_for_test(signals, PromptSubmitted, timeout=20.0)
            await _next_signal_for_test(signals, TurnEnded, timeout=20.0)
        except TimeoutError as error:
            visible = await pane.capture_visible()
            hooks_path = spec.run_dir / spec.role / HOOKS_FILENAME
            hooks = hooks_path.read_text(encoding="utf-8") if hooks_path.is_file() else "missing"
            message = f"Codex did not emit prime signals; pane={visible!r}; hooks={hooks!r}"
            raise AssertionError(message) from error
        return agent, spec, pane, session, signals, started

    async def test_live_launch_starts_session(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        _agent, _spec, _pane, _session, _signals, started = await self._prime_live_session(
            tmux_server, tmp_path, prepare_turn=False
        )
        assert started.session_id is not None

    async def test_live_turn_completes(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        _agent, _spec, pane, _session, signals, _started = await self._prime_live_session(
            tmux_server, tmp_path, prepare_turn=True
        )
        await pane.paste_line("do the thing")
        await _next_signal_for_test(signals, PromptSubmitted, timeout=60.0)
        await _next_signal_for_test(signals, TurnEnded, timeout=60.0)

    async def test_live_usage_reports_tokens(self, tmux_server: TmuxSocket, tmp_path: Path) -> None:
        _agent, _spec, pane, session, signals, _started = await self._prime_live_session(
            tmux_server, tmp_path, prepare_turn=True
        )
        await pane.paste_line("do the thing")
        await _next_signal_for_test(signals, PromptSubmitted, timeout=60.0)
        await _next_signal_for_test(signals, TurnEnded, timeout=60.0)
        usage = await session.usage()
        assert usage.input_tokens + usage.output_tokens > 0

    async def test_live_attach_does_not_replay_signals(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        agent, spec, pane, session, signals, _started = await self._prime_live_session(
            tmux_server, tmp_path, prepare_turn=True
        )
        await pane.paste_line("first turn")
        await _next_signal_for_test(signals, PromptSubmitted, timeout=60.0)
        await _next_signal_for_test(signals, TurnEnded, timeout=60.0)
        snapshot = session.snapshot()
        resumed = await agent.attach(spec, pane, snapshot)
        resumed_signals = resumed.signals()
        await pane.paste_line("second turn")
        submitted = await _next_signal_for_test(resumed_signals, PromptSubmitted, timeout=60.0)
        assert submitted.text == "second turn"
        await _next_signal_for_test(resumed_signals, TurnEnded, timeout=60.0)

    async def test_offline_launch_argv_includes_hooks_trust_and_git_dir(
        self, tmp_path: Path
    ) -> None:
        spec = self.build_spec(tmp_path, resume_session_id=None)
        pane = RecordingPaneHandle()
        await self._build_agent().launch(spec, pane)
        call = pane.last_respawn()
        assert call.argv[:4] == ("codex", "-m", _MODEL, "-c")
        assert "--dangerously-bypass-hook-trust" in call.argv
        assert "-C" in call.argv
        assert call.argv[call.argv.index("-C") + 1] == str(spec.cwd)
        assert call.argv[call.argv.index("--add-dir") + 1] == str(spec.run_dir)
        assert call.env["CODEX_API_KEY"] is None
        assert call.env["OPENAI_API_KEY"] is None
        hook_values = [
            call.argv[index + 1]
            for index, value in enumerate(call.argv)
            if value == "-c" and call.argv[index + 1].startswith("hooks.")
        ]
        assert len(hook_values) == 8
        hook_command = json.dumps(shikiru_command("hook"))
        hook_suffix = f'[{{hooks=[{{type="command",command={hook_command}}}]}}]'
        assert all(value.endswith(hook_suffix) for value in hook_values)

        resume_spec = self.build_spec(tmp_path / "resume", resume_session_id="the-old-session-id")
        resume_pane = RecordingPaneHandle()
        await self._build_agent().launch(resume_spec, resume_pane)
        assert resume_pane.last_respawn().argv[:3] == (
            "codex",
            "resume",
            "the-old-session-id",
        )

    async def test_api_key_auth_sets_codex_key_and_unsets_openai_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHIKIRU_CODEX_TEST_KEY", "test-key")
        agent = self._build_agent({"auth": "api_key", "api_key_env": "SHIKIRU_CODEX_TEST_KEY"})
        spec = self.build_spec(tmp_path, resume_session_id=None)
        pane = RecordingPaneHandle()
        await agent.launch(spec, pane)
        environment = pane.last_respawn().env
        assert environment["CODEX_API_KEY"] == "test-key"
        assert environment["OPENAI_API_KEY"] is None

    async def test_live_quota_reports_without_error(self) -> None:
        self._require_live()
        with tempfile.TemporaryDirectory(prefix="shikiru-codex-quota-") as directory:
            root = Path(directory)
            _hook_home, codex_home = await asyncio.to_thread(_prepare_live_environment, root, root)
            self._live_codex_home = codex_home
            with _codex_home_environment(codex_home):
                await self._build_agent().quota()

    async def test_live_headless_ask_matches_schema(self, tmp_path: Path) -> None:
        self._require_live()
        _hook_home, codex_home = await asyncio.to_thread(
            _prepare_live_environment, tmp_path, tmp_path
        )
        self._live_codex_home = codex_home
        with _codex_home_environment(codex_home):
            await super().test_live_headless_ask_matches_schema(tmp_path)

    async def test_live_resume_recalls_context(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        agent, _spec, pane, _session, signals, started = await self._prime_live_session(
            tmux_server, tmp_path, prepare_turn=True
        )
        assert started.session_id is not None
        nonce = uuid.uuid4().hex
        await pane.paste_line(RESUME_NONCE_PROMPT.format(nonce=nonce))
        await _next_signal_for_test(signals, PromptSubmitted, timeout=60.0)
        await _next_signal_for_test(signals, TurnEnded, timeout=60.0)
        await pane.paste_line(agent.keys.exit_input)
        try:
            await _next_signal_for_test(signals, SessionEnded)
        except TimeoutError:
            await wait_until(lambda: _pane_dead(pane), timeout=15.0)
        resume_spec = self.build_spec(tmp_path, resume_session_id=started.session_id)
        self._live_prompt = RESUME_RECALL_PROMPT
        resumed_agent = self._build_agent()
        resumed = await resumed_agent.launch(resume_spec, pane)
        resumed_signals = resumed.signals()
        resumed_started = await _next_signal_for_test(resumed_signals, SessionStarted, timeout=60.0)
        assert resumed_started.session_id == started.session_id
        try:
            await _next_signal_for_test(resumed_signals, PromptSubmitted, timeout=60.0)
            ended = await _next_signal_for_test(resumed_signals, TurnEnded, timeout=60.0)
        except TimeoutError as error:
            visible = await pane.capture_visible()
            hooks_path = resume_spec.run_dir / resume_spec.role / HOOKS_FILENAME
            hooks = hooks_path.read_text(encoding="utf-8") if hooks_path.is_file() else "missing"
            message = f"Codex resumed turn did not end; pane={visible!r}; hooks={hooks!r}"
            raise AssertionError(message) from error
        assert nonce in ended.last_message


def _prepare_live_environment(cwd: Path, tmp_path: Path) -> tuple[Path, Path]:
    source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    source_auth = source_home / "auth.json"
    if not source_auth.is_file():
        message = f"authenticated Codex credentials are required at {source_auth}"
        raise AssertionError(message)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_auth, codex_home / "auth.json")
    (codex_home / "auth.json").chmod(0o600)
    project = json.dumps(str(cwd), ensure_ascii=False)
    (codex_home / "config.toml").write_text(
        f'check_for_update_on_startup = false\n\n[projects.{project}]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    hook_home = tmp_path / "home"
    hook_bin = hook_home / ".local" / "bin"
    hook_bin.mkdir(parents=True, exist_ok=True)
    shikiru_path = Path(sys.executable).with_name("shikiru")
    if not shikiru_path.is_file():
        fallback = shutil.which("shikiru")
        if fallback is None:
            message = "shikiru executable is required for the live Codex check"
            raise AssertionError(message)
        shikiru_path = Path(fallback)
    if not shikiru_path.is_file():
        message = "shikiru executable is required for the live Codex check"
        raise AssertionError(message)
    (hook_bin / "shikiru").symlink_to(shikiru_path)
    return hook_home, codex_home


def _hook_config_overrides(command: str) -> list[str]:
    encoded = '[{hooks=[{type="command",command=' + json.dumps(command) + "}]}]"
    return [item for event in HOOK_EVENTS for item in (CONFIG_FLAG, f"hooks.{event}={encoded}")]


async def _next_signal_for_test[S: AgentSignal](
    signals: AsyncIterator[AgentSignal], kind: type[S], *, timeout: float = 15.0
) -> S:
    while True:
        signal = await asyncio.wait_for(signals.__anext__(), timeout=timeout)
        if isinstance(signal, kind):
            return signal


async def _pane_dead(pane: TmuxPane) -> bool:
    return not await pane.is_alive()


@contextmanager
def _codex_home_environment(codex_home: Path):
    previous = {name: os.environ.get(name) for name in ("CODEX_HOME", *_INHERITED_CODEX_ENV_VARS)}
    os.environ["CODEX_HOME"] = str(codex_home)
    for name in _INHERITED_CODEX_ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
