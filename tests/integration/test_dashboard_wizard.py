from __future__ import annotations

import asyncio
import os
import stat
from io import StringIO
from typing import TYPE_CHECKING, cast

import pytest
from fake_agent.agent import create as create_fake_agent
from rich.console import Console
from textual.widgets import Button, Input, Select, Static

import shikiru.agents.registry as registry_module
import shikiru.dashboard.wizard as wizard_module
from shikiru.agents.contracts import AgentContext, CheckResult, ModelCatalog, ModelChoice, Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.bootstrap import build_doctor, build_project_detectors, run_init_wizard
from shikiru.cli.render import doctor_renderable
from shikiru.clock import SystemClock
from shikiru.config.loader import load_default_orchestration, load_settings
from shikiru.dashboard.wizard import InitWizardApp
from shikiru.domain.ladder import pick_rung
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.doctor import Doctor
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.factories import make_task
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from textual.pilot import Pilot

    from shikiru.agents.contracts import Agent, AgentFactory
    from shikiru.operator.detectors import InitWizardResult

pytestmark = pytest.mark.asyncio

WIZARD_SIZE = (100, 40)
COMPACT_WIZARD_SIZE = (40, 12)
TRANSIENT_SMALL_SIZE = (40, 8)
WIZARD_TIMER_TEST_SECONDS = 1.5
MODEL_WITH_LITERAL_BRACKETS = "claude-fable-5-1[1m]"
LONG_TRACKER_CANDIDATE = (
    "tasks/z-very-long-tracker-candidate-name-that-wraps-without-losing-its-final-segments.md"
)
LONG_CACHE_CANDIDATE = (
    "z-very-long-cache-directory-name-that-wraps-before-the-node-modules-suffix/node_modules"
)
SELECTOR_OPTIONS = {
    "#ladder-agent": ("", "fake", "fake-copy", "legacy"),
    "#ladder-model": (
        "",
        "default",
        "strong",
        "smoke",
        "compact-visible",
        MODEL_WITH_LITERAL_BRACKETS,
    ),
    "#integration": ("merge", "pr"),
}
LADDER_SCOPES = (
    "Leader",
    "Implementer · Base",
    "Implementer · Low",
    "Implementer · Normal",
    "Implementer · High",
    "Helper",
)


class _LegacyAgent:
    name = "legacy"

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="available")]


class _CatalogWithoutHeadlessAgent:
    name = "legacy"

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="available")]

    async def model_catalog(self) -> ModelCatalog:
        return ModelCatalog(models=(ModelChoice("listed-model", ()),), complete=True)


def _create_legacy_agent(_context: AgentContext) -> Agent:
    return cast("Agent", _LegacyAgent())


def _create_catalog_without_headless_agent(_context: AgentContext) -> Agent:
    return cast("Agent", _CatalogWithoutHeadlessAgent())


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
    return InitWizardApp(
        project_root=root,
        project=project,
        detectors=detectors,
        doctor=build_doctor(root),
        doctor_renderer=doctor_renderable,
        orchestration_defaults=load_default_orchestration(),
    )


async def _app_with_agents(root: Path, agents: Mapping[str, AgentFactory]) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = SystemClock()
    registry = AgentRegistry()
    for name, factory in agents.items():
        registry.register(name, factory)
    plugin_checks = PluginChecks(registry, runner, clock)
    git = Git(runner, command_timeout=GIT_COMMAND_TIMEOUT)
    cache_cloner = CacheCloner(
        runner,
        cow_args=None,
        copy_timeout=CACHE_COPY_TIMEOUT,
    )
    detectors = ProjectDetectors(
        root,
        git=git,
        plugin_checks=plugin_checks,
        cache_cloner=cache_cloner,
    )
    project = await detectors.detect()
    doctor = Doctor(
        root,
        cache_cloner=cache_cloner,
        git=git,
        runner=runner,
        clock=clock,
        plugin_checks=plugin_checks,
        probe=AgentProbe(registry, runner, clock),
    )
    return InitWizardApp(
        project_root=root,
        project=project,
        detectors=detectors,
        doctor=doctor,
        doctor_renderer=doctor_renderable,
        orchestration_defaults=load_default_orchestration(),
    )


async def _app_with_cow_probe_error(root: Path) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = SystemClock()
    registry = AgentRegistry()
    registry.register("fake", create_fake_agent)
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


