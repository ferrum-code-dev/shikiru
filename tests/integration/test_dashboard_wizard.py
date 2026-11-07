from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent
from textual.widgets import Button, Input, Select, Static

import shikiru.agents.registry as registry_module
import shikiru.dashboard.wizard as wizard_module
from shikiru.config.loader import load_default_orchestration, load_settings
from shikiru.dashboard.ladder_editor import LadderEditor
from shikiru.domain.tasks import Role
from shikiru.process import SubprocessRunner
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

    from shikiru.operator.detectors import InitWizardResult


from tests.integration.dashboard_wizard_support import (
    BASE_RUNG_TEXT,
    COMPACT_WIZARD_SIZE,
    LADDER_SCOPES,
    LONG_CACHE_CANDIDATE,
    LONG_TRACKER_CANDIDATE,
    TRANSIENT_SMALL_SIZE,
    WIZARD_SIZE,
    advance_orchestration_to_finish,
    advance_to_orchestration,
    app_with_agents,
    assert_written_ladders,
    assert_written_project_fields,
    choose,
    click_back,
    click_button,
    click_next,
    configure_complexity_ladders,
    configure_helper_rung,
    configure_implementer_ladder,
    configure_leader_ladder,
    doctor_text,
    git_project,
    input_value,
    no_entry_points,
    private_doctor_runtime,
    replace_input,
    resize,
    set_base_ladders,
    verify_back_navigation_and_fill_project_fields,
    wait_for_orchestration_error,
)

pytestmark = pytest.mark.asyncio


