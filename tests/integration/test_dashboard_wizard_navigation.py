from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent
from textual.widgets import Button, Input, Select, Static

import shikiru.agents.registry as registry_module
import shikiru.dashboard.wizard as wizard_module
from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.domain.ladder import pick_rung
from shikiru.domain.tasks import Role
from tests.support.dashboard import focus
from tests.support.factories import make_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


from tests.integration.dashboard_wizard_support import (
    COMPACT_WIZARD_SIZE,
    LADDER_SCOPES,
    TRANSIENT_SMALL_SIZE,
    WIZARD_SIZE,
    WIZARD_TIMER_TEST_SECONDS,
    advance_orchestration_to_finish,
    app_with_agents,
    choose,
    click_button,
    click_next,
    create_catalog_without_headless_agent,
    create_legacy_agent,
    doctor_text,
    git_project,
    no_entry_points,
    private_doctor_runtime,
    replace_input,
    resize,
    set_base_ladders,
    set_role,
    set_scope,
    set_scope_text,
)

pytestmark = pytest.mark.asyncio


async def test_transient_small_resize_preserves_drafts_and_recovers_navigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await click_next(pilot, "#wizard-page-2")
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        await resize(pilot, TRANSIENT_SMALL_SIZE)
        await wait_until(
            lambda: (
                app.is_running
                and app.query_one("#wizard-page-2").display
                and tracker_field.value == "draft.md"
            ),
            timeout=UI_WAIT_SECONDS,
        )
        assert not config_path.exists()
        await resize(pilot, COMPACT_WIZARD_SIZE)
        await wait_until(
            lambda: app.query_one("#wizard-page-2").display and tracker_field.value == "draft.md",
            timeout=UI_WAIT_SECONDS,
        )
        await click_next(pilot, "#wizard-page-3")
        assert not config_path.exists()
        await click_button(pilot, "#wizard-cancel")

    assert app.return_value is None
    assert not config_path.exists()


async def test_art_reveal_preserves_wizard_state_across_resize(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await click_next(pilot, "#wizard-page-2")
        await resize(pilot, (40, 12))
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        candidate_list = app.query_one(f"#{wizard_module.TRACKERS_LIST_ID}")
        await focus(pilot, candidate_list)
        await pilot.press("enter")
        await wait_until(
            lambda: tracker_field.value == "draft.md, tasks/plan.md", timeout=UI_WAIT_SECONDS
        )
        await wait_until(lambda: app.focused is tracker_field, timeout=UI_WAIT_SECONDS)
        await focus(pilot, candidate_list)
        body.scroll_home(animate=False)
        await wait_until(lambda: body.scroll_y == 0, timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: body.max_scroll_y > 0, timeout=UI_WAIT_SECONDS)
        body.scroll_to(y=body.max_scroll_y, animate=False, immediate=True)
        await wait_until(lambda: body.scroll_y == body.max_scroll_y, timeout=UI_WAIT_SECONDS)
        saved_scroll_y = body.scroll_y
        await pilot.press("ctrl+f")
        await wait_until(lambda: not frame.visible, timeout=UI_WAIT_SECONDS)
        await resize(pilot, (80, 12))
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: app.focused is candidate_list, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: body.scroll_y == min(saved_scroll_y, body.max_scroll_y),
            timeout=UI_WAIT_SECONDS,
        )
        await wait_until(
            lambda: tracker_field.value == "draft.md, tasks/plan.md",
            timeout=UI_WAIT_SECONDS,
        )
        await wait_until(lambda: app.query_one("#wizard-page-2").display, timeout=UI_WAIT_SECONDS)

        await resize(pilot, (80, 24))
        await click_button(pilot, "#wizard-cancel")


