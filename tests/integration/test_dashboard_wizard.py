from __future__ import annotations

import asyncio
import os
import stat
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Input, Select, Static

from shikiru.agents.registry import AgentRegistry
from shikiru.bootstrap import build_doctor, build_project_detectors, run_init_wizard
from shikiru.clock import SystemClock
from shikiru.config.loader import load_default_orchestration, load_settings
from shikiru.dashboard.wizard import InitWizardApp
from shikiru.errors import ConfigError
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from textual.pilot import Pilot

    from shikiru.operator.detectors import InitWizardResult

pytestmark = pytest.mark.asyncio


async def _git_project(root: Path) -> None:
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    (root / "tasks").mkdir()
    (root / "tasks" / "plan.md").write_text("plan\n")
    (root / ".gitignore").write_text("node_modules/\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "cache.dat").write_text("cache\n")
    runner = SubprocessRunner()
    await runner.run(["git", "init", "-q"], timeout=10, cwd=root, check=True)
    await runner.run(
        ["git", "add", ".gitignore", "tasks/plan.md"], timeout=10, cwd=root, check=True
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
            "initial",
        ],
        timeout=10,
        cwd=root,
        check=True,
    )


async def _app(root: Path) -> InitWizardApp:
    detectors = build_project_detectors(root)
    project = await detectors.detect()
    assert "fake" in project.agents
    return InitWizardApp(
        project_root=root,
        project=project,
        detectors=detectors,
        doctor=build_doctor(root),
        orchestration_defaults=load_default_orchestration(),
    )


async def _app_with_cow_probe_error(root: Path) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = SystemClock()
    registry = AgentRegistry()
    detectors = ProjectDetectors(
        root,
        git=Git(runner),
        plugin_checks=PluginChecks(registry, runner, clock),
        cache_cloner=CacheCloner(runner, cow_args=("shikiru-missing-cp",)),
    )
    project = await detectors.detect()
    return InitWizardApp(
        project_root=root,
        project=project,
        detectors=detectors,
        doctor=build_doctor(root),
        orchestration_defaults=load_default_orchestration(),
    )


async def test_detect_caches_preserves_nested_ignored_paths(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "README.md").write_text("tracked\n")
    runner = SubprocessRunner()
    await runner.run(["git", "add", "frontend/README.md"], timeout=10, cwd=tmp_path, check=True)
    await runner.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "track frontend",
        ],
        timeout=10,
        cwd=tmp_path,
        check=True,
    )
    nested_cache = frontend / "node_modules"
    nested_cache.mkdir(parents=True)
    (nested_cache / "cache.dat").write_text("nested cache\n")

    project = await build_project_detectors(tmp_path).detect()

    assert {"node_modules", "frontend/node_modules"} <= set(project.caches)


