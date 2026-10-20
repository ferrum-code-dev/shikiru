from __future__ import annotations

import asyncio
import os
import stat
from io import StringIO
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent
from rich.console import Console
from textual.widgets import Input, Select, Static

import shikiru.agents.registry as registry_module
from shikiru.agents.contracts import AgentContext, CheckResult
from shikiru.agents.registry import AgentRegistry
from shikiru.bootstrap import build_doctor, build_project_detectors, run_init_wizard
from shikiru.cli.render import doctor_renderable
from shikiru.clock import SystemClock
from shikiru.config.loader import load_default_orchestration, load_settings
from shikiru.dashboard.wizard import InitWizardApp
from shikiru.errors import ConfigError
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.cells import (
    rendered_select_label,
    visible_widget_text,
)
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from textual.pilot import Pilot

    from shikiru.agents.contracts import Agent
    from shikiru.operator.detectors import InitWizardResult

pytestmark = pytest.mark.asyncio


class _LegacyAgent:
    name = "legacy"

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="available")]


def _create_legacy_agent(_context: AgentContext) -> Agent:
    return cast("Agent", _LegacyAgent())


def _no_entry_points(*, group: str) -> list[object]:
    del group
    return []


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
        doctor_renderer=doctor_renderable,
        orchestration_defaults=load_default_orchestration(),
    )