async def test_hidden_wizard_frame_ignores_navigation_keys(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        repository_page = app.query_one("#wizard-page-0")
        next_button = app.query_one("#wizard-next", Button)
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        await focus(pilot, next_button)
        await pilot.press("ctrl+f")
        await wait_until(lambda: not frame.visible, timeout=UI_WAIT_SECONDS)
        await pilot.press("enter", "tab")
        await wait_until(
            lambda: not frame.visible and repository_page.display, timeout=UI_WAIT_SECONDS
        )
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: app.focused is next_button, timeout=UI_WAIT_SECONDS)
        await click_button(pilot, "#wizard-cancel")


async def test_art_reveal_does_not_interrupt_editors_or_expanded_selects(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await click_next(pilot, "#wizard-page-2")
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        await pilot.press("ctrl+f")
        await wait_until(
            lambda: frame.visible and tracker_field.value == "draft.md", timeout=UI_WAIT_SECONDS
        )
        await click_next(pilot, "#wizard-page-3")
        await click_next(pilot, "#wizard-page-4")
        await click_next(pilot, "#wizard-page-5")
        await click_next(pilot, "#wizard-page-6")
        integration = cast(
            "Select[str]", app.query_one(f"#{wizard_module.INTEGRATION_SELECT_ID}", Select)
        )
        await focus(pilot, integration)
        await pilot.press("enter")
        await wait_until(lambda: integration.expanded, timeout=UI_WAIT_SECONDS)
        await pilot.press("ctrl+f")
        await wait_until(lambda: frame.visible and integration.expanded, timeout=UI_WAIT_SECONDS)
        await pilot.press("escape")
        await wait_until(
            lambda: app.is_running and not integration.expanded, timeout=UI_WAIT_SECONDS
        )
        await click_button(pilot, "#wizard-cancel")


async def test_hidden_incomplete_complexity_draft_blocks_write_with_its_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await set_scope_text(pilot, LADDER_SCOPES[3], "   ")
        await set_scope_text(pilot, LADDER_SCOPES[4], "broken")
        await set_role(pilot, "leader")
        await click_next(pilot)
        error = app.query_one("#ladder-problem", Static)
        await wait_until(lambda: bool(str(error.render()).strip()), timeout=UI_WAIT_SECONDS)
        error_text = str(error.render())
        assert "Implementer" in error_text
        assert "High" in error_text
        await wait_until(
            lambda: (
                app.query_one("#ladder-scopes", wizard_module.RowList).selected
                == LADDER_SCOPES.index("Implementer · High")
                and app.focused is app.query_one("#ladder-input", Input)
            ),
            timeout=UI_WAIT_SECONDS,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()


async def test_corrected_complexity_draft_is_written_and_resolved(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await set_scope_text(pilot, LADDER_SCOPES[4], "fake:default:high")
        await click_next(pilot, "#wizard-page-2")
        await click_next(pilot, "#wizard-page-3")
        await click_next(pilot, "#wizard-page-4")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: bool(doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        result_button = app.query_one("#wizard-finish", Button)
        await pilot.press("escape", "ctrl+q")
        await wait_until(
            lambda: app.is_running and result_button.label == "Exit", timeout=UI_WAIT_SECONDS
        )
        await click_button(pilot, "#wizard-finish")

    settings = load_settings(tmp_path)
    assert settings.roles.implementer.complexity.keys() == {"high"}
    assert settings.roles.implementer.complexity["high"].ladder == [
        Rung(agent="fake", model="default", effort="high"),
    ]
    assert pick_rung(make_task(complexity="normal"), Role.IMPLEMENTER, settings) == Rung(
        agent="fake", model="smoke", effort="low"
    )
    assert pick_rung(make_task(complexity="high"), Role.IMPLEMENTER, settings) == Rung(
        agent="fake", model="default", effort="high"
    )


async def test_unavailable_model_catalog_is_explained_and_prevents_config_write(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"legacy": create_legacy_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        guidance = app.query_one("#ladder-guidance", Static)
        await wait_until(
            lambda: (
                "legacy" in str(guidance.render())
                and "authoritative model catalog" in str(guidance.render())
            ),
            timeout=UI_WAIT_SECONDS,
        )
        await click_next(pilot)
        await wait_until(
            lambda: bool(str(app.query_one("#ladder-problem", Static).render()).strip()),
            timeout=UI_WAIT_SECONDS,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await pilot.press("escape")
        assert app.is_running
        await click_button(pilot, "#wizard-cancel")


async def test_helper_requires_headless_agent_capability(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"legacy": create_catalog_without_headless_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_scope_text(pilot, LADDER_SCOPES[0], "legacy:listed-model")
        await set_scope_text(pilot, LADDER_SCOPES[1], "legacy:listed-model")
        await set_scope_text(pilot, LADDER_SCOPES[-1], "legacy:listed-model")
        await set_scope(pilot, LADDER_SCOPES[0])
        await choose(pilot, "#ladder-agent", "legacy")
        await choose(pilot, "#ladder-model", "listed-model")
        await click_next(pilot)
        error = app.query_one("#ladder-problem", Static)
        await wait_until(lambda: "headless support" in str(error.render()), timeout=UI_WAIT_SECONDS)
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await wait_until(
            lambda: (
                app.query_one("#ladder-scopes", wizard_module.RowList).selected
                == LADDER_SCOPES.index("Helper")
                and pilot.app.focused is app.query_one("#ladder-input", Input)
            ),
            timeout=UI_WAIT_SECONDS,
        )
        assert app.query_one("#ladder-agent", Select).disabled
        await click_button(pilot, "#wizard-cancel")


async def test_ladder_draft_and_caret_survive_scope_changes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_scope_text(pilot, LADDER_SCOPES[0], "fake:smoke,,unfinished")
        field = app.query_one("#ladder-input", Input)
        cursor_position = len(field.value) // 2
        field.cursor_position = cursor_position
        await choose(pilot, "#ladder-agent", "fake")
        await choose(pilot, "#ladder-model", "smoke")
        await click_button(pilot, "#ladder-add")
        await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)
        assert field.value == "fake:smoke,,unfinished"
        await wait_until(lambda: field.cursor_position == cursor_position, timeout=UI_WAIT_SECONDS)
        await set_scope(pilot, LADDER_SCOPES[2])
        await wait_until(lambda: field.value == "", timeout=UI_WAIT_SECONDS)
        assert field.value == ""
        await set_scope(pilot, LADDER_SCOPES[0])
        await wait_until(lambda: field.value == "fake:smoke,,unfinished", timeout=UI_WAIT_SECONDS)
        await pilot.press("enter")
        await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)
        await wait_until(lambda: field.cursor_position == cursor_position, timeout=UI_WAIT_SECONDS)
        assert field.value == "fake:smoke,,unfinished"
        assert field.cursor_position == cursor_position
        await pilot.press("x")
        await wait_until(
            lambda: field.value == "fake:smoke,x,unfinished",
            timeout=UI_WAIT_SECONDS,
        )
        await click_button(pilot, "#wizard-cancel")


async def test_agent_and_model_changes_clear_dependent_add_choices(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(
        tmp_path,
        {"fake": create_fake_agent, "fake-copy": create_fake_agent},
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_scope(pilot, LADDER_SCOPES[0])
        await choose(pilot, "#ladder-agent", "fake")
        await choose(pilot, "#ladder-model", "default")
        await choose(pilot, "#ladder-effort", "high")
        await choose(pilot, "#ladder-model", "smoke")
        effort = cast("Select[str]", app.query_one("#ladder-effort", Select))
        await wait_until(lambda: effort.value is Select.BLANK, timeout=UI_WAIT_SECONDS)
        await choose(pilot, "#ladder-agent", "fake-copy")
        model = cast("Select[str]", app.query_one("#ladder-model", Select))
        await wait_until(
            lambda: model.value is Select.BLANK and effort.value is Select.BLANK,
            timeout=UI_WAIT_SECONDS,
        )
        await click_button(pilot, "#wizard-cancel")
