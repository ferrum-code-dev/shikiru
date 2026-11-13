"""Muse in the init wizard: selectable ladders, the runtime default and the slow catalog error."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Static

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import RUNTIME_DEFAULT_MODEL, Rung
from shikiru.config.loader import load_settings
from shikiru.plugins.agents.muse.agent import create as create_muse
from tests.integration.dashboard_wizard_support import (
    LADDER_SCOPES,
    WIZARD_SIZE,
    advance_orchestration_to_finish,
    app_with_agents,
    click_button,
    click_next,
    create_catalog_without_headless_agent,
    doctor_text,
    git_project,
    no_entry_points,
    private_doctor_runtime,
    set_scope_text,
    wait_for_finish_then_exit,
)
from tests.integration.fake_muse import install_fake_muse
from tests.support.waiting import LONG_WAIT_SECONDS, UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

    from shikiru.operator.detectors import InitWizardResult

pytestmark = pytest.mark.asyncio

_MUSE = "muse"
_LEGACY = "legacy"
_MUSE_DEFAULT_TEXT = f"{_MUSE}:{RUNTIME_DEFAULT_MODEL}"
_LEGACY_DEFAULT_TEXT = f"{_LEGACY}:{RUNTIME_DEFAULT_MODEL}"
_CONFIG_DIRECTORY = ".shikiru"
_CONFIG_FILENAME = "config.toml"
_CATALOG_ERROR_PREFIX = "Model discovery unavailable for muse"


@pytest.fixture(autouse=True)
def _only_registered_agents(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "entry_points", no_entry_points)


async def test_muse_is_selectable_for_every_ladder_and_the_written_config_uses_it(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    install_fake_muse(tmp_path, monkeypatch)
    app = await app_with_agents(tmp_path, {_MUSE: create_muse})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        for scope in LADDER_SCOPES:
            await set_scope_text(pilot, scope, _MUSE_DEFAULT_TEXT)
        await click_next(pilot, "#wizard-page-2")
        await click_next(pilot, "#wizard-page-3")
        await click_next(pilot, "#wizard-page-4")
        await advance_orchestration_to_finish(pilot)
        await click_button(pilot, "#wizard-finish")
        config_path = tmp_path / _CONFIG_DIRECTORY / _CONFIG_FILENAME
        await wait_until(config_path.is_file, timeout=UI_WAIT_SECONDS)
        await wait_until(
            lambda: bool(doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=LONG_WAIT_SECONDS,
        )
        await wait_for_finish_then_exit(pilot)

    result = cast("InitWizardResult", app.return_value)
    settings = load_settings(tmp_path)
    muse_rung = Rung(agent=_MUSE, model=RUNTIME_DEFAULT_MODEL)
    assert settings.roles.leader.ladder == [muse_rung]
    assert settings.roles.helper.ladder == [muse_rung]
    assert settings.roles.implementer.ladder == [muse_rung]
    assert result.doctor_report is not None
    muse_checks = [check for check in result.doctor_report.checks if _MUSE in check.name]
    assert muse_checks
    assert all(check.ok for check in muse_checks)


async def test_the_runtime_default_is_rejected_for_a_catalog_that_does_not_declare_it(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    app = await app_with_agents(tmp_path, {_LEGACY: create_catalog_without_headless_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        await set_scope_text(pilot, LADDER_SCOPES[0], _LEGACY_DEFAULT_TEXT)
        await click_next(pilot)
        problem = pilot.app.query_one("#ladder-problem", Static)
        await wait_until(lambda: bool(str(problem.render()).strip()), timeout=UI_WAIT_SECONDS)
        assert pilot.app.query_one("#wizard-page-1").display
        await click_button(pilot, "#wizard-cancel")


async def test_a_muse_serve_that_never_answers_shows_the_catalog_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await git_project(tmp_path)
    private_doctor_runtime(monkeypatch, tmp_path)
    install_fake_muse(tmp_path, monkeypatch, serve_answers=False)
    app = await app_with_agents(tmp_path, {_MUSE: create_muse})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await click_next(pilot, "#wizard-page-1")
        guidance = pilot.app.query_one("#ladder-guidance", Static)
        await wait_until(
            lambda: _CATALOG_ERROR_PREFIX in str(guidance.render()),
            timeout=LONG_WAIT_SECONDS,
        )
        assert _CATALOG_ERROR_PREFIX in str(guidance.render())
        await click_button(pilot, "#wizard-cancel")