async def _app_with_cow_probe_error(root: Path) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = SystemClock()
    registry = AgentRegistry()
    detectors = ProjectDetectors(
        root,
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        plugin_checks=PluginChecks(registry, runner, clock),
        cache_cloner=CacheCloner(
            runner, cow_args=("shikiru-missing-cp",), copy_timeout=CACHE_COPY_TIMEOUT
        ),
    )
    project = await detectors.detect()
    return InitWizardApp(
        project_root=root,
        project=project,
        detectors=detectors,
        doctor=build_doctor(root),
        doctor_renderer=doctor_renderable,
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


async def _choose(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    select = cast("Select[str]", pilot.app.query_one(selector, Select))
    select.focus()
    await wait_until(lambda: pilot.app.focused is select, timeout=1)
    await pilot.press("enter")
    await pilot.press(*value)
    await pilot.press("enter")
    await wait_until(lambda: select.value == value, timeout=1)


async def _click_next(pilot: Pilot[InitWizardResult | None]) -> None:
    button = pilot.app.query_one("#wizard-next")
    await wait_until(lambda: not button.disabled, timeout=60)
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    await pilot.click("#wizard-next")


async def _click_scrolled(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    target = pilot.app.query_one(selector)
    target.focus()
    await wait_until(lambda: pilot.app.focused is target, timeout=1)
    await pilot.press("enter")


async def _click_back(pilot: Pilot[InitWizardResult | None], page_id: str) -> None:
    button = pilot.app.query_one("#wizard-back")
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    await pilot.click("#wizard-back")
    await wait_until(lambda: pilot.app.query_one(page_id).display, timeout=1)


async def _focus_with_tab(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    target = pilot.app.query_one(selector)
    for _ in range(12):
        if pilot.app.focused is target:
            return
        await pilot.press("tab")
    assert pilot.app.focused is target


async def _focus_with_shift_tab(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    target = pilot.app.query_one(selector)
    for _ in range(40):
        if pilot.app.focused is target:
            return
        await pilot.press("shift+tab")
    assert pilot.app.focused is target


async def _set_agents_and_trackers(pilot: Pilot[InitWizardResult | None]) -> None:
    await _click_next(pilot)

    await _choose(pilot, "#rung-leader-0-agent", "fake")
    await _choose(pilot, "#rung-leader-0-model", "smoke")
    await _choose(pilot, "#rung-leader-0-effort", "low")
    await _choose(pilot, "#rung-leader-0-model", "strong")
    effort = cast("Select[str]", pilot.app.query_one("#rung-leader-0-effort", Select))
    await wait_until(lambda: effort.value is Select.BLANK, timeout=1)
    await _choose(pilot, "#rung-leader-0-effort", "high")
    await _choose(pilot, "#rung-leader-0-model", "compact-visible")
    await wait_until(lambda: effort.value == "high", timeout=1)
    await _click_scrolled(pilot, "#leader-add")
    await wait_until(
        lambda: pilot.app.query_one("#rung-leader-1-model", Select).id == "rung-leader-1-model",
        timeout=1,
    )
    await _choose(pilot, "#rung-leader-1-agent", "fake")
    await _choose(pilot, "#rung-leader-1-model", "smoke")
    await _choose(pilot, "#rung-leader-1-effort", "low")
    await _click_scrolled(pilot, "#rung-leader-1-up")
    await wait_until(
        lambda: (
            cast("Select[str]", pilot.app.query_one("#rung-leader-0-model", Select)).value
            == "smoke"
        ),
        timeout=1,
    )
    await _click_scrolled(pilot, "#leader-add")
    await wait_until(
        lambda: pilot.app.query_one("#rung-leader-2-model", Select).id == "rung-leader-2-model",
        timeout=1,
    )
    await _click_scrolled(pilot, "#rung-leader-2-remove")
    await wait_until(
        lambda: not pilot.app.query("#rung-leader-2-model"),
        timeout=1,
    )

    await _choose(pilot, "#rung-implementer-0-agent", "fake")
    await _choose(pilot, "#rung-implementer-0-model", "strong")
    await _choose(pilot, "#rung-implementer-0-effort", "high")
    await _choose(pilot, "#rung-helper-0-agent", "fake")
    await _choose(pilot, "#rung-helper-0-model", "claude-fable-5-1[1m]")
    await _choose(pilot, "#rung-helper-0-effort", "high")
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
    integration = cast("Select[str]", pilot.app.query_one("#integration", Select))
    await wait_until(lambda: integration.value == "pr", timeout=GIT_COMMAND_TIMEOUT)
    assert integration.value == "pr"
    await _click_next(pilot)


def _doctor_text(report: Static) -> str:
    output = StringIO()
    Console(file=output, width=max(1, report.content_region.width), color_system=None).print(
        report.content
    )
    return output.getvalue()


async def _assert_doctor_viewport(
    pilot: Pilot[InitWizardResult | None], report_content: str
) -> None:
    report = pilot.app.query_one("#doctor-report", Static)
    body = pilot.app.query_one("#wizard-body")
    assert report_content in _doctor_text(report)
    assert report.region.y < body.region.bottom
    assert report.region.bottom > body.region.y
    body.focus()
    await pilot.press("home")
    await wait_until(lambda: body.scroll_y == 0, timeout=1)
    assert report_content in visible_widget_text(report, body)
    await pilot.press("end")
    await wait_until(lambda: body.scroll_y == body.max_scroll_y, timeout=1)
    assert report.region.bottom <= body.region.bottom
    assert visible_widget_text(report, body).strip()


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
        await _set_agents_and_trackers(pilot)
        await _click_back(pilot, "#wizard-page-2")
        assert app.query_one("#wizard-page-2").display
        assert pilot.app.query_one("#trackers", Input).value == "tasks/plan.md, docs/todo.md"
        await _click_back(pilot, "#wizard-page-1")
        assert app.query_one("#wizard-page-1").display
        assert (
            cast("Select[str]", pilot.app.query_one("#rung-leader-0-model", Select)).value
            == "smoke"
        )
        assert (
            cast("Select[str]", pilot.app.query_one("#rung-leader-1-model", Select)).value
            == "compact-visible"
        )
        assert (
            cast("Select[str]", pilot.app.query_one("#rung-implementer-0-model", Select)).value
            == "strong"
        )
        assert (
            cast("Select[str]", pilot.app.query_one("#rung-helper-0-model", Select)).value
            == "claude-fable-5-1[1m]"
        )
        await _click_next(pilot)
        await _click_next(pilot)
        await _set_caches_parallelism_and_integration(pilot)
        assert app.query_one("#wizard-page-6").display
        await _focus_with_tab(pilot, "#wizard-finish")
        await pilot.press("enter")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: bool(_doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        await _assert_doctor_viewport(pilot, "Config:")
        doctor_text = _doctor_text(app.query_one("#doctor-report", Static))
        doctor_text_without_wraps = doctor_text.replace("\n", "")
        doctor_text_normalized = " ".join(doctor_text.split())
        assert str(config_path.resolve()) in doctor_text_without_wraps
        await pilot.click("#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert result.doctor_report is not None
    assert result.doctor_report.ok
    visible_checks = [
        check
        for check in result.doctor_report.checks
        if check.name != "plugins"
        and not (check.name.startswith("plugin:") and check.name.count(":") == 1)
    ]
    for check in visible_checks:
        assert (
            check.detail in doctor_text_without_wraps
            or " ".join(check.detail.split()) in doctor_text_normalized
        )
    settings = load_settings(tmp_path)
    assert settings.project.trackers == ["tasks/plan.md", "docs/todo.md"]
    assert settings.repo.caches == ["node_modules", "build-cache"]
    assert [rung.model for rung in settings.roles.leader.ladder] == ["smoke", "compact-visible"]
    assert [rung.effort for rung in settings.roles.leader.ladder] == ["low", "high"]
    assert settings.roles.implementer.ladder[0].model == "strong"
    assert settings.roles.implementer.ladder[0].effort == "high"
    assert settings.roles.helper.ladder[0].model == "claude-fable-5-1[1m]"
    assert settings.roles.helper.ladder[0].effort == "high"
    assert settings.orchestration.max_parallel == 3
    assert settings.orchestration.integration == "pr"
    assert "/.shikiru/" in (tmp_path / ".git" / "info" / "exclude").read_text()
    probe_directories = await asyncio.to_thread(lambda: list(tmp_path.glob(".shikiru-cow-check-*")))
    assert probe_directories == []


async def test_init_wizard_keeps_run_doctor_focus_after_compact_resize(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await _git_project(tmp_path)
    _private_doctor_runtime(monkeypatch, tmp_path)
    app = await _app(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        await _click_next(pilot)
        leader_model = cast("Select[str]", app.query_one("#rung-leader-0-model", Select))
        assert leader_model.value is Select.BLANK
        await _click_next(pilot)
        await wait_until(
            lambda: (
                app.query_one("#wizard-error", Static).display
                and app.query_one("#wizard-page-1").display
            ),
            timeout=1,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await _choose(pilot, "#rung-leader-0-agent", "fake")
        await _choose(pilot, "#rung-leader-0-model", "compact-visible")
        await _choose(pilot, "#rung-leader-0-effort", "high")
        await _choose(pilot, "#rung-implementer-0-agent", "fake")
        await _choose(pilot, "#rung-implementer-0-model", "strong")
        await _choose(pilot, "#rung-implementer-0-effort", "high")
        await _choose(pilot, "#rung-helper-0-agent", "fake")
        await _choose(pilot, "#rung-helper-0-model", "smoke")
        await _choose(pilot, "#rung-helper-0-effort", "low")
        await _focus_with_shift_tab(pilot, "#rung-leader-0-model")
        await pilot.resize_terminal(40, 12)
        body = app.query_one("#wizard-body")
        await wait_until(
            lambda: (
                leader_model.content_region.height > 0
                and body.content_region.height > 0
                and "compact-visible" in rendered_select_label(leader_model)
            ),
            timeout=1,
        )
        assert app.focused is leader_model
        await _click_scrolled(pilot, "#rung-leader-0-remove")
        await _click_next(pilot)
        error = app.query_one("#wizard-error", Static)
        shell = app.query_one("#wizard-shell")

        def validation_is_visible() -> bool:
            error_text = str(error.render()).strip()
            visible_error = visible_widget_text(error, shell)
            return bool(error_text) and " ".join(error_text.split()) in " ".join(
                visible_error.split()
            )

        await wait_until(validation_is_visible, timeout=1)
        assert app.focused is app.query_one("#leader-add")
        await _click_scrolled(pilot, "#leader-add")
        await wait_until(
            lambda: app.query_one("#rung-leader-0-model", Select).id == "rung-leader-0-model",
            timeout=1,
        )
        await _choose(pilot, "#rung-leader-0-agent", "fake")
        await _choose(pilot, "#rung-leader-0-model", "compact-visible")
        await _choose(pilot, "#rung-leader-0-effort", "high")
        await _click_next(pilot)
        await _replace_input(pilot, "#trackers", "tasks/plan.md, docs/todo.md")
        await _click_next(pilot)
        await _set_caches_parallelism_and_integration(pilot)
        assert app.query_one("#wizard-page-6").display
        finish_button = app.query_one("#wizard-finish")
        await wait_until(lambda: app.focused is finish_button, timeout=1)
        await pilot.resize_terminal(40, 12)
        navigation_buttons = tuple(
            app.query_one(selector)
            for selector in ("#wizard-back", "#wizard-finish", "#wizard-cancel")
        )
        await wait_until(
            lambda: all(
                button.region.width > 0
                and button.region.right <= app.size.width
                and button.region.bottom <= app.size.height
                for button in navigation_buttons
            ),
            timeout=1,
        )
        assert app.focused is finish_button


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
        await _set_agents_and_trackers(pilot)
        for step in range(4, 7):
            await _click_next(pilot)
            await wait_until(
                lambda page_index=step: app.query_one(f"#wizard-page-{page_index}").display,
                timeout=60 if step == 4 else 1,
            )
        assert app.query_one("#wizard-page-6").display
        await _focus_with_tab(pilot, "#wizard-finish")
        await pilot.press("enter")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: "exit 9" in _doctor_text(app.query_one("#doctor-report", Static)),
            timeout=60,
        )

        await _assert_doctor_viewport(pilot, "Config:")
        await pilot.resize_terminal(40, 12)
        await wait_until(lambda: app.size.width == 40 and app.size.height == 12, timeout=1)
        report = app.query_one("#doctor-report", Static)
        await wait_until(lambda: "exit 9" in _doctor_text(report), timeout=1)
        doctor_text = _doctor_text(report)
        assert str(config_path.resolve()) in doctor_text.replace("\n", "")
        await pilot.click("#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert config_path.is_file()
    assert result.doctor_report is not None
    assert not result.doctor_report.ok
    failed_checks = tuple(check for check in result.doctor_report.checks if not check.ok)
    assert any(check.name == "tmux" for check in failed_checks)
    rendered_failure = " ".join(doctor_text.split())
    assert all(" ".join(check.detail.split()) in rendered_failure for check in failed_checks)


async def test_init_wizard_explains_and_blocks_agents_without_model_catalogs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    runner = SubprocessRunner()
    registry = AgentRegistry()
    registry.register("fake", create_fake_agent)
    registry.register("fake-copy", create_fake_agent)
    registry.register("legacy", _create_legacy_agent)
    detectors = ProjectDetectors(
        tmp_path,
        git=Git(runner, command_timeout=GIT_COMMAND_TIMEOUT),
        plugin_checks=PluginChecks(registry, runner, SystemClock()),
        cache_cloner=CacheCloner(runner, cow_args=None, copy_timeout=CACHE_COPY_TIMEOUT),
    )
    project = await detectors.detect()
    app = InitWizardApp(
        project_root=tmp_path,
        project=project,
        detectors=detectors,
        doctor=build_doctor(tmp_path),
        doctor_renderer=doctor_renderable,
        orchestration_defaults=load_default_orchestration(),
    )

    async with app.run_test(size=(80, 24)) as pilot:
        await _click_next(pilot)
        status = app.query_one("#agent-choice-status", Static)
        guidance = app.query_one("#agent-choice-guidance", Static)
        await wait_until(lambda: "legacy" in str(status.content), timeout=1)
        assert guidance.display
        agent = cast("Select[str]", app.query_one("#rung-leader-0-agent", Select))
        assert agent.value == "fake"
        await _choose(pilot, "#rung-leader-0-model", "default")
        await _choose(pilot, "#rung-leader-0-effort", "low")
        await _choose(pilot, "#rung-leader-0-agent", "fake-copy")
        model = cast("Select[str]", app.query_one("#rung-leader-0-model", Select))
        effort = cast("Select[str]", app.query_one("#rung-leader-0-effort", Select))
        await wait_until(
            lambda: (
                model.value is Select.BLANK and effort.value is Select.BLANK and effort.disabled
            ),
            timeout=1,
        )
        await _choose(pilot, "#rung-leader-0-agent", "fake")
        await wait_until(lambda: model.value is Select.BLANK and effort.disabled, timeout=1)
        await _choose(pilot, "#rung-leader-0-model", "default")
        await _choose(pilot, "#rung-leader-0-effort", "low")
        agent.focus()
        await pilot.press("enter", *"legacy", "enter")
        await wait_until(lambda: agent.value == "fake", timeout=1)
        await _choose(pilot, "#rung-leader-0-model", "default")
        await _choose(pilot, "#rung-implementer-0-model", "default")
        await _choose(pilot, "#rung-helper-0-model", "default")
        await _click_next(pilot)
        await wait_until(lambda: app.query_one("#wizard-page-2").display, timeout=1)

    assert not (tmp_path / ".shikiru" / "config.toml").exists()


async def test_cow_probe_error_remains_visible_after_navigation(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    app = await _app_with_cow_probe_error(tmp_path)

    async with app.run_test(size=(64, 20)) as pilot:
        await _set_agents_and_trackers(pilot)
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
