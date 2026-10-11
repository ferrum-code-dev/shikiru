"""`TaskTitles`: pane/window titles and window state, read back from `tmux_server` (06 §5.3)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.loader import validate_project_config_text
from shikiru.config.settings import (
    AgentConfig,
    TitlesConfig,
)
from shikiru.domain.runs import MISSING_TITLE_VALUE, context_title
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step
from shikiru.engine.titles import TaskTitles
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
    AMBER,
    DUSTY_ROSE,
    HOLD_COLOR,
    OPERATOR_COLOR,
    ROLE_COLORS,
    SAGE,
    SECONDARY_TEXT_COLOR,
)
from tests.support.engine import FAKE_RUNG, set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo
from tests.support.tmux import attach_tmux_client, rendered_foreground
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0
_RENDER_TIMEOUT_SECONDS = 20.0

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


def _titled_settings(*, max_iterations: int = 5) -> Settings:
    settings = flow_settings(max_iterations=max_iterations)
    return settings.model_copy(
        update={
            "dashboard": settings.dashboard.model_copy(update={"widgets": [], "titles": _TITLES})
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


_CONTEXT_COLOUR_CASES = (
    pytest.param(39, SAGE, id="below-warn"),
    pytest.param(40, AMBER, id="warn-threshold"),
    pytest.param(59, AMBER, id="below-alert"),
    pytest.param(60, DUSTY_ROSE, id="alert-threshold"),
)


async def _attached_foreground(
    engine_env: EngineEnv, tmux_server: TmuxServer, pane_id: str, text: str
) -> str | None:
    await engine_env.tmux.select_window(await engine_env.tmux.window_id(pane_id))
    async with attach_tmux_client(tmux_server, tmux_server.session) as client:
        await wait_until(
            lambda: text.encode() in client.process.output, timeout=_RENDER_TIMEOUT_SECONDS
        )
        return rendered_foreground(client.process.output, text)


@pytest.mark.parametrize(("context_used_pct", "colour"), _CONTEXT_COLOUR_CASES)
async def test_the_rendered_context_cell_takes_the_colour_of_its_usage_level(
    engine_env: EngineEnv, tmux_server: TmuxServer, context_used_pct: int, colour: str
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_flow_task(engine_env, mode=Mode.IMPLEMENT)
    await set_role_script(
        tmux_server,
        Role.IMPLEMENTER,
        [{"kind": "accept", "context_used_pct": context_used_pct}],
    )
    flow_env = build_flow(engine_env, _titled_settings())

    await asyncio.wait_for(flow_env.flow.run(task.id), timeout=_TIMEOUT_SECONDS)

    final = engine_env.tasks.get(task.id)
    assert final is not None
    rendered = await _attached_foreground(
        engine_env,
        tmux_server,
        final.panes[Role.IMPLEMENTER],
        f"{context_used_pct}%",
    )
    assert rendered == colour


async def test_unreported_context_renders_as_a_grey_dash(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    task = await engine_env.build_task()
    engine_env.runs.start(task.id, Role.IMPLEMENTER, FAKE_RUNG)
    titles = TaskTitles(PaneDecorator(engine_env.tmux), engine_env.runs, _titled_settings())
    await titles.refresh(task)

    rendered = await _attached_foreground(
        engine_env, tmux_server, task.panes[Role.IMPLEMENTER], MISSING_TITLE_VALUE
    )

    assert rendered == SECONDARY_TEXT_COLOR