async def _replace_input(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    target = pilot.app.query_one(selector)
    if not await pilot.click(selector):
        for _ in range(12):
            await pilot.press("tab")
            if pilot.app.focused is target:
                break
        assert pilot.app.focused is target
    await pilot.press("ctrl+a", "ctrl+k")
    await pilot.press(*value)


async def _click_next(pilot: Pilot[InitWizardResult | None]) -> None:
    button = pilot.app.query_one("#wizard-next")
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    await pilot.click("#wizard-next")


async def _focus_with_tab(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    target = pilot.app.query_one(selector)
    for _ in range(12):
        if pilot.app.focused is target:
            return
        await pilot.press("tab")
    assert pilot.app.focused is target


async def _set_agents_and_trackers(pilot: Pilot[InitWizardResult | None], tmp_path: Path) -> None:
    await _click_next(pilot)
    await _click_next(pilot)
    assert str(pilot.app.query_one("#wizard-error", Static).render()).strip()
    assert not (tmp_path / ".shikiru" / "config.toml").exists()
    await _replace_input(pilot, "#leader-ladder", "missing:model:high")
    await _click_next(pilot)
    assert str(pilot.app.query_one("#wizard-error", Static).render()).strip()
    assert not (tmp_path / ".shikiru" / "config.toml").exists()
    await _replace_input(pilot, "#leader-ladder", "fake:smoke:low")
    await _replace_input(pilot, "#implementer-ladder", "fake:strong:high")
    await _replace_input(pilot, "#helper-ladder", "fake:smoke:low")
    await _click_next(pilot)
    await _replace_input(pilot, "#trackers", "tasks/plan.md, docs/todo.md")
    await _click_next(pilot)


async def _set_caches_parallelism_and_integration(
    pilot: Pilot[InitWizardResult | None],
) -> None:
    await _replace_input(pilot, "#caches", "node_modules, build-cache")
    assert "copy-on-write" in str(pilot.app.query_one("#cow-result", Static).render()).lower()
    await _click_next(pilot)
    await _replace_input(pilot, "#max-parallel", "3")
    await _click_next(pilot)
    await pilot.click("#integration")
    await pilot.press("down", "enter")
    assert cast("Select[str]", pilot.app.query_one("#integration", Select)).value == "pr"
    await _click_next(pilot)


async def _assert_doctor_viewport(
    pilot: Pilot[InitWizardResult | None], report_content: str
) -> None:
    report = pilot.app.query_one("#doctor-report", Static)
    body = pilot.app.query_one("#wizard-body")
    assert report_content in str(report.render())
    assert report.region.y < body.region.bottom
    assert report.region.bottom > body.region.y
    body.focus()
    await pilot.press("end")
    await wait_until(lambda: body.scroll_y == body.max_scroll_y, timeout=1)
    assert report.region.bottom <= body.region.bottom


def _private_doctor_runtime(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("TMPDIR", str(runtime))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


async def test_init_wizard_walkthrough_saves_choices_and_shows_doctor(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await _git_project(tmp_path)
    _private_doctor_runtime(monkeypatch, tmp_path)
    app = await _app(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        assert app.query_one("#wizard-page-0").display
        assert app.query_one("#wizard-next").region.bottom <= app.size.height
        await _set_agents_and_trackers(pilot, tmp_path)
        await _set_caches_parallelism_and_integration(pilot)
        assert app.query_one("#wizard-page-6").display
        assert app.query_one("#leader-ladder", Input).value == "fake:smoke:low"
        assert app.query_one("#implementer-ladder", Input).value == "fake:strong:high"
        assert app.query_one("#helper-ladder", Input).value == "fake:smoke:low"
        await _focus_with_tab(pilot, "#wizard-finish")
        await pilot.press("enter")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: bool(str(app.query_one("#doctor-report", Static).render())), timeout=60
        )
        await _assert_doctor_viewport(pilot, "PASS · repository:")
        await pilot.click("#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert result.doctor_report is not None
    assert result.doctor_report.ok
    settings = load_settings(tmp_path)
    assert settings.project.trackers == ["tasks/plan.md", "docs/todo.md"]
    assert settings.repo.caches == ["node_modules", "build-cache"]
    assert settings.roles.leader.ladder[0].model == "smoke"
    assert settings.roles.leader.ladder[0].effort == "low"
    assert settings.roles.implementer.ladder[0].model == "strong"
    assert settings.roles.implementer.ladder[0].effort == "high"
    assert settings.orchestration.max_parallel == 3
    assert settings.orchestration.integration == "pr"
    assert "/.shikiru/" in (tmp_path / ".git" / "info" / "exclude").read_text()
    probe_directories = await asyncio.to_thread(lambda: list(tmp_path.glob(".shikiru-cow-check-*")))
    assert probe_directories == []


async def test_init_wizard_keeps_config_and_exposes_failed_doctor_report(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await _git_project(tmp_path)
    _private_doctor_runtime(monkeypatch, tmp_path)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    tmux = executable_dir / "tmux"
    tmux.write_text("#!/bin/sh\nexit 9\n")
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    app = await _app(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        await _click_next(pilot)
        await _replace_input(pilot, "#leader-ladder", "fake:smoke:low")
        await _replace_input(pilot, "#implementer-ladder", "fake:smoke:low")
        await _replace_input(pilot, "#helper-ladder", "fake:smoke:low")
        for step in range(1, 6):
            await _click_next(pilot)
            timeout = 60 if step == 2 else 1
            await wait_until(
                lambda page_index=step + 1: app.query_one(f"#wizard-page-{page_index}").display,
                timeout=timeout,
            )
        assert app.query_one("#wizard-page-6").display
        await _focus_with_tab(pilot, "#wizard-finish")
        await pilot.press("enter")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: "FAIL · tmux:" in str(app.query_one("#doctor-report", Static).render()),
            timeout=60,
        )

        await _assert_doctor_viewport(pilot, "FAIL · tmux:")
        await pilot.click("#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert config_path.is_file()
    assert result.doctor_report is not None
    assert not result.doctor_report.ok
    assert any(check.name == "tmux" and not check.ok for check in result.doctor_report.checks)


async def test_cow_probe_error_remains_visible_after_navigation(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    app = await _app_with_cow_probe_error(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        await _click_next(pilot)
        await _replace_input(pilot, "#leader-ladder", "fake:smoke:low")
        await _replace_input(pilot, "#implementer-ladder", "fake:smoke:low")
        await _replace_input(pilot, "#helper-ladder", "fake:smoke:low")
        await _click_next(pilot)
        await _click_next(pilot)
        cow_result = app.query_one("#cow-result", Static)
        await wait_until(lambda: "CoW check failed" in str(cow_result.render()), timeout=3)
        assert "shikiru-missing-cp" in str(cow_result.render())
        await _click_next(pilot)
        await pilot.click("#wizard-back")
        assert "shikiru-missing-cp" in str(cow_result.render())


async def test_init_wizard_cancellation_does_not_write_project_state(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    app = await _app(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        await pilot.press("escape")

    assert app.return_value is None
    assert not (tmp_path / ".shikiru" / "config.toml").exists()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude = exclude_path.read_text() if exclude_path.exists() else ""
    assert "/.shikiru/" not in exclude


@pytest.mark.parametrize("entrypoint", ["wizard", "yes"])
async def test_init_refuses_nested_repository_config_without_changing_it(
    tmp_path: Path, entrypoint: str
) -> None:
    await _git_project(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    config_path = nested / ".shikiru" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("operator-owned config\n")
    detectors = build_project_detectors(nested)

    with pytest.raises(ConfigError):
        await _initialize(entrypoint, nested, detectors)

    assert config_path.read_text() == "operator-owned config\n"


async def test_init_creates_the_config_owner_only_from_the_start(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    previous = os.umask(0o022)
    try:
        await build_project_detectors(tmp_path).initialize()
    finally:
        os.umask(previous)

    config_path = tmp_path / ".shikiru" / "config.toml"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700


async def _initialize(entrypoint: str, root: Path, detectors: ProjectDetectors) -> None:
    if entrypoint == "wizard":
        await run_init_wizard(root)
    else:
        await detectors.initialize()


async def test_init_wizard_refuses_an_existing_config_without_changing_it(
    tmp_path: Path,
) -> None:
    await _git_project(tmp_path)
    config_path = tmp_path / ".shikiru" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text("operator-owned config\n")

    with pytest.raises(ConfigError):
        await run_init_wizard(tmp_path)

    assert config_path.read_text() == "operator-owned config\n"
