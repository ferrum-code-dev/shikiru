"""Codex satisfies the agent plugin contract with committed captures (04 §3)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import BaseModel

from shikiru.agents.contracts import (
    ROLE_ENV_VAR,
    RUN_DIR_ENV_VAR,
    Activity,
    AgentFactory,
    AttentionNeeded,
    Compacted,
    FailureKind,
    LaunchSpec,
    PromptSubmitted,
    Rung,
    SessionEnded,
    SessionStarted,
    TurnEnded,
    TurnFailed,
    Usage,
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
from tests.support.waiting import WaitTimeoutError, wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from shikiru.agents.contracts import AgentSession, AgentSignal, InteractiveAgent
    from shikiru.tmux.pane import TmuxPane

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "codex"
_MODEL = "gpt-5.6-sol"
_EFFORT = "low"
_ROLE = "implementer"
_FULL_TURN_SESSION_ID = "11111111-1111-1111-1111-111111111111"
_TWO_TURNS_SESSION_ID = "22222222-2222-2222-2222-222222222222"
_ERROR_SESSION_ID = "33333333-3333-3333-3333-333333333333"
_RESET = datetime.fromtimestamp(1893459600, tz=UTC)

_FULL_TURN_USAGE = Usage(
    input_tokens=100,
    output_tokens=50,
    cache_read_tokens=10,
    cache_write_tokens=5,
    cost_usd=0.00062625,
    cost_basis="api_equivalent",
    context_used_pct=0.03869969040247678,
)
_TWO_TURNS_USAGE = Usage(
    input_tokens=300,
    output_tokens=150,
    cache_read_tokens=30,
    cache_write_tokens=15,
    cost_usd=0.00187875,
    cost_basis="api_equivalent",
    context_used_pct=0.07739938080495357,
)
_NO_TURNS_USAGE = Usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="api_equivalent",
    context_used_pct=None,
)

_LIVE_START_TIMEOUT_SECONDS = 45.0
_PROMPT_STABILITY_COUNT = 3
_TRUST_PROMPT_MARKERS = ("Do you trust the contents of this directory?", "Press enter to continue")
_UPDATE_PROMPT_MARKERS = ("Update now (runs", "Skip", "Press enter to continue")
_HOOKS_COMPLETION_MARKER = "/hooks  view and manage lifecycle hooks"
_HOOKS_OVERVIEW_MARKERS = ("Press t to trust all", "Press enter to view hooks")
_HOOK_DETAIL_MARKER = "Press t to trust; esc to go back"
_HOOK_TRUSTED_MARKER = "Trust     Trusted"
_HOOK_CLOSE_ATTEMPTS = 3
_HOOK_CLOSE_TIMEOUT_SECONDS = 5.0
_PROMPT_MARKER = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0


class _AskResult(BaseModel):
    ok: bool


class TestCodexAgentContract(AgentContractSuite):
    """Exercise Codex's offline contract and gate live checks behind SHIKIRU_E2E."""

    runs_live_checks_in_default_gate: ClassVar[bool] = False

    def agent_factory(self) -> AgentFactory:
        return create

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
            env={RUN_DIR_ENV_VAR: str(run_dir), ROLE_ENV_VAR: _ROLE},
            resume_session_id=resume_session_id,
        )

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
                    PromptSubmitted(text="implement the retry helper"),
                    Activity(tool_running=True),
                    Activity(tool_running=False),
                    TurnEnded(last_message="accept: implement the retry helper"),
                    Compacted(),
                    AttentionNeeded(message="waiting for operator input"),
                    SessionEnded(),
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
                    PromptSubmitted(text="first turn"),
                    TurnEnded(last_message="first turn complete"),
                    PromptSubmitted(text="second turn"),
                    TurnEnded(last_message="second turn complete"),
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
                    TurnFailed(
                        kind=FailureKind.QUOTA,
                        message="usage limit reached",
                        resets_at=_RESET,
                    )
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
                    TurnFailed(
                        kind=FailureKind.UNAVAILABLE,
                        message="service overloaded",
                        resets_at=None,
                    )
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
                    TurnFailed(
                        kind=FailureKind.AUTH, message="authentication failed", resets_at=None
                    )
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
                    TurnFailed(kind=FailureKind.CONTEXT, message="context exceeded", resets_at=None)
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
                    TurnFailed(kind=FailureKind.OTHER, message="unexpected failure", resets_at=None)
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
        spec = self.build_spec(tmp_path, resume_session_id=None)
        hook_home = await asyncio.to_thread(
            _prepare_live_environment,
            os.environ.get("CODEX_HOME"),
            spec.cwd,
            tmp_path,
        )
        await tmux_server.runner.run(
            [
                "tmux",
                "-L",
                tmux_server.socket_name,
                "set-environment",
                "-g",
                "HOME",
                str(hook_home),
            ],
            timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
            check=True,
        )
        await tmux_server.runner.run(
            [
                "tmux",
                "-L",
                tmux_server.socket_name,
                "set-environment",
                "-g",
                "SHELL",
                "/bin/sh",
            ],
            timeout=_TMUX_COMMAND_TIMEOUT_SECONDS,
            check=True,
        )
        agent, spec, pane, session = await super()._launch_live(
            tmux_server, tmp_path, prepare_turn=prepare_turn
        )
        hooks_path = spec.run_dir / spec.role / HOOKS_FILENAME
        await _prepare_codex_startup(pane, hooks_path)
        return agent, spec, pane, session

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
        assert all(
            value.endswith('[{hooks=[{type="command",command="shikiru hook"}]}]')
            for value in hook_values
        )

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

    async def test_live_resume_recalls_context(
        self, tmux_server: TmuxSocket, tmp_path: Path
    ) -> None:
        agent, _spec, pane, session = await self._launch_live(
            tmux_server, tmp_path, prepare_turn=True
        )
        signals = session.signals()
        started = await _next_signal_for_test(signals, SessionStarted)
        if started.session_id is None:
            pytest.skip("Codex did not provide a resumable session id")
        nonce = uuid.uuid4().hex
        await pane.paste_line(RESUME_NONCE_PROMPT.format(nonce=nonce))
        await _next_signal_for_test(signals, TurnEnded)
        await pane.paste_line(agent.keys.exit_input)
        try:
            await _next_signal_for_test(signals, SessionEnded)
        except TimeoutError:
            await wait_until(lambda: _pane_dead(pane), timeout=15.0)
        resume_spec = self.build_spec(tmp_path, resume_session_id=started.session_id)
        resumed = await agent.launch(resume_spec, pane)
        resumed_signals = resumed.signals()
        try:
            resumed_started = await _next_signal_for_test(resumed_signals, SessionStarted)
        except TimeoutError as error:
            visible = await pane.capture_visible()
            hooks_path = resume_spec.run_dir / resume_spec.role / HOOKS_FILENAME
            hooks = hooks_path.read_text(encoding="utf-8") if hooks_path.is_file() else "missing"
            message = f"Codex resume did not start; pane={visible!r}; hooks={hooks!r}"
            raise AssertionError(message) from error
        assert resumed_started.session_id == started.session_id
        try:
            await wait_until(
                lambda: _resumed_prompt_visible(pane), timeout=_LIVE_START_TIMEOUT_SECONDS
            )
        except WaitTimeoutError as error:
            visible = await pane.capture_visible()
            message = f"Codex resumed prompt did not open; pane={visible!r}"
            raise AssertionError(message) from error
        await pane.paste_line(RESUME_RECALL_PROMPT)
        try:
            ended = await _next_signal_for_test(resumed_signals, TurnEnded, timeout=60.0)
        except TimeoutError as error:
            visible = await pane.capture_visible()
            hooks_path = resume_spec.run_dir / resume_spec.role / HOOKS_FILENAME
            hooks = hooks_path.read_text(encoding="utf-8") if hooks_path.is_file() else "missing"
            message = f"Codex resumed turn did not end; pane={visible!r}; hooks={hooks!r}"
            raise AssertionError(message) from error
        assert nonce in ended.last_message


