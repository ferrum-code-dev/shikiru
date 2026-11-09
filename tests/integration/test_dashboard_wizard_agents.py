from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent
from textual.widgets import Button, Static

import shikiru.agents.registry as registry_module
import shikiru.dashboard.wizard as wizard_module
from shikiru.bootstrap import build_project_detectors, run_init_wizard
from shikiru.config.loader import load_settings
from shikiru.errors import ConfigError
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

    from shikiru.operator.detectors import InitWizardResult


from tests.integration.dashboard_wizard_support import (
    COMPACT_WIZARD_SIZE,
    PICKY_REJECTED_DEPTH,
    WIZARD_SIZE,
    advance_orchestration_to_finish,
    advance_to_agent_settings,
    app_with_agents,
    app_with_cow_probe_error,
    build_wizard_app,
    choose,
    click_back,
    click_button,
    click_next,
    create_legacy_agent,
    create_picky_agent,
    create_unwelcoming_agent,
    doctor_text,
    finish_from_agent_settings,
    git_project,
    initialize,
    input_value,
    no_entry_points,
    private_doctor_runtime,
    replace_input,
    resize,
    set_base_ladders,
    wait_for_agent_setting_error,
    wait_for_finish_then_exit,
)
from tests.support.waiting import LONG_WAIT_SECONDS

pytestmark = pytest.mark.asyncio


async def test_written_config_remains_truthful_when_doctor_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    tmux = executable_dir / "tmux"
    tmux.write_text("#!/bin/sh\nexit 9\n")
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await click_next(pilot, "#wizard-page-2")
        await click_next(pilot, "#wizard-page-3")
        await click_next(pilot, "#wizard-page-4")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        report = app.query_one("#doctor-report", Static)
        await wait_until(lambda: "exit 9" in doctor_text(report), timeout=LONG_WAIT_SECONDS)
        config_row = app.query_one("#doctor-config", wizard_module.WizardDetailRow)
        config_value = app.query_one("#doctor-config .wizard-detail-value", Static)
        await wait_until(
            lambda: (
                config_row.display
                and config_row.is_on_screen
                and str(config_path.resolve()) in str(config_value.render())
            ),
            timeout=UI_WAIT_SECONDS,
        )
        result_button = app.query_one("#wizard-finish", Button)
        await wait_until(lambda: result_button.label == "Exit", timeout=UI_WAIT_SECONDS)
        await pilot.press("escape")
        await wait_until(lambda: app.is_running and result_button.display, timeout=UI_WAIT_SECONDS)
        await click_button(pilot, "#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert result.config_path == config_path.resolve()
    assert result.doctor_report is not None
    assert not result.doctor_report.ok
    assert any(check.name == "tmux" and not check.ok for check in result.doctor_report.checks)


async def test_copy_on_write_probe_error_survives_navigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_cow_probe_error(tmp_path)

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_base_ladders(pilot)
        await click_next(pilot, "#wizard-page-2")
        await click_next(pilot, "#wizard-page-3")
        cow_result = app.query_one("#cow-result .wizard-detail-value", Static)
        await wait_until(
            lambda: "CoW check failed" in str(cow_result.render()), timeout=UI_WAIT_SECONDS
        )
        await click_next(pilot, "#wizard-page-4")
        await click_back(pilot, "#wizard-page-3")
        assert "shikiru-missing-cp" in str(cow_result.render())


async def test_init_wizard_cancellation_does_not_write_project_state(tmp_path: Path) -> None:
    await git_project(tmp_path)
    app = await build_wizard_app(tmp_path)

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=UI_WAIT_SECONDS)
        screen = app.screen
        for key in ("escape", "ctrl+q", "ctrl+p"):
            await pilot.press(key)
            await wait_until(
                lambda: app.is_running and app.screen is screen and frame.visible,
                timeout=UI_WAIT_SECONDS,
            )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await click_button(pilot, "#wizard-cancel")

    assert app.return_value is None
    assert not (tmp_path / ".shikiru" / "config.toml").exists()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude = exclude_path.read_text() if exclude_path.exists() else ""
    assert "/.shikiru/" not in exclude


@pytest.mark.parametrize("worktree_changed", [False, True])
async def test_repository_checkout_indicator_uses_git_status(
    tmp_path: Path,
    worktree_changed: bool,
) -> None:
    await git_project(tmp_path)
    if worktree_changed:
        (tmp_path / "tasks" / "plan.md").write_text("changed\n")
        (tmp_path / "notes.txt").write_text("untracked\n")
    app = await build_wizard_app(tmp_path)

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        checkout = app.query_one(
            f"#{wizard_module.REPOSITORY_CHECKOUT_ID} .wizard-detail-value", Static
        )
        if worktree_changed:
            await wait_until(
                lambda: all(
                    detail in str(checkout.render())
                    for detail in ("+1", "\N{MINUS SIGN}1", "1 untracked path")
                ),
                timeout=UI_WAIT_SECONDS,
            )
        else:
            await wait_until(lambda: "clean" in str(checkout.render()), timeout=UI_WAIT_SECONDS)
        await click_button(pilot, "#wizard-cancel")


