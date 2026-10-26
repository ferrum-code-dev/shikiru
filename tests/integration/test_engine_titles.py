"""`TaskTitles`: pane/window titles and window state, read back from `tmux_server`."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.loader import validate_project_config_text
from shikiru.config.settings import (
    AgentConfig,
    TitlesConfig,
)
from shikiru.domain.runs import context_title
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.engine.titles import TaskTitles
from shikiru.process import ProcessResult, SubprocessRunner
from shikiru.tmux.client import Tmux
from shikiru.tmux.decorator import (
    PANE_COLOR_OPTION,
    WINDOW_STATE_COLOR_OPTION,
    WINDOW_STATE_GLYPH_OPTION,
    PaneDecorator,
)
from shikiru.ui.glyphs import (
    GLYPH_ESCALATED,
    GLYPH_OPERATOR,
    GLYPH_PAUSED,
    ROLE_GLYPHS,
    STAGE_MERGE,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    HOLD_COLOR,
    OPERATOR_COLOR,
    ROLE_COLORS,
)
from tests.support.engine import FAKE_RUNG, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0
_SELECTED_RUNG_MODEL = "fake-2"
_RUNG_CHANGE_TITLE = "{glyph} Implementer - {agent}:{model}{pending_rung}"

_LEADER_TITLE = (
    "{glyph} Leader - {agent}:{model} - iter {iteration} - ctx {context} - {cost} {flags}"
)

_TITLES = TitlesConfig(  # noqa: S604 -- `shell=` is `TitlesConfig`'s field name, not a subprocess flag
    window="{task_id} {title:.24}",
    leader=_LEADER_TITLE,
    implementer="{glyph} Implementer - {agent}:{model} - iter {iteration} - ctx {context} {flags}",
    system="Shikiru",
    shell="Shell",
    context_warn_pct=40,
    context_alert_pct=60,
)

_PACKAGED_TITLE_SETTINGS = """
[project]
name = "title-defaults"

[agents.fake]

[roles.leader]
ladder = [{ agent = "fake", model = "model", effort = "high" }]

[roles.implementer]
ladder = [{ agent = "fake", model = "model", effort = "high" }]