def _prepare_live_environment(codex_home: str | None, cwd: Path, tmp_path: Path) -> Path:
    if codex_home is not None:
        home = Path(codex_home)
        home.mkdir(parents=True, exist_ok=True)
        project = json.dumps(str(cwd), ensure_ascii=False)
        (home / "config.toml").write_text(
            f"check_for_update_on_startup = false\n\n"
            f'[projects.{project}]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
    hook_home = tmp_path / "home"
    hook_bin = hook_home / ".local" / "bin"
    hook_bin.mkdir(parents=True, exist_ok=True)
    shikiru = shutil.which("shikiru")
    if shikiru is None:
        message = "shikiru executable is required for the live Codex check"
        raise AssertionError(message)
    (hook_bin / "shikiru").symlink_to(shikiru)
    return hook_home


async def _prepare_codex_startup(pane: TmuxPane, hooks_path: Path) -> None:
    await _wait_codex_startup(pane, hooks_path)
    visible = await pane.capture_visible()
    if all(marker in visible for marker in _TRUST_PROMPT_MARKERS):
        await pane.send_keys("Enter")
        await wait_until(
            lambda: _regular_prompt_visible(pane),
            timeout=_LIVE_START_TIMEOUT_SECONDS,
        )
    visible = await pane.capture_visible()
    if all(marker in visible for marker in _UPDATE_PROMPT_MARKERS):
        await pane.send_keys("Down", "Enter")
        await wait_until(
            lambda: _regular_prompt_visible(pane),
            timeout=_LIVE_START_TIMEOUT_SECONDS,
        )
    if await _path_is_file(hooks_path):
        return
    await _trust_codex_hooks(pane)
    await pane.paste_line("prime hooks")
    await wait_until(lambda: _path_is_file(hooks_path), timeout=_LIVE_START_TIMEOUT_SECONDS)


async def _wait_codex_startup(pane: TmuxPane, hooks_path: Path) -> None:
    last_visible = ""
    trust_prompt_observations = 0
    update_prompt_observations = 0

    async def _startup_ready() -> bool:
        nonlocal last_visible, trust_prompt_observations, update_prompt_observations
        visible = await pane.capture_visible()
        last_visible = visible
        if all(marker in visible for marker in _TRUST_PROMPT_MARKERS):
            trust_prompt_observations += 1
        else:
            trust_prompt_observations = 0
        if all(marker in visible for marker in _UPDATE_PROMPT_MARKERS):
            update_prompt_observations += 1
        else:
            update_prompt_observations = 0
        return (
            await _path_is_file(hooks_path)
            or "Ask Codex to do anything" in visible
            or trust_prompt_observations >= _PROMPT_STABILITY_COUNT
            or update_prompt_observations >= _PROMPT_STABILITY_COUNT
        )

    try:
        await wait_until(_startup_ready, timeout=_LIVE_START_TIMEOUT_SECONDS)
    except WaitTimeoutError as error:
        message = f"Codex startup did not emit hooks; pane={last_visible!r}"
        raise AssertionError(message) from error


async def _trust_codex_hooks(pane: TmuxPane) -> None:
    await pane.send_keys("C-u")
    await pane.send_keys("/", "h", "o", "o", "k", "s")
    await wait_until(
        lambda: _visible_contains(pane, _HOOKS_COMPLETION_MARKER),
        timeout=_LIVE_START_TIMEOUT_SECONDS,
    )
    await pane.send_keys("Enter")
    await wait_until(
        lambda: _visible_lacks(pane, _HOOKS_COMPLETION_MARKER),
        timeout=_LIVE_START_TIMEOUT_SECONDS,
    )
    await pane.send_keys("Enter")
    try:
        await wait_until(lambda: _hooks_overview_visible(pane), timeout=_LIVE_START_TIMEOUT_SECONDS)
    except WaitTimeoutError as error:
        visible = await pane.capture_visible()
        message = f"Codex hooks review did not open; pane={visible!r}"
        raise AssertionError(message) from error
    visible = await pane.capture_visible()
    if "Press t to trust all" in visible:
        await pane.send_keys("t")
    elif _HOOK_DETAIL_MARKER not in visible:
        await pane.send_keys("Enter")
        await wait_until(
            lambda: _visible_contains(pane, _HOOK_DETAIL_MARKER),
            timeout=_LIVE_START_TIMEOUT_SECONDS,
        )
    if _HOOK_DETAIL_MARKER in visible or _HOOK_DETAIL_MARKER in await pane.capture_visible():
        await pane.send_keys("t")
        await wait_until(
            lambda: _visible_contains(pane, _HOOK_TRUSTED_MARKER),
            timeout=_LIVE_START_TIMEOUT_SECONDS,
        )
    for _ in range(_HOOK_CLOSE_ATTEMPTS):
        await pane.send_keys("Escape")
        try:
            await wait_until(
                lambda: _regular_prompt_visible(pane),
                timeout=_HOOK_CLOSE_TIMEOUT_SECONDS,
            )
        except WaitTimeoutError:
            continue
        break
    await pane.send_keys("Escape")


async def _next_signal_for_test[S: AgentSignal](
    signals: AsyncIterator[AgentSignal], kind: type[S], *, timeout: float = 15.0
) -> S:
    while True:
        signal = await asyncio.wait_for(signals.__anext__(), timeout=timeout)
        if isinstance(signal, kind):
            return signal


async def _pane_dead(pane: TmuxPane) -> bool:
    return not await pane.is_alive()


async def _hooks_overview_visible(pane: TmuxPane) -> bool:
    visible = await pane.capture_visible()
    return (
        any(marker in visible for marker in _HOOKS_OVERVIEW_MARKERS)
        or _HOOK_DETAIL_MARKER in visible
    )


async def _regular_prompt_visible(pane: TmuxPane) -> bool:
    visible = await pane.capture_visible()
    return "Ask Codex to do anything" in visible


async def _resumed_prompt_visible(pane: TmuxPane) -> bool:
    visible = await pane.capture_visible()
    return _MODEL in visible and _PROMPT_MARKER in visible


async def _path_is_file(path: Path) -> bool:
    return await asyncio.to_thread(path.is_file)


async def _visible_contains(pane: TmuxPane, marker: str) -> bool:
    return marker in await pane.capture_visible()


async def _visible_lacks(pane: TmuxPane, marker: str) -> bool:
    return marker not in await pane.capture_visible()
