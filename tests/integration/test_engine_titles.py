"""`TaskTitles`: pane/window titles and window state, read back from `tmux_server` (06 §5.3)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.settings import (
    AgentConfig,
    DashboardConfig,
    LayoutSplit,
    PaneLeaf,
    TitlesConfig,
)
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.tmux.decorator import WINDOW_STATE_COLOR_OPTION, WINDOW_STATE_GLYPH_OPTION
from shikiru.ui.glyphs import GLYPH_ESCALATED, GLYPH_PAUSED, ROLE_GLYPHS, STAGE_MERGE
from shikiru.ui.tokens import ALERT_COLOR, HOLD_COLOR, ROLE_COLORS
from tests.support.engine import FAKE_RUNG, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0

_LEADER_TITLE = (
    "{glyph} Leader - {agent}:{model} - iter {iteration} - ctx {context} - {cost} {flags}"
)

_TITLES = TitlesConfig(  # noqa: S604 -- `shell=` is `TitlesConfig`'s field name, not a subprocess flag
    window="{task_id} {title:.24}",
    leader=_LEADER_TITLE,
    implementer="{glyph} Implementer - {agent}:{model} - iter {iteration} - {flags}",
    system="Shikiru",
    shell="Shell",
)


def _titled_settings(*, max_iterations: int = 5) -> Settings:
    settings = flow_settings(max_iterations=max_iterations)
    return settings.model_copy(
        update={
            "dashboard": DashboardConfig(
                animations=False,
                widgets=[],
                layout=LayoutSplit(split="h", panes=[PaneLeaf(pane="system")]),
                titles=_TITLES,
            )
        }
    )


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
    color = await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_COLOR_OPTION)
    glyph = await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_GLYPH_OPTION)
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
    color = await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_COLOR_OPTION)
    glyph = await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_GLYPH_OPTION)
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
        await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_COLOR_OPTION)
        == HOLD_COLOR
    )
    assert (
        await engine_env.tmux.show_window_option(window_target, WINDOW_STATE_GLYPH_OPTION)
        == GLYPH_PAUSED
    )