[roles.helper]
ladder = [{ agent = "fake", model = "model", effort = "high" }]
"""


class _TitleWriteBarrier(SubprocessRunner):
    def __init__(self, delegate: SubprocessRunner, pane_id: str) -> None:
        self._delegate = delegate
        self._pane_id = pane_id
        self.title_written = asyncio.Event()
        self.overlapping_publication_io = asyncio.Event()
        self.release = asyncio.Event()
        self._armed = True
        self._holding_title_write = False
        self.written_title: str | None = None

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        check: bool = False,
        stop_grace: float | None = None,
    ) -> ProcessResult:
        command = tuple(argv)
        if self._holding_title_write:
            self.overlapping_publication_io.set()
        if self._armed and "select-pane" in command and "-T" in command:
            target = command[command.index("-t") + 1]
            title = command[command.index("-T") + 1]
            if target == self._pane_id:
                self._armed = False
                self.written_title = title
                result = await self._delegate.run(
                    command,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                    input_text=input_text,
                    check=check,
                    stop_grace=stop_grace,
                )
                self.title_written.set()
                self._holding_title_write = True
                await self.release.wait()
                self._holding_title_write = False
                return result
        return await self._delegate.run(
            command,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input_text=input_text,
            check=check,
            stop_grace=stop_grace,
        )


def _titled_settings(*, max_iterations: int = 5) -> Settings:
    settings = flow_settings(max_iterations=max_iterations)
    return settings.model_copy(
        update={
            "dashboard": settings.dashboard.model_copy(update={"widgets": [], "titles": _TITLES})
        }
    )


async def test_stale_title_publication_keeps_the_rung_saved_in_sqlite(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    task = await engine_env.build_task()
    stale_snapshot = engine_env.tasks.require(task.id)
    engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    selected_rung = Rung(
        agent=FAKE_RUNG.agent,
        model=_SELECTED_RUNG_MODEL,
        effort=FAKE_RUNG.effort,
    )
    transition = dataclasses.replace(
        Transition.stay(
            stale_snapshot,
            step=stale_snapshot.step,
            status=stale_snapshot.status,
        ),
        set_overrides={Role.IMPLEMENTER: selected_rung},
    )
    engine_env.tasks.apply_transition(task.id, transition)
    current_snapshot = engine_env.tasks.require(task.id)
    pane_id = current_snapshot.panes[Role.IMPLEMENTER]
    runner = _TitleWriteBarrier(tmux_server.runner, pane_id)
    tmux = Tmux(runner, socket=tmux_server.socket_name, env=tmux_server.env)
    settings = _titled_settings()
    title_settings = settings.dashboard.titles.model_copy(
        update={"implementer": _RUNG_CHANGE_TITLE}
    )
    settings = settings.model_copy(
        update={"dashboard": settings.dashboard.model_copy(update={"titles": title_settings})}
    )
    titles = TaskTitles(PaneDecorator(tmux), engine_env.runs, engine_env.tasks, settings)
    refreshes: list[asyncio.Task[None]] = []
    stale_refresh_started = asyncio.Event()

    async def publish_stale_snapshot() -> None:
        stale_refresh_started.set()
        await titles.refresh(stale_snapshot)

    try:
        current_refresh = asyncio.create_task(titles.refresh(current_snapshot))
        refreshes.append(current_refresh)
        await asyncio.wait_for(runner.title_written.wait(), timeout=_TIMEOUT_SECONDS)
        assert runner.written_title is not None
        assert _SELECTED_RUNG_MODEL in runner.written_title
        current_title = await engine_env.tmux.display(pane_id, "#{pane_title}")
        assert f"next {selected_rung.agent}:{selected_rung.model}" in current_title

        stale_refresh = asyncio.create_task(publish_stale_snapshot())
        refreshes.append(stale_refresh)
        await asyncio.wait_for(stale_refresh_started.wait(), timeout=_TIMEOUT_SECONDS)
        assert not runner.overlapping_publication_io.is_set()
        runner.release.set()
        await asyncio.gather(*refreshes)
    finally:
        runner.release.set()
        for refresh in refreshes:
            if not refresh.done():
                refresh.cancel()
        await asyncio.gather(*refreshes, return_exceptions=True)

    final_title = await engine_env.tmux.display(pane_id, "#{pane_title}")
    assert f"next {selected_rung.agent}:{selected_rung.model}" in final_title


async def test_a_task_reaching_integrate_shows_the_implementer_stage_on_its_window(
    engine_env: EngineEnv,
) -> None:
    settings = _titled_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    flow_env = build_flow(engine_env, settings)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.step is Step.INTEGRATE
    window_target = final.window_id
    assert window_target is not None
    color = await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_COLOR_OPTION}}}")
    glyph = await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}")
    assert color == ROLE_COLORS[Role.IMPLEMENTER]
    assert glyph == STAGE_MERGE
    window_title = await engine_env.tmux.display(window_target, "#{window_name}")
    assert window_title == f"{task.id} {task.title:.24}"


async def test_an_escalated_task_shows_the_escalated_window_state_and_flag(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _titled_settings(max_iterations=1)
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(tmux_server, Role.LEADER, [{"kind": "changes_requested"}])
    flow_env = build_flow(engine_env, settings)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    assert final.status is Status.ESCALATED
    window_target = final.window_id
    assert window_target is not None
    color = await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_COLOR_OPTION}}}")
    glyph = await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}")
    assert color == ALERT_COLOR
    assert glyph == GLYPH_ESCALATED
    leader_pane = final.panes[Role.LEADER]
    title = await engine_env.tmux.display(leader_pane, "#{pane_title}")
    assert "escalated" in title
    assert ROLE_GLYPHS[Role.LEADER] in title


async def test_hold_and_rung_changes_update_titles_on_the_isolated_tmux_server(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    base_settings = _titled_settings()
    settings = base_settings.model_copy(
        update={
            "agents": {"fake": AgentConfig(), "fake2": AgentConfig()},
            "roles": base_settings.roles.model_copy(
                update={
                    "implementer": base_settings.roles.implementer.model_copy(
                        update={
                            "ladder": [
                                FAKE_RUNG,
                                Rung(agent="fake2", model="fake-2", effort=None),
                            ]
                        }
                    )
                }
            ),
        }
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [{"kind": "changes_requested"}, {"kind": "changes_requested"}, {"kind": "accept"}],
    )
    flow_env = build_flow(engine_env, settings)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    implementer_pane = final.panes[Role.IMPLEMENTER]
    assert "fake2:fake-2" in await engine_env.tmux.display(implementer_pane, "#{pane_title}")

    engine_env.tasks.set_holds(final.id, frozenset({Hold.PAUSED}))
    paused = engine_env.tasks.get(final.id)
    assert paused is not None
    await flow_env.titles.refresh(paused)

    assert "paused" in await engine_env.tmux.display(implementer_pane, "#{pane_title}")
    window_target = paused.window_id
    assert window_target is not None
    assert (
        await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_COLOR_OPTION}}}")
        == HOLD_COLOR
    )
    assert (
        await engine_env.tmux.display(window_target, f"#{{{WINDOW_STATE_GLYPH_OPTION}}}")
        == GLYPH_PAUSED
    )


async def test_a_taken_over_pane_shows_the_operator_with_the_runs_usage(
    engine_env: EngineEnv,
) -> None:
    settings = _titled_settings()
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    flow_env = build_flow(engine_env, settings)
    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)
    final = engine_env.tasks.get(task.id)
    assert final is not None
    run = engine_env.runs.get_live(final.id, Role.IMPLEMENTER)
    assert run is not None

    engine_env.runs.mark_taken_over(run.id)
    await flow_env.titles.refresh(final)

    pane = final.panes[Role.IMPLEMENTER]
    title = await engine_env.tmux.display(pane, "#{pane_title}")
    assert title.startswith(GLYPH_OPERATOR)
    assert f"ctx {context_title(run.usage)}" in title
    assert await engine_env.tmux.display(pane, f"#{{{PANE_COLOR_OPTION}}}") == OPERATOR_COLOR


async def test_packaged_agent_titles_keep_role_and_ownership_words_without_glyph_prefixes(
    engine_env: EngineEnv,
) -> None:
    settings = _titled_settings()
    packaged_titles = validate_project_config_text(_PACKAGED_TITLE_SETTINGS).dashboard.titles
    settings = settings.model_copy(
        update={"dashboard": settings.dashboard.model_copy(update={"titles": packaged_titles})}
    )
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    flow_env = build_flow(engine_env, settings)

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    implementer_pane = final.panes[Role.IMPLEMENTER]
    run = engine_env.runs.get_live(final.id, Role.IMPLEMENTER)
    assert run is not None
    engine_env.runs.mark_taken_over(run.id)
    engine_env.tasks.set_holds(final.id, frozenset({Hold.PAUSED}))
    paused = engine_env.tasks.get(final.id)
    assert paused is not None
    await flow_env.titles.refresh(paused)

    title = await engine_env.tmux.display(implementer_pane, "#{pane_title}")
    assert title.startswith("Implementer ·")
    assert f"{FAKE_RUNG.agent}:{FAKE_RUNG.model}/" in title
    assert "iter 1" in title
    assert f"ctx {context_title(run.usage)}" in title
    assert "operator" in title
    assert "paused" in title
    assert GLYPH_OPERATOR not in title
    assert GLYPH_PAUSED not in title