async def _replace_input(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    target = pilot.app.query_one(selector, Input)
    target.focus()
    await wait_until(lambda: pilot.app.focused is target, timeout=1)
    await pilot.press("ctrl+a", "ctrl+k")
    await pilot.press(*value)


async def _choose(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    select = cast("Select[str]", pilot.app.query_one(selector, Select))
    select.focus()
    await wait_until(lambda: pilot.app.focused is select, timeout=1)
    await pilot.press("enter")
    overlay = select.query_one("SelectOverlay")
    await wait_until(
        lambda: select.expanded and pilot.app.focused is overlay,
        timeout=1,
    )
    options = _selection_order(pilot, selector)
    selected = "" if select.value is Select.BLANK else select.value
    difference = options.index(value) - options.index(selected)
    direction = "down" if difference > 0 else "up"
    await pilot.press(*(direction for _ in range(abs(difference))))
    await pilot.press("enter")
    await wait_until(lambda: select.value == value, timeout=1)
    await wait_until(lambda: not select.expanded, timeout=1)


def _selection_order(pilot: Pilot[InitWizardResult | None], selector: str) -> tuple[str, ...]:
    if selector == "#ladder-model":
        agent = cast("Select[str]", pilot.app.query_one("#ladder-agent", Select)).value
        if agent == "legacy":
            return ("", "listed-model")
    if selector == "#ladder-effort":
        selected_model = cast("Select[str]", pilot.app.query_one("#ladder-model", Select)).value
        if selected_model == "default":
            return ("", "low", "high")
        if selected_model in {"strong", "compact-visible", MODEL_WITH_LITERAL_BRACKETS}:
            return ("", "high")
        if selected_model == "smoke":
            return ("", "low")
        return ("", "low", "high")
    return SELECTOR_OPTIONS[selector]


async def _click_button(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    button = pilot.app.query_one(selector, Button)
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    body = pilot.app.query_one("#wizard-body")
    body.scroll_to_widget(button, animate=False, immediate=True, top=True)
    await wait_until(lambda: button.is_on_screen, timeout=3)
    await wait_until(lambda: not button.disabled, timeout=3)
    button.focus()
    await wait_until(lambda: pilot.app.focused is button, timeout=1)
    await pilot.press("enter")


async def _click_next(
    pilot: Pilot[InitWizardResult | None], expected_page: str | None = None
) -> None:
    button = pilot.app.query_one("#wizard-next", Button)
    await wait_until(lambda: not button.disabled, timeout=60)
    await wait_until(
        lambda: (
            button.region.width > 0
            and button.region.height > 0
            and button.region.right <= pilot.app.size.width
            and button.region.bottom <= pilot.app.size.height
        ),
        timeout=3,
    )
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    clicked = await pilot.click(button, offset=(button.region.width // 2, 0))
    assert clicked
    if expected_page is not None:
        page = pilot.app.query_one(expected_page)
        error = pilot.app.query_one("#wizard-error", Static)
        ladder_problem = pilot.app.query_one("#ladder-problem", Static)
        await wait_until(
            lambda: (
                page.display
                or bool(str(error.render()).strip())
                or bool(str(ladder_problem.render()).strip())
            ),
            timeout=60,
        )
        assert page.display, str(error.render()) or str(ladder_problem.render())


async def _click_back(pilot: Pilot[InitWizardResult | None], expected_page: str) -> None:
    button = pilot.app.query_one("#wizard-back", Button)
    await wait_until(lambda: not button.has_class("-active"), timeout=1)
    clicked = await pilot.click(button, offset=(button.region.width // 2, 0))
    assert clicked
    await wait_until(lambda: pilot.app.query_one(expected_page).display, timeout=3)


async def _assign_rung(
    pilot: Pilot[InitWizardResult | None],
    *,
    agent: str,
    model: str,
    effort: str | None,
) -> None:
    await _choose(pilot, "#ladder-agent", agent)
    await _choose(pilot, "#ladder-model", model)
    if effort is not None:
        await _choose(pilot, "#ladder-effort", effort)
    await _click_button(pilot, "#ladder-add")


async def _set_role(pilot: Pilot[InitWizardResult | None], role: str) -> None:
    scope_by_role = {
        "leader": LADDER_SCOPES[0],
        "implementer": LADDER_SCOPES[1],
        "helper": LADDER_SCOPES[-1],
    }
    scope = scope_by_role[role]
    await _set_scope(pilot, scope)


async def _set_scope(pilot: Pilot[InitWizardResult | None], scope: str) -> None:
    row_list = pilot.app.query_one("#ladder-scopes", wizard_module.RowList)
    row_list.focus()
    await wait_until(lambda: pilot.app.focused is row_list, timeout=1)
    target = LADDER_SCOPES.index(scope)
    distance = target - row_list.selected
    if distance:
        await pilot.press(*(("down" if distance > 0 else "up",) * abs(distance)))
    await wait_until(
        lambda: (
            row_list.selected == target
            and scope in str(pilot.app.query_one("#ladder-input-title", Static).render())
        ),
        timeout=1,
    )


async def _set_scope_text(
    pilot: Pilot[InitWizardResult | None],
    scope: str,
    value: str,
) -> None:
    await _set_scope(pilot, scope)
    await _replace_input(pilot, "#ladder-input", value)


async def _set_base_ladders(pilot: Pilot[InitWizardResult | None]) -> None:
    for scope in (LADDER_SCOPES[0], LADDER_SCOPES[1], LADDER_SCOPES[-1]):
        await _set_scope(pilot, scope)
        await _assign_rung(pilot, agent="fake", model="smoke", effort="low")


async def _configure_leader_ladder(pilot: Pilot[InitWizardResult | None]) -> None:
    await _set_scope(pilot, LADDER_SCOPES[0])
    await _assign_rung(pilot, agent="fake-copy", model="strong", effort="high")
    await _assign_rung(pilot, agent="fake", model="default", effort=None)


async def _configure_implementer_ladder(pilot: Pilot[InitWizardResult | None]) -> None:
    await _set_scope(pilot, LADDER_SCOPES[1])
    await _assign_rung(pilot, agent="fake-copy", model="smoke", effort="low")
    await _assign_rung(pilot, agent="fake", model="strong", effort="high")


async def _configure_complexity_ladders(pilot: Pilot[InitWizardResult | None]) -> None:
    await _set_scope(pilot, LADDER_SCOPES[2])
    await _assign_rung(pilot, agent="fake", model="compact-visible", effort="high")
    await _set_scope(pilot, LADDER_SCOPES[3])
    await wait_until(lambda: app_field_value(pilot) == "", timeout=1)
    assert app_field_value(pilot) == ""
    await _set_scope_text(pilot, LADDER_SCOPES[4], "fake-copy:strong:high")


async def _configure_helper_rung(pilot: Pilot[InitWizardResult | None]) -> None:
    await _set_scope(pilot, LADDER_SCOPES[-1])
    await _assign_rung(
        pilot,
        agent="fake-copy",
        model=MODEL_WITH_LITERAL_BRACKETS,
        effort="high",
    )
    await _assign_rung(pilot, agent="fake", model="default", effort="high")


def app_field_value(pilot: Pilot[InitWizardResult | None]) -> str:
    return pilot.app.query_one("#ladder-input", Input).value


def _doctor_text(report: Static) -> str:
    output = StringIO()
    Console(file=output, width=max(1, report.content_region.width), color_system=None).print(
        report.content
    )
    return output.getvalue()


def _private_doctor_runtime(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("TMPDIR", str(runtime))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


async def _resize(pilot: Pilot[InitWizardResult | None], size: tuple[int, int]) -> None:
    await pilot.resize_terminal(*size)
    await wait_until(
        lambda: pilot.app.size.width == size[0] and pilot.app.size.height == size[1],
        timeout=3,
    )


async def _exercise_candidate_readd(
    pilot: Pilot[InitWizardResult | None],
    *,
    list_id: str,
    input_id: str,
    candidate: str,
    manual_path: str,
    additional_candidate: str,
) -> None:
    candidate_list = pilot.app.query_one(f"#{list_id}")
    field = pilot.app.query_one(f"#{input_id}", Input)
    assert field.value == ""
    candidate_list.focus()
    await wait_until(lambda: pilot.app.focused is candidate_list, timeout=1)
    await pilot.press("enter")
    await wait_until(lambda: field.value == candidate, timeout=1)
    add_button = pilot.app.query_one(f"#{input_id}-candidate-add-0", Button)
    await wait_until(lambda: add_button.disabled, timeout=1)
    await pilot.press("enter")
    await wait_until(lambda: field.value == candidate, timeout=1)
    await _replace_input(pilot, f"#{input_id}", manual_path)
    await wait_until(lambda: not add_button.disabled, timeout=1)
    add_button.focus()
    await wait_until(lambda: pilot.app.focused is add_button, timeout=1)
    await pilot.press("enter")
    await wait_until(lambda: field.value == f"{manual_path}, {candidate}", timeout=1)
    additional_button = pilot.app.query_one(f"#{input_id}-candidate-add-1", Button)
    additional_button.focus()
    await wait_until(lambda: pilot.app.focused is additional_button, timeout=1)
    await pilot.press("enter")
    await wait_until(
        lambda: field.value == f"{manual_path}, {candidate}, {additional_candidate}", timeout=1
    )
    await wait_until(
        lambda: add_button.disabled and additional_button.disabled,
        timeout=1,
    )
    candidate_list.focus()
    await wait_until(lambda: pilot.app.focused is candidate_list, timeout=1)
    await pilot.press("enter")
    await wait_until(
        lambda: field.value == f"{manual_path}, {candidate}, {additional_candidate}", timeout=1
    )


async def _verify_back_navigation_and_fill_project_fields(
    pilot: Pilot[InitWizardResult | None],
) -> None:
    await _resize(pilot, (40, 40))
    await _resize(pilot, WIZARD_SIZE)
    await _resize(pilot, (100, 12))
    await _resize(pilot, WIZARD_SIZE)
    await _click_next(pilot, "#wizard-page-2")
    await _exercise_candidate_readd(
        pilot,
        list_id=wizard_module.TRACKERS_LIST_ID,
        input_id=wizard_module.TRACKERS_INPUT_ID,
        candidate="tasks/plan.md",
        manual_path="docs/todo.md",
        additional_candidate=LONG_TRACKER_CANDIDATE,
    )
    await _click_back(pilot, "#wizard-page-1")
    await _resize(pilot, COMPACT_WIZARD_SIZE)
    await _click_next(pilot, "#wizard-page-2")
    await _resize(pilot, WIZARD_SIZE)
    await _click_next(pilot, "#wizard-page-3")
    await _exercise_candidate_readd(
        pilot,
        list_id=wizard_module.CACHES_LIST_ID,
        input_id=wizard_module.CACHES_INPUT_ID,
        candidate="node_modules",
        manual_path="build-cache",
        additional_candidate=LONG_CACHE_CANDIDATE,
    )
    await _click_next(pilot, "#wizard-page-4")
    parallel = pilot.app.query_one(f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", Input)
    await _replace_input(pilot, f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", "0")
    await wait_until(lambda: parallel.value == "1", timeout=1)
    await _replace_input(pilot, f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", "-2")
    await wait_until(lambda: parallel.value == "1", timeout=1)
    await _replace_input(pilot, f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", "")
    await wait_until(lambda: parallel.value == "", timeout=1)
    await _click_next(pilot, "#wizard-page-5")
    await _choose(pilot, "#integration", "pr")
    await _click_next(pilot, "#wizard-page-6")


def _assert_written_mixed_config(tmp_path: Path, result: InitWizardResult) -> None:
    assert result.doctor_report is not None
    assert result.doctor_report.ok
    settings = load_settings(tmp_path)
    assert settings.roles.leader.ladder == [
        Rung(agent="fake-copy", model="strong", effort="high"),
        Rung(agent="fake", model="default"),
    ]
    assert settings.roles.implementer.ladder == [
        Rung(agent="fake-copy", model="smoke", effort="low"),
        Rung(agent="fake", model="strong", effort="high"),
    ]
    assert settings.roles.implementer.complexity.keys() == {"low", "high"}
    assert pick_rung(make_task(complexity="low"), Role.IMPLEMENTER, settings) == Rung(
        agent="fake", model="compact-visible", effort="high"
    )
    assert pick_rung(make_task(complexity="normal"), Role.IMPLEMENTER, settings) == Rung(
        agent="fake-copy", model="smoke", effort="low"
    )
    assert pick_rung(make_task(complexity="high"), Role.IMPLEMENTER, settings) == Rung(
        agent="fake-copy", model="strong", effort="high"
    )
    assert settings.roles.helper.ladder == [
        Rung(agent="fake-copy", model=MODEL_WITH_LITERAL_BRACKETS, effort="high"),
        Rung(agent="fake", model="default", effort="high"),
    ]
    assert settings.project.trackers == [
        "docs/todo.md",
        "tasks/plan.md",
        LONG_TRACKER_CANDIDATE,
    ]
    assert settings.repo.caches == ["build-cache", "node_modules", LONG_CACHE_CANDIDATE]
    assert settings.orchestration.max_parallel == 3
    assert settings.orchestration.integration == "pr"
    assert "/.shikiru/" in (tmp_path / ".git" / "info" / "exclude").read_text()


async def test_init_writes_mixed_multirung_config_and_runtime_resolves_complexity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
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
    _private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(
        tmp_path,
        {"fake": create_fake_agent, "fake-copy": create_fake_agent},
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible and frame.region.width > 0, timeout=3)
        preferred_frame_size = (frame.region.width, frame.region.height)
        await _click_next(pilot, "#wizard-page-1")
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
            timeout=3,
        )
        await _configure_leader_ladder(pilot)
        await _configure_implementer_ladder(pilot)
        await _configure_complexity_ladders(pilot)
        await _configure_helper_rung(pilot)
        await _verify_back_navigation_and_fill_project_fields(pilot)
        await _click_button(pilot, "#wizard-finish")
        parallel = app.query_one(f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", Input)
        await wait_until(
            lambda: (
                app.query_one("#wizard-page-4").display
                and app.focused is parallel
                and bool(str(app.query_one("#wizard-error", Static).render()).strip())
            ),
            timeout=3,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await _replace_input(pilot, f"#{wizard_module.MAX_PARALLEL_INPUT_ID}", "3")
        await _click_next(pilot, "#wizard-page-5")
        await _click_next(pilot, "#wizard-page-6")
        assert (frame.region.width, frame.region.height) == preferred_frame_size
        await _click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: bool(_doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        await _resize(pilot, COMPACT_WIZARD_SIZE)
        await wait_until(lambda: body.max_scroll_y > 0, timeout=3)
        await pilot.press("pagedown")
        await wait_until(lambda: body.scroll_y > 0, timeout=3)
        await pilot.press("pageup")
        await wait_until(lambda: body.scroll_y == 0, timeout=3)
        await _resize(pilot, TRANSIENT_SMALL_SIZE)
        await _resize(pilot, WIZARD_SIZE)
        await wait_until(
            lambda: (frame.region.width, frame.region.height) == preferred_frame_size,
            timeout=3,
        )
        await pilot.click("#wizard-finish")

    _assert_written_mixed_config(tmp_path, cast("InitWizardResult", app.return_value))


async def test_transient_small_resize_preserves_drafts_and_recovers_navigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    config_path = tmp_path / ".shikiru" / "config.toml"

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _click_next(pilot, "#wizard-page-2")
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await _replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        await _resize(pilot, TRANSIENT_SMALL_SIZE)
        await wait_until(
            lambda: (
                app.is_running
                and app.query_one("#wizard-page-2").display
                and tracker_field.value == "draft.md"
            ),
            timeout=3,
        )
        assert not config_path.exists()
        await _resize(pilot, COMPACT_WIZARD_SIZE)
        await wait_until(
            lambda: app.query_one("#wizard-page-2").display and tracker_field.value == "draft.md",
            timeout=3,
        )
        await _click_next(pilot, "#wizard-page-3")
        assert not config_path.exists()
        await _click_button(pilot, "#wizard-cancel")

    assert app.return_value is None
    assert not config_path.exists()


async def test_art_reveal_preserves_wizard_state_across_resize(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=3)
        body = app.query_one("#wizard-body", wizard_module.InitWizardBody)
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _click_next(pilot, "#wizard-page-2")
        await _resize(pilot, (40, 12))
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await _replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        candidate_list = app.query_one(f"#{wizard_module.TRACKERS_LIST_ID}")
        candidate_list.focus()
        await wait_until(lambda: app.focused is candidate_list, timeout=1)
        await pilot.press("enter")
        await wait_until(lambda: tracker_field.value == "draft.md, tasks/plan.md", timeout=1)
        candidate_list.focus()
        await wait_until(lambda: app.focused is candidate_list, timeout=1)
        body.scroll_home(animate=False)
        await wait_until(lambda: body.scroll_y == 0, timeout=1)
        await wait_until(lambda: body.max_scroll_y > 0, timeout=3)
        body.scroll_to(y=body.max_scroll_y, animate=False, immediate=True)
        await wait_until(lambda: body.scroll_y == body.max_scroll_y, timeout=1)
        saved_scroll_y = body.scroll_y
        await pilot.press("ctrl+f")
        await wait_until(lambda: not frame.visible, timeout=1)
        await _resize(pilot, (80, 12))
        await wait_until(lambda: frame.visible, timeout=3)
        await wait_until(lambda: app.focused is candidate_list, timeout=1)
        await wait_until(
            lambda: body.scroll_y == min(saved_scroll_y, body.max_scroll_y),
            timeout=1,
        )
        await wait_until(
            lambda: tracker_field.value == "draft.md, tasks/plan.md",
            timeout=1,
        )
        await wait_until(lambda: app.query_one("#wizard-page-2").display, timeout=1)

        await _resize(pilot, (80, 24))
        await _click_button(pilot, "#wizard-cancel")


async def test_hidden_wizard_frame_ignores_navigation_keys(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        repository_page = app.query_one("#wizard-page-0")
        next_button = app.query_one("#wizard-next", Button)
        await wait_until(lambda: frame.visible, timeout=3)
        next_button.focus()
        await wait_until(lambda: app.focused is next_button, timeout=1)
        await pilot.press("ctrl+f")
        await wait_until(lambda: not frame.visible, timeout=1)
        await pilot.press("enter", "tab")
        await wait_until(lambda: not frame.visible and repository_page.display, timeout=1)
        await wait_until(lambda: frame.visible, timeout=3)
        await wait_until(lambda: app.focused is next_button, timeout=1)
        await _click_button(pilot, "#wizard-cancel")


async def test_art_reveal_does_not_interrupt_editors_or_expanded_selects(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    monkeypatch.setattr(wizard_module, "ART_REVEAL_SECONDS", WIZARD_TIMER_TEST_SECONDS)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=3)
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _click_next(pilot, "#wizard-page-2")
        tracker_field = app.query_one(f"#{wizard_module.TRACKERS_INPUT_ID}", Input)
        await _replace_input(pilot, f"#{wizard_module.TRACKERS_INPUT_ID}", "draft.md")
        await pilot.press("ctrl+f")
        await wait_until(lambda: frame.visible and tracker_field.value == "draft.md", timeout=1)
        await _click_next(pilot, "#wizard-page-3")
        await _click_next(pilot, "#wizard-page-4")
        await _click_next(pilot, "#wizard-page-5")
        integration = cast(
            "Select[str]", app.query_one(f"#{wizard_module.INTEGRATION_SELECT_ID}", Select)
        )
        integration.focus()
        await wait_until(lambda: app.focused is integration, timeout=1)
        await pilot.press("enter")
        await wait_until(lambda: integration.expanded, timeout=1)
        await pilot.press("ctrl+f")
        await wait_until(lambda: frame.visible and integration.expanded, timeout=1)
        await pilot.press("escape")
        await wait_until(lambda: app.is_running and not integration.expanded, timeout=1)
        await _click_button(pilot, "#wizard-cancel")


async def test_hidden_incomplete_complexity_draft_blocks_write_with_its_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    _private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _set_scope_text(pilot, LADDER_SCOPES[3], "   ")
        await _set_scope_text(pilot, LADDER_SCOPES[4], "broken")
        await _set_role(pilot, "leader")
        await _click_next(pilot)
        error = app.query_one("#ladder-problem", Static)
        await wait_until(lambda: bool(str(error.render()).strip()), timeout=3)
        error_text = str(error.render())
        assert "Implementer" in error_text
        assert "High" in error_text
        await wait_until(
            lambda: (
                app.query_one("#ladder-scopes", wizard_module.RowList).selected
                == LADDER_SCOPES.index("Implementer · High")
                and app.focused is app.query_one("#ladder-input", Input)
            ),
            timeout=1,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await _set_scope_text(pilot, LADDER_SCOPES[4], "fake:default:high")
        await _click_next(pilot, "#wizard-page-2")
        await _click_next(pilot, "#wizard-page-3")
        await _click_next(pilot, "#wizard-page-4")
        await _click_next(pilot, "#wizard-page-5")
        await _click_next(pilot, "#wizard-page-6")
        await _click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        await wait_until(
            lambda: bool(_doctor_text(app.query_one("#doctor-report", Static)).strip()),
            timeout=60,
        )
        result_button = app.query_one("#wizard-finish", Button)
        await pilot.press("escape", "ctrl+q")
        await wait_until(lambda: app.is_running and result_button.label == "Exit", timeout=1)
        await _click_button(pilot, "#wizard-finish")

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
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(tmp_path, {"legacy": _create_legacy_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        guidance = app.query_one("#ladder-guidance", Static)
        await wait_until(
            lambda: (
                "legacy" in str(guidance.render())
                and "authoritative model catalog" in str(guidance.render())
            ),
            timeout=3,
        )
        await _click_next(pilot)
        await wait_until(
            lambda: bool(str(app.query_one("#ladder-problem", Static).render()).strip()),
            timeout=3,
        )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await pilot.press("escape")
        assert app.is_running
        await _click_button(pilot, "#wizard-cancel")


async def test_helper_requires_headless_agent_capability(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(tmp_path, {"legacy": _create_catalog_without_headless_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_scope_text(pilot, LADDER_SCOPES[0], "legacy:listed-model")
        await _set_scope_text(pilot, LADDER_SCOPES[1], "legacy:listed-model")
        await _set_scope_text(pilot, LADDER_SCOPES[-1], "legacy:listed-model")
        await _set_scope(pilot, LADDER_SCOPES[0])
        await _choose(pilot, "#ladder-agent", "legacy")
        await _choose(pilot, "#ladder-model", "listed-model")
        await _click_next(pilot)
        error = app.query_one("#ladder-problem", Static)
        await wait_until(lambda: "headless support" in str(error.render()), timeout=3)
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await wait_until(
            lambda: (
                app.query_one("#ladder-scopes", wizard_module.RowList).selected
                == LADDER_SCOPES.index("Helper")
                and pilot.app.focused is app.query_one("#ladder-input", Input)
            ),
            timeout=1,
        )
        assert app.query_one("#ladder-agent", Select).disabled
        await _click_button(pilot, "#wizard-cancel")


async def test_ladder_draft_and_caret_survive_scope_changes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_scope_text(pilot, LADDER_SCOPES[0], "fake:smoke,,unfinished")
        field = app.query_one("#ladder-input", Input)
        cursor_position = len(field.value) // 2
        field.cursor_position = cursor_position
        await _choose(pilot, "#ladder-agent", "fake")
        await _choose(pilot, "#ladder-model", "smoke")
        await _click_button(pilot, "#ladder-add")
        assert field.value == "fake:smoke,,unfinished"
        await wait_until(lambda: field.cursor_position == cursor_position, timeout=1)
        await _set_scope(pilot, LADDER_SCOPES[2])
        await wait_until(lambda: field.value == "", timeout=1)
        assert field.value == ""
        await _set_scope(pilot, LADDER_SCOPES[0])
        await wait_until(lambda: field.value == "fake:smoke,,unfinished", timeout=1)
        await pilot.press("enter")
        await wait_until(lambda: pilot.app.focused is field, timeout=1)
        await wait_until(lambda: field.cursor_position == cursor_position, timeout=1)
        assert field.value == "fake:smoke,,unfinished"
        assert field.cursor_position == cursor_position
        await pilot.press("x")
        await wait_until(
            lambda: field.value == "fake:smoke,x,unfinished",
            timeout=1,
        )
        await _click_button(pilot, "#wizard-cancel")


async def test_agent_and_model_changes_clear_dependent_add_choices(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_agents(
        tmp_path,
        {"fake": create_fake_agent, "fake-copy": create_fake_agent},
    )

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_scope(pilot, LADDER_SCOPES[0])
        await _choose(pilot, "#ladder-agent", "fake")
        await _choose(pilot, "#ladder-model", "default")
        await _choose(pilot, "#ladder-effort", "high")
        await _choose(pilot, "#ladder-model", "smoke")
        effort = cast("Select[str]", app.query_one("#ladder-effort", Select))
        await wait_until(lambda: effort.value is Select.BLANK, timeout=1)
        await _choose(pilot, "#ladder-agent", "fake-copy")
        model = cast("Select[str]", app.query_one("#ladder-model", Select))
        await wait_until(
            lambda: model.value is Select.BLANK and effort.value is Select.BLANK,
            timeout=1,
        )
        await _click_button(pilot, "#wizard-cancel")


async def test_written_config_remains_truthful_when_doctor_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    _private_doctor_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    tmux = executable_dir / "tmux"
    tmux.write_text("#!/bin/sh\nexit 9\n")
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    app = await _app_with_agents(tmp_path, {"fake": create_fake_agent})

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _click_next(pilot, "#wizard-page-2")
        await _click_next(pilot, "#wizard-page-3")
        await _click_next(pilot, "#wizard-page-4")
        await _click_next(pilot, "#wizard-page-5")
        await _click_next(pilot, "#wizard-page-6")
        await _click_button(pilot, "#wizard-finish")
        config_path = tmp_path / ".shikiru" / "config.toml"
        await wait_until(config_path.is_file, timeout=3)
        report = app.query_one("#doctor-report", Static)
        await wait_until(lambda: "exit 9" in _doctor_text(report), timeout=60)
        config_row = app.query_one("#doctor-config", wizard_module.WizardDetailRow)
        config_value = app.query_one("#doctor-config .wizard-detail-value", Static)
        await wait_until(
            lambda: (
                config_row.display
                and config_row.is_on_screen
                and str(config_path.resolve()) in str(config_value.render())
            ),
            timeout=3,
        )
        result_button = app.query_one("#wizard-finish", Button)
        await wait_until(lambda: result_button.label == "Exit", timeout=3)
        await pilot.press("escape")
        await wait_until(lambda: app.is_running and result_button.display, timeout=1)
        await _click_button(pilot, "#wizard-finish")

    result = cast("InitWizardResult", app.return_value)
    assert result.config_path == config_path.resolve()
    assert result.doctor_report is not None
    assert not result.doctor_report.ok
    assert any(check.name == "tmux" and not check.ok for check in result.doctor_report.checks)


async def test_copy_on_write_probe_error_survives_navigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    await _git_project(tmp_path)
    monkeypatch.setattr(registry_module, "entry_points", _no_entry_points)
    app = await _app_with_cow_probe_error(tmp_path)

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        await _click_next(pilot, "#wizard-page-1")
        await _set_base_ladders(pilot)
        await _click_next(pilot, "#wizard-page-2")
        await _click_next(pilot, "#wizard-page-3")
        cow_result = app.query_one("#cow-result .wizard-detail-value", Static)
        await wait_until(lambda: "CoW check failed" in str(cow_result.render()), timeout=3)
        await _click_next(pilot, "#wizard-page-4")
        await _click_back(pilot, "#wizard-page-3")
        assert "shikiru-missing-cp" in str(cow_result.render())


async def test_init_wizard_cancellation_does_not_write_project_state(tmp_path: Path) -> None:
    await _git_project(tmp_path)
    app = await _app(tmp_path)

    async with app.run_test(size=WIZARD_SIZE) as pilot:
        frame = app.query_one("#wizard-frame")
        await wait_until(lambda: frame.visible, timeout=3)
        screen = app.screen
        for key in ("escape", "ctrl+q", "ctrl+p"):
            await pilot.press(key)
            await wait_until(
                lambda: app.is_running and app.screen is screen and frame.visible,
                timeout=1,
            )
        assert not (tmp_path / ".shikiru" / "config.toml").exists()
        await _click_button(pilot, "#wizard-cancel")

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
    await _git_project(tmp_path)
    if worktree_changed:
        (tmp_path / "tasks" / "plan.md").write_text("changed\n")
        (tmp_path / "notes.txt").write_text("untracked\n")
    app = await _app(tmp_path)

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
                timeout=3,
            )
        else:
            await wait_until(lambda: "clean" in str(checkout.render()), timeout=3)
        await _click_button(pilot, "#wizard-cancel")


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


async def test_init_creates_config_owner_only_from_the_start(tmp_path: Path) -> None:
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