async def test_init_writes_mixed_multirung_ladders_and_runtime_resolves_complexity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(
        tmp_path,
        {"fake": create_fake_agent, "fake-copy": create_fake_agent},
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        scopes = app.query_one("#ladder-scopes", wizard_module.RowList)
        editor_controls = (
            app.query_one("#ladder-input", Input),
            cast("Select[str]", app.query_one("#ladder-agent", Select)),
            cast("Select[str]", app.query_one("#ladder-model", Select)),
            cast("Select[str]", app.query_one("#ladder-effort", Select)),
            app.query_one("#ladder-add", Button),
        )
        await wait_until(
            lambda: (
                len(scopes.query(".row-list-row")) == len(LADDER_SCOPES)
                and body.max_scroll_y == 0
                and all(row.is_on_screen for row in scopes.query(".row-list-row"))
                and all(control.is_on_screen for control in editor_controls)
            ),
            timeout=UI_WAIT_SECONDS,
        )
        await configure_leader_ladder(pilot)
        await configure_implementer_ladder(pilot)
        await configure_complexity_ladders(pilot)
        await configure_helper_rung(pilot)
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
        await click_button(pilot, "#wizard-finish")

    assert_written_ladders(tmp_path, cast("InitWizardResult", app.return_value))


async def test_init_writes_project_fields_and_orchestration_after_back_navigation_and_resizes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    (tmp_path / LONG_TRACKER_CANDIDATE).write_text("long tracker\n")
    (tmp_path / LONG_CACHE_CANDIDATE).mkdir(parents=True)
    (tmp_path / LONG_CACHE_CANDIDATE / "cache.dat").write_text("long cache\n")
    cache_parent = (tmp_path / LONG_CACHE_CANDIDATE).parent
    (cache_parent / "tracked.txt").write_text("tracked parent\n")
    runner = SubprocessRunner()
    await runner.run(
        ["git", "add", LONG_TRACKER_CANDIDATE, cache_parent.relative_to(tmp_path).as_posix()],
        timeout=10,
        cwd=tmp_path,
        check=True,
    )
    await runner.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "add wizard candidates",
        ],
        timeout=10,
        cwd=tmp_path,
        check=True,
    )
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(
        tmp_path,
        {"fake": create_fake_agent, "fake-copy": create_fake_agent},
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible and frame.region.width > 0, timeout=UI_WAIT_SECONDS)
        preferred_frame_size = (frame.region.width, frame.region.height)
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        await verify_back_navigation_and_fill_project_fields(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "max-parallel")
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await replace_input(pilot, "#max-parallel", "3")
        await advance_orchestration_to_finish(pilot)
        assert (frame.region.width, frame.region.height) == preferred_frame_size
        await click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: bool(doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        await resize(pilot, COMPACT_WIZARD_SIZE)
        await wait_until(lambda: body.max_scroll_y > 0, timeout=UI_WAIT_SECONDS)
        await pilot.press("pagedown")
        await wait_until(lambda: body.scroll_y > 0, timeout=UI_WAIT_SECONDS)
        await pilot.press("pageup")
        await wait_until(lambda: body.scroll_y == 0, timeout=UI_WAIT_SECONDS)
        await resize(pilot, TRANSIENT_SMALL_SIZE)
        await resize(pilot, WIZARD_SIZE)
        await wait_until(
            lambda: (frame.region.width, frame.region.height) == preferred_frame_size,
            timeout=UI_WAIT_SECONDS,
        )
        await pilot.click("#wizard-finish")

    assert_written_project_fields(tmp_path, cast("InitWizardResult", app.return_value))


async def test_orchestration_fields_start_from_injected_defaults_and_keep_edits(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    packaged = load_default_orchestration()
    defaults = packaged.model_copy(
        update={
            "max_parallel": 4,
            "max_iterations": 6,
            "push_interval_seconds": 7200,
            "timeouts": packaged.timeouts.model_copy(
                update={"max_quota_wait": 1200, "stall": 900, "tool": 3600}
            ),
            "resources": packaged.resources.model_copy(
                update={"max_cpu_pct": 70, "min_free_ram_gb": 6.0}
            ),
        }
    )
    app = await app_with_agents(
        tmp_path,
        {"fake": create_fake_agent},
        orchestration_defaults=defaults,
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_orchestration(pilot)
        assert input_value(pilot, "#max-parallel") == "4"
        assert input_value(pilot, "#max-iterations") == "6"
        assert input_value(pilot, "#push-interval") == "7200"
        assert input_value(pilot, "#max-quota-wait") == "1200"
        assert input_value(pilot, "#stall-timeout") == "900"
        assert input_value(pilot, "#tool-timeout") == "3600"
        assert input_value(pilot, "#max-cpu") == "70"
        assert input_value(pilot, "#min-free-ram") == "6.0"
        await replace_input(pilot, "#max-parallel", "3")
        await replace_input(pilot, "#tool-timeout", "5400")
        await replace_input(pilot, "#max-cpu", "90")
        await click_back(pilot, "#wizard-page-3")
        await click_next(pilot, "#wizard-page-4")
        assert input_value(pilot, "#max-parallel") == "3"
        assert input_value(pilot, "#tool-timeout") == "5400"
        assert input_value(pilot, "#max-cpu") == "90"
        await click_next(pilot, "#wizard-page-5")
        await click_next(pilot, "#wizard-page-6")
        await choose(pilot, "#integration", "pr")
        await click_next(pilot, "#wizard-page-7")
        summary = str(pilot.app.query_one("#wizard-summary .wizard-detail-value", Static).render())
        assert summary == (
            "Max parallel 3 tasks · Max iterations 6 passes · Push interval 7200 seconds · "
            "Max quota wait 1200 seconds · Stall timeout 900 seconds · Tool timeout 5400 seconds · "
            "Max CPU 90 percent · Min free RAM 6.0 GB · Pull request"
        )


async def test_invalid_orchestration_values_focus_their_field_and_boundaries_round_trip(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})
    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_orchestration(pilot)
        await replace_input(pilot, "#max-cpu", "101")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "max-cpu")
        assert not config_path.exists()

        await replace_input(pilot, "#max-cpu", "100")
        await replace_input(pilot, "#max-parallel", "0")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "max-parallel")
        assert not config_path.exists()

        await replace_input(pilot, "#max-parallel", "1")
        await replace_input(pilot, "#stall-timeout", "0")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "stall-timeout")
        assert not config_path.exists()

        await replace_input(pilot, "#stall-timeout", "600")
        await replace_input(pilot, "#tool-timeout", "0")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "tool-timeout")
        assert not config_path.exists()

        await replace_input(pilot, "#tool-timeout", "1800")
        await replace_input(pilot, "#max-iterations", "1")
        await replace_input(pilot, "#max-quota-wait", "0")
        await replace_input(pilot, "#min-free-ram", "0")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: bool(doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        await pilot.click("#wizard-finish")

    orchestration = load_settings(tmp_path).orchestration
    assert (orchestration.max_parallel, orchestration.max_iterations) == (1, 1)
    assert (orchestration.timeouts.stall, orchestration.timeouts.tool) == (600, 1800)
    assert orchestration.timeouts.max_quota_wait == 0
    assert orchestration.resources.max_cpu_pct == 100
    assert orchestration.resources.min_free_ram_gb == 0


async def test_invalid_push_interval_focuses_its_field_and_zero_is_saved(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})
    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_orchestration(pilot)
        await replace_input(pilot, "#push-interval", "-5")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_for_orchestration_error(pilot, "push-interval")
        assert not config_path.exists()

        await replace_input(pilot, "#push-interval", "0")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: bool(doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        await pilot.click("#wizard-finish")

    assert load_settings(tmp_path).orchestration.push_interval_seconds == 0


async def test_ladder_text_typed_before_its_change_event_survives_a_scope_switch(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        editor = app.query_one("#ladder-editor", LadderEditor)
        field = app.query_one("#ladder-input", Input)
        field.value = BASE_RUNG_TEXT
        editor.set_scope(Role.IMPLEMENTER)
        editor.set_scope(Role.LEADER)
        await pilot.pause()

        assert input_value(pilot, "#ladder-input") == BASE_RUNG_TEXT