@pytest.mark.parametrize("entrypoint", ["wizard", "yes"])
async def test_init_refuses_nested_repository_config_without_changing_it(
    tmp_path: Path, entrypoint: str
) -> None:
    await git_project(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    config_path = nested / ".shikiru" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("operator-owned config\n")
    detectors = build_project_detectors(nested)

    with pytest.raises(ConfigError):
        await initialize(entrypoint, nested, detectors)

    assert config_path.read_text() == "operator-owned config\n"


async def test_init_creates_config_owner_only_from_the_start(tmp_path: Path) -> None:
    await git_project(tmp_path)
    previous = os.umask(0o022)
    try:
        await build_project_detectors(tmp_path).initialize()
    finally:
        os.umask(previous)

    config_path = tmp_path / ".shikiru" / "config.toml"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700


async def test_init_wizard_refuses_an_existing_config_without_changing_it(
    tmp_path: Path,
) -> None:
    await git_project(tmp_path)
    config_path = tmp_path / ".shikiru" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("operator-owned config\n")

    with pytest.raises(ConfigError):
        await run_init_wizard(tmp_path)

    assert config_path.read_text() == "operator-owned config\n"


async def test_agents_step_follows_orchestration_and_writes_edited_options(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_agent_settings(pilot)
        assert str(app.query_one("#wizard-step", Static).render()) == "6 / 8"
        assert "Agents" in str(app.query_one("#wizard-title", Static).render())
        assert input_value(pilot, "#agent-setting-0-0") == "80"
        await replace_input(pilot, "#agent-setting-0-0", "65")
        await choose(pilot, "#agent-setting-0-1", "subscription")

        await click_next(pilot, "#wizard-page-6")
        await click_back(pilot, "#wizard-page-5")
        assert input_value(pilot, "#agent-setting-0-0") == "65"
        await resize(pilot, COMPACT_WIZARD_SIZE)
        await resize(pilot, WIZARD_SIZE)
        assert input_value(pilot, "#agent-setting-0-0") == "65"

        await finish_from_agent_settings(pilot)
        await wait_for_finish_then_exit(pilot)

    assert load_settings(tmp_path).agents["fake"].options == {
        "compact_at_pct": 65.0,
        "billing_mode": "subscription",
        "auth": "none",
    }


async def test_unset_optional_agent_settings_are_not_written(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_agent_settings(pilot)
        await finish_from_agent_settings(pilot)
        await wait_for_finish_then_exit(pilot)

    assert "billing_mode" not in load_settings(tmp_path).agents["fake"].options


async def test_invalid_agent_setting_focuses_its_field_and_blocks_the_write(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent})
    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_agent_settings(pilot)
        await replace_input(pilot, "#agent-setting-0-0", "150")
        await click_next(pilot)
        message = await wait_for_agent_setting_error(pilot, "agent-setting-0-0")
        assert "Compact at" in message
        assert not config_path.exists()

        await replace_input(pilot, "#agent-setting-0-0", "100")
        await finish_from_agent_settings(pilot)
        await wait_for_finish_then_exit(pilot)

    assert load_settings(tmp_path).agents["fake"].options["compact_at_pct"] == 100.0


async def test_third_party_plugin_settings_work_and_its_rejection_is_reported(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"fake": create_fake_agent, "picky": create_picky_agent})
    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_agent_settings(pilot)
        assert input_value(pilot, "#agent-setting-1-1") == "1"
        await replace_input(pilot, "#agent-setting-1-1", str(PICKY_REJECTED_DEPTH))
        await click_next(pilot)
        message = await wait_for_agent_setting_error(pilot, "agent-setting-1-1")
        assert "picky" in message
        assert "unlucky" in message
        assert not config_path.exists()

        await replace_input(pilot, "#agent-setting-1-1", "7")
        await finish_from_agent_settings(pilot)
        await wait_for_finish_then_exit(pilot)

    assert load_settings(tmp_path).agents["picky"].options == {"first": 1.0, "custom.depth": 7.0}


async def test_agent_wide_plugin_rejection_shows_a_page_error_and_blocks_the_write(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(
        tmp_path, {"fake": create_fake_agent, "picky": create_unwelcoming_agent}
    )
    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await advance_to_agent_settings(pilot)
        await replace_input(pilot, "#agent-setting-1-0", str(PICKY_REJECTED_DEPTH))
        await click_next(pilot)
        error = pilot.app.query_one("#wizard-error", Static)
        await wait_until(lambda: "unsupported" in str(error.render()), timeout=UI_WAIT_SECONDS)
        assert pilot.app.query_one("#wizard-page-5").display
        assert not config_path.exists()

        await replace_input(pilot, "#agent-setting-1-0", "2")
        await finish_from_agent_settings(pilot)
        await wait_for_finish_then_exit(pilot)


async def test_agent_without_settings_capability_is_explained_and_writes_no_options(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)
    app = await app_with_agents(tmp_path, {"legacy": create_legacy_agent})

    async with app.run_test(size=WIZARD_SIZE):
        page = app.query_one("#wizard-page-5")
        texts = " ".join(str(item.render()) for item in page.query(Static))
        assert "does not expose editable settings" in texts
        assert "nothing is written" in texts
