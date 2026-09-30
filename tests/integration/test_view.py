"""The `shikiru view` role pane rendering a real turn's view log."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Mode, Role
from shikiru.paths import run_dir, task_root
from shikiru.view_log import view_argv, view_log_path
from tests.support.engine import set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_FLOW_TIMEOUT_SECONDS = 60
_VIEW_TIMEOUT_SECONDS = 20
_MESSAGE_PREFIX_LENGTH = 20
_TURN = "01-implement"
_RUNG = {"agent": "fake", "model": "smoke", "effort": None}


async def test_view_pane_shows_a_turns_messages_and_tool_calls(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    flow_env = build_flow(engine_env, flow_settings())
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_FLOW_TIMEOUT_SECONDS)
    log = view_log_path(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    message = next(row["text"] for row in rows if row["signal"] == "Message")
    tool = next(row["name"] for row in rows if row["signal"] == "ToolStarted")
    pane = await engine_env.new_pane()
    argv = view_argv(engine_env.project_root, task.id, Role.IMPLEMENTER.value)
    await engine_env.tmux.respawn_pane(
        pane, argv, env={}, cwd=task_root(engine_env.project_root, task.id)
    )

    async def rendered() -> bool:
        text = (await tmux_server.run("capture-pane", "-p", "-t", pane)).stdout
        return all(
            part in text for part in (rows[0]["turn"], message[:_MESSAGE_PREFIX_LENGTH], tool)
        )

    await wait_until(rendered, timeout=_VIEW_TIMEOUT_SECONDS)


def _row(signal: str, **fields: object) -> str:
    return json.dumps({"ts": "t", "turn": _TURN, "rung": _RUNG, "signal": signal, **fields})


def _append(log: Path, text: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as handle:
        handle.write(text)


async def test_view_pane_shows_rows_appended_while_the_turn_is_running(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    run = run_dir(engine_env.project_root, "T-1")
    log = view_log_path(run, Role.IMPLEMENTER.value)
    pane = await engine_env.new_pane()
    argv = view_argv(engine_env.project_root, "T-1", Role.IMPLEMENTER.value)
    await engine_env.tmux.respawn_pane(pane, argv, env={}, cwd=run)

    async def shows(*parts: str) -> bool:
        text = (await tmux_server.run("capture-pane", "-p", "-t", pane)).stdout
        return all(part in text for part in parts)

    async def hides(part: str) -> bool:
        return not await shows(part)

    second = _row("Message", text="second message")
    split = len(second) // 2
    _append(log, _row("Message", text="first message") + "\n" + second[:split])
    await wait_until(lambda: shows(_TURN, "first message"), timeout=_VIEW_TIMEOUT_SECONDS)
    assert await hides("second message")

    _append(log, second[split:] + "\n")
    await wait_until(lambda: shows("second message"), timeout=_VIEW_TIMEOUT_SECONDS)

    _append(log, _row("TurnFailed", kind="auth", message="login expired", resets_at=None) + "\n")
    await wait_until(lambda: shows("auth: login expired"), timeout=_VIEW_TIMEOUT_SECONDS)
    assert await hides("turn ended")
