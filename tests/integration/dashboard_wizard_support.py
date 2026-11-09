from __future__ import annotations

import asyncio
from io import StringIO
from typing import TYPE_CHECKING, cast

from fake_agent.agent import create as create_fake_agent
from rich.console import Console
from textual.errors import NoWidget
from textual.widgets import Button, Input, Select, Static

import shikiru.dashboard.wizard as wizard_module
from shikiru.agents.contracts import AgentContext, CheckResult, ModelCatalog, ModelChoice, Rung
from shikiru.agents.registry import AgentRegistry
from shikiru.agents.settings import AgentOptionsError, AgentSetting, SettingKind
from shikiru.bootstrap import build_doctor, build_project_detectors, run_init_wizard
from shikiru.cli.render import doctor_renderable
from shikiru.config.loader import load_default_orchestration, load_settings
from shikiru.dashboard.wizard import InitWizardApp
from shikiru.domain.ladder import pick_rung
from shikiru.domain.tasks import Role
from shikiru.operator.detectors import ProjectDetectors
from shikiru.operator.doctor import Doctor
from shikiru.operator.plugin_checks import PluginChecks
from shikiru.operator.probe import AgentProbe
from shikiru.process import SubprocessRunner
from shikiru.workspace.caches import CacheCloner
from shikiru.workspace.git import Git
from tests.support.clock import LONG_AGO, FakeClock
from tests.support.dashboard import focus
from tests.support.factories import make_task
from tests.support.git_repo import CACHE_COPY_TIMEOUT, GIT_COMMAND_TIMEOUT
from tests.support.waiting import (
    LONG_WAIT_SECONDS,
    MEDIUM_WAIT_SECONDS,
    UI_WAIT_SECONDS,
    wait_until,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from textual.pilot import Pilot

    from shikiru.agents.contracts import Agent, AgentFactory
    from shikiru.config.settings import OrchestrationConfig
    from shikiru.operator.detectors import InitWizardResult


WIZARD_SIZE = (100, 40)


COMPACT_WIZARD_SIZE = (40, 12)


TRANSIENT_SMALL_SIZE = (40, 8)


WIZARD_TIMER_TEST_SECONDS = 1.5


MODEL_WITH_LITERAL_BRACKETS = "claude-fable-5-1[1m]"


PICKY_REJECTED_DEPTH = 13


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
    "#integration": ("merge", "squash", "pr"),
    "#agent-setting-0-1": ("", "api", "subscription"),
}


LADDER_SCOPES = (
    "Leader",
    "Implementer · Base",
    "Implementer · Low",
    "Implementer · Normal",
    "Implementer · High",
    "Helper",
)


class LegacyAgent:
    name = "legacy"

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="available")]


class CatalogWithoutHeadlessAgent:
    name = "legacy"

    async def doctor(self, models: Sequence[str]) -> list[CheckResult]:
        del models
        return [CheckResult(name="binary", ok=True, detail="available")]

    async def model_catalog(self) -> ModelCatalog:
        return ModelCatalog(models=(ModelChoice("listed-model", ()),), complete=True)


def create_legacy_agent(_context: AgentContext) -> Agent:
    return cast("Agent", LegacyAgent())


def create_catalog_without_headless_agent(_context: AgentContext) -> Agent:
    return cast("Agent", CatalogWithoutHeadlessAgent())


def no_entry_points(*, group: str) -> list[object]:
    del group
    return []


async def git_project(root: Path) -> None:
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    (root / "tasks").mkdir()
    (root / "tasks" / "plan.md").write_text("plan\n")
    (root / ".gitignore").write_text("node_modules/\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "cache.dat").write_text("cache\n")
    runner = SubprocessRunner()
    await runner.run(["git", "init", "-q"], timeout=MEDIUM_WAIT_SECONDS, cwd=root, check=True)
    await runner.run(
        ["git", "add", ".gitignore", "tasks/plan.md"],
        timeout=MEDIUM_WAIT_SECONDS,
        cwd=root,
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
            "initial",
        ],
        timeout=MEDIUM_WAIT_SECONDS,
        cwd=root,
        check=True,
    )


async def build_wizard_app(root: Path) -> InitWizardApp:
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


async def app_with_agents(
    root: Path,
    agents: Mapping[str, AgentFactory],
    *,
    orchestration_defaults: OrchestrationConfig | None = None,
) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = FakeClock(LONG_AGO)
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
        orchestration_defaults=(
            load_default_orchestration()
            if orchestration_defaults is None
            else orchestration_defaults
        ),
    )


async def app_with_cow_probe_error(root: Path) -> InitWizardApp:
    runner = SubprocessRunner()
    clock = FakeClock(LONG_AGO)
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


LADDER_AGENT_SELECTOR = "#ladder-agent"
LADDER_MODEL_SELECTOR = "#ladder-model"
LADDER_EFFORT_SELECTOR = "#ladder-effort"
LADDER_FOCUS_SUCCESSORS = {
    LADDER_AGENT_SELECTOR: LADDER_MODEL_SELECTOR,
    LADDER_MODEL_SELECTOR: LADDER_EFFORT_SELECTOR,
}


async def replace_input(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    target = pilot.app.query_one(selector, Input)
    await focus(pilot, target)
    target.value = value
    target.cursor_position = len(value)
    await pilot.pause()


async def choose(pilot: Pilot[InitWizardResult | None], selector: str, value: str) -> None:
    select = cast("Select[str]", pilot.app.query_one(selector, Select))
    await focus(pilot, select)
    await pilot.press("enter")
    overlay = select.query_one("SelectOverlay")
    await wait_until(
        lambda: select.expanded and pilot.app.focused is overlay,
        timeout=UI_WAIT_SECONDS,
    )
    options = selection_order(pilot, selector)
    selected = "" if select.value is Select.BLANK else select.value
    difference = options.index(value) - options.index(selected)
    direction = "down" if difference > 0 else "up"
    await pilot.press(*(direction for _ in range(abs(difference))))
    await pilot.press("enter")
    await wait_until(lambda: select.value == value, timeout=UI_WAIT_SECONDS)
    await wait_until(lambda: not select.expanded, timeout=UI_WAIT_SECONDS)
    await _wait_for_ladder_focus_to_advance(pilot, selector)


async def _wait_for_ladder_focus_to_advance(
    pilot: Pilot[InitWizardResult | None], selector: str
) -> None:
    successor_selector = LADDER_FOCUS_SUCCESSORS.get(selector)
    if successor_selector is None:
        return
    successor = cast("Select[str]", pilot.app.query_one(successor_selector, Select))
    skippable = selector == LADDER_MODEL_SELECTOR
    await wait_until(
        lambda: pilot.app.focused is successor or (skippable and successor.disabled),
        timeout=UI_WAIT_SECONDS,
    )


def selection_order(pilot: Pilot[InitWizardResult | None], selector: str) -> tuple[str, ...]:
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


async def click_button(pilot: Pilot[InitWizardResult | None], selector: str) -> None:
    button = pilot.app.query_one(selector, Button)
    await wait_until(lambda: not button.has_class("-active"), timeout=UI_WAIT_SECONDS)
    body = pilot.app.query_one("#wizard-body")
    body.scroll_to_widget(button, animate=False, immediate=True, top=True)
    await click_ready_button(pilot, button)


async def click_ready_button(pilot: Pilot[InitWizardResult | None], button: Button) -> None:
    await pilot.pause()

    def ready() -> bool:
        region = button.region
        x, y = region.x + region.width // 2, region.y
        if button.disabled or not button.visible or not pilot.app.screen.region.contains(x, y):
            return False
        try:
            widget, _ = pilot.app.get_widget_at(x, y)
        except NoWidget:
            return False
        return widget is button

    await wait_until(ready, timeout=UI_WAIT_SECONDS)
    await pilot.click(button, offset=(button.region.width // 2, 0))


async def click_next(
    pilot: Pilot[InitWizardResult | None], expected_page: str | None = None
) -> None:
    button = pilot.app.query_one("#wizard-next", Button)
    await wait_until(lambda: not button.disabled, timeout=LONG_WAIT_SECONDS)
    await wait_until(
        lambda: (
            button.region.width > 0
            and button.region.height > 0
            and button.region.right <= pilot.app.size.width
            and button.region.bottom <= pilot.app.size.height
        ),
        timeout=UI_WAIT_SECONDS,
    )
    await wait_until(lambda: not button.has_class("-active"), timeout=UI_WAIT_SECONDS)
    await click_ready_button(pilot, button)
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
            timeout=LONG_WAIT_SECONDS,
        )
        assert page.display, str(error.render()) or str(ladder_problem.render())
        await pilot.pause()


async def click_back(pilot: Pilot[InitWizardResult | None], expected_page: str) -> None:
    button = pilot.app.query_one("#wizard-back", Button)
    await wait_until(lambda: not button.has_class("-active"), timeout=UI_WAIT_SECONDS)
    await click_ready_button(pilot, button)
    await wait_until(lambda: pilot.app.query_one(expected_page).display, timeout=UI_WAIT_SECONDS)
    await pilot.pause()


async def assign_rung(
    pilot: Pilot[InitWizardResult | None],
    *,
    agent: str,
    model: str,
    effort: str | None,
) -> None:
    await choose(pilot, "#ladder-agent", agent)
    await choose(pilot, "#ladder-model", model)
    if effort is not None:
        await choose(pilot, "#ladder-effort", effort)
    await click_button(pilot, "#ladder-add")
    field = pilot.app.query_one("#ladder-input", Input)
    await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)


async def set_role(pilot: Pilot[InitWizardResult | None], role: str) -> None:
    scope_by_role = {
        "leader": LADDER_SCOPES[0],
        "implementer": LADDER_SCOPES[1],
        "helper": LADDER_SCOPES[-1],
    }
    scope = scope_by_role[role]
    await set_scope(pilot, scope)


async def set_scope(pilot: Pilot[InitWizardResult | None], scope: str) -> None:
    row_list = pilot.app.query_one("#ladder-scopes", wizard_module.RowList)
    await focus(pilot, row_list)
    target = LADDER_SCOPES.index(scope)
    distance = target - row_list.selected
    if distance:
        await pilot.press(*(("down" if distance > 0 else "up",) * abs(distance)))
    await wait_until(
        lambda: (
            row_list.selected == target
            and scope in str(pilot.app.query_one("#ladder-input-title", Static).render())
        ),
        timeout=UI_WAIT_SECONDS,
    )


async def set_scope_text(
    pilot: Pilot[InitWizardResult | None],
    scope: str,
    value: str,
) -> None:
    await set_scope(pilot, scope)
    await replace_input(pilot, "#ladder-input", value)


BASE_RUNG_TEXT = "fake:smoke:low"


async def set_base_ladders(pilot: Pilot[InitWizardResult | None]) -> None:
    for scope in (LADDER_SCOPES[0], LADDER_SCOPES[1], LADDER_SCOPES[-1]):
        await set_scope_text(pilot, scope, BASE_RUNG_TEXT)


async def configure_leader_ladder(pilot: Pilot[InitWizardResult | None]) -> None:
    await set_scope(pilot, LADDER_SCOPES[0])
    await assign_rung(pilot, agent="fake-copy", model="strong", effort="high")
    await assign_rung(pilot, agent="fake", model="default", effort=None)


async def configure_implementer_ladder(pilot: Pilot[InitWizardResult | None]) -> None:
    await set_scope(pilot, LADDER_SCOPES[1])
    await assign_rung(pilot, agent="fake-copy", model="smoke", effort="low")
    await assign_rung(pilot, agent="fake", model="strong", effort="high")


async def configure_complexity_ladders(pilot: Pilot[InitWizardResult | None]) -> None:
    await set_scope(pilot, LADDER_SCOPES[2])
    await assign_rung(pilot, agent="fake", model="compact-visible", effort="high")
    await set_scope(pilot, LADDER_SCOPES[3])
    await wait_until(lambda: app_field_value(pilot) == "", timeout=UI_WAIT_SECONDS)
    assert app_field_value(pilot) == ""
    await set_scope_text(pilot, LADDER_SCOPES[4], "fake-copy:strong:high")


async def configure_helper_rung(pilot: Pilot[InitWizardResult | None]) -> None:
    await set_scope(pilot, LADDER_SCOPES[-1])
    await assign_rung(
        pilot,
        agent="fake-copy",
        model=MODEL_WITH_LITERAL_BRACKETS,
        effort="high",
    )
    await assign_rung(pilot, agent="fake", model="default", effort="high")


def app_field_value(pilot: Pilot[InitWizardResult | None]) -> str:
    return pilot.app.query_one("#ladder-input", Input).value


def doctor_text(report: Static) -> str:
    output = StringIO()
    Console(file=output, width=max(1, report.content_region.width), color_system=None).print(
        report.content
    )
    return output.getvalue()


def private_doctor_runtime(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("TMPDIR", str(runtime))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


async def resize(pilot: Pilot[InitWizardResult | None], size: tuple[int, int]) -> None:
    await pilot.resize_terminal(*size)
    await wait_until(
        lambda: pilot.app.size.width == size[0] and pilot.app.size.height == size[1],
        timeout=UI_WAIT_SECONDS,
    )


async def exercise_candidate_readd(
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
    await focus(pilot, candidate_list)
    await pilot.press("enter")
    await wait_until(lambda: field.value == candidate, timeout=UI_WAIT_SECONDS)
    await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)
    add_button = pilot.app.query_one(f"#{input_id}-candidate-add-0", Button)
    await wait_until(lambda: add_button.disabled, timeout=UI_WAIT_SECONDS)
    await pilot.press("enter")
    await wait_until(lambda: field.value == candidate, timeout=UI_WAIT_SECONDS)
    await replace_input(pilot, f"#{input_id}", manual_path)
    await wait_until(lambda: not add_button.disabled, timeout=UI_WAIT_SECONDS)
    await focus(pilot, add_button)
    await pilot.press("enter")
    await wait_until(lambda: field.value == f"{manual_path}, {candidate}", timeout=UI_WAIT_SECONDS)
    await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)
    additional_button = pilot.app.query_one(f"#{input_id}-candidate-add-1", Button)
    await wait_until(lambda: not additional_button.disabled, timeout=UI_WAIT_SECONDS)
    await focus(pilot, additional_button)
    await pilot.press("enter")
    await wait_until(
        lambda: field.value == f"{manual_path}, {candidate}, {additional_candidate}",
        timeout=UI_WAIT_SECONDS,
    )
    await wait_until(lambda: pilot.app.focused is field, timeout=UI_WAIT_SECONDS)
    await wait_until(
        lambda: add_button.disabled and additional_button.disabled,
        timeout=UI_WAIT_SECONDS,
    )
    await focus(pilot, candidate_list)
    await pilot.press("enter")
    await wait_until(
        lambda: field.value == f"{manual_path}, {candidate}, {additional_candidate}",
        timeout=UI_WAIT_SECONDS,
    )


async def verify_back_navigation_and_fill_project_fields(
    pilot: Pilot[InitWizardResult | None],
) -> None:
    await resize(pilot, (40, 40))
    await resize(pilot, WIZARD_SIZE)
    await resize(pilot, (100, 12))
    await resize(pilot, WIZARD_SIZE)
    await click_next(pilot, "#wizard-page-2")
    await exercise_candidate_readd(
        pilot,
        list_id=wizard_module.TRACKERS_LIST_ID,
        input_id=wizard_module.TRACKERS_INPUT_ID,
        candidate="tasks/plan.md",
        manual_path="docs/todo.md",
        additional_candidate=LONG_TRACKER_CANDIDATE,
    )
    await click_back(pilot, "#wizard-page-1")
    await resize(pilot, COMPACT_WIZARD_SIZE)
    await click_next(pilot, "#wizard-page-2")
    await resize(pilot, WIZARD_SIZE)
    await click_next(pilot, "#wizard-page-3")
    await exercise_candidate_readd(
        pilot,
        list_id=wizard_module.CACHES_LIST_ID,
        input_id=wizard_module.CACHES_INPUT_ID,
        candidate="node_modules",
        manual_path="build-cache",
        additional_candidate=LONG_CACHE_CANDIDATE,
    )
    await click_next(pilot, "#wizard-page-4")
    await replace_input(pilot, "#max-parallel", "0")
    await replace_input(pilot, "#max-iterations", "7")
    await replace_input(pilot, "#push-interval", "900")
    await replace_input(pilot, "#max-quota-wait", "600")
    await replace_input(pilot, "#max-cpu", "90")
    await replace_input(pilot, "#min-free-ram", "2.5")
    await click_next(pilot, "#wizard-page-5")
    await click_next(pilot, "#wizard-page-6")
    await choose(pilot, "#integration", "squash")
    await click_next(pilot, "#wizard-page-7")
    summary = str(pilot.app.query_one("#wizard-summary .wizard-detail-value", Static).render())
    assert summary.endswith("Local squash and merge")


async def wait_for_orchestration_error(
    pilot: Pilot[InitWizardResult | None], input_id: str
) -> None:
    field = pilot.app.query_one(f"#{input_id}", Input)
    await wait_until(
        lambda: (
            pilot.app.query_one("#wizard-page-4").display
            and pilot.app.focused is field
            and bool(str(pilot.app.query_one("#wizard-error", Static).render()).strip())
        ),
        timeout=UI_WAIT_SECONDS,
    )


async def advance_to_orchestration(pilot: Pilot[InitWizardResult | None]) -> None:
    await click_next(pilot, "#wizard-page-1")
    await set_base_ladders(pilot)
    await click_next(pilot, "#wizard-page-2")
    await click_next(pilot, "#wizard-page-3")
    await click_next(pilot, "#wizard-page-4")


async def advance_orchestration_to_finish(pilot: Pilot[InitWizardResult | None]) -> None:
    await click_next(pilot, "#wizard-page-5")
    await click_next(pilot, "#wizard-page-6")
    await click_next(pilot, "#wizard-page-7")


def input_value(pilot: Pilot[InitWizardResult | None], selector: str) -> str:
    return pilot.app.query_one(selector, Input).value


def assert_written_ladders(tmp_path: Path, result: InitWizardResult) -> None:
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


def assert_written_project_fields(tmp_path: Path, result: InitWizardResult) -> None:
    assert result.doctor_report is not None
    assert result.doctor_report.ok
    settings = load_settings(tmp_path)
    assert settings.project.trackers == [
        "docs/todo.md",
        "tasks/plan.md",
        LONG_TRACKER_CANDIDATE,
    ]
    assert settings.repo.caches == ["build-cache", "node_modules", LONG_CACHE_CANDIDATE]
    assert settings.orchestration.max_parallel == 3
    assert settings.orchestration.max_iterations == 7
    assert settings.orchestration.integration == "squash"
    assert settings.orchestration.push_interval_seconds == 900
    assert settings.orchestration.timeouts.max_quota_wait == 600
    assert settings.orchestration.resources.max_cpu_pct == 90
    assert settings.orchestration.resources.min_free_ram_gb == 2.5
    assert "/.shikiru/" in (tmp_path / ".git" / "info" / "exclude").read_text()


async def initialize(entrypoint: str, root: Path, detectors: ProjectDetectors) -> None:
    if entrypoint == "wizard":
        await run_init_wizard(root)
    else:
        await detectors.initialize()


class PickyAgent(LegacyAgent):
    name = "picky"

    def settings(self) -> tuple[AgentSetting, ...]:
        return (
            AgentSetting(
                key="first",
                label="First",
                help="A first option.",
                kind=SettingKind.NUMBER,
                default=1,
                minimum=0,
            ),
            AgentSetting(
                key="custom.depth",
                label="Depth",
                help="How deep.",
                kind=SettingKind.NUMBER,
                default=1,
                minimum=0,
            ),
        )


def create_picky_agent(context: AgentContext) -> Agent:
    if context.options.get("custom.depth") == PICKY_REJECTED_DEPTH:
        message = "depth 13 is unlucky"
        raise AgentOptionsError(message, "custom.depth")
    return cast("Agent", PickyAgent())


def create_unwelcoming_agent(context: AgentContext) -> Agent:
    if context.options.get("first") == PICKY_REJECTED_DEPTH:
        message = "this combination is unsupported"
        raise AgentOptionsError(message)
    return cast("Agent", PickyAgent())


async def advance_to_agent_settings(pilot: Pilot[InitWizardResult | None]) -> None:
    await advance_to_orchestration(pilot)
    await click_next(pilot, "#wizard-page-5")


async def finish_from_agent_settings(pilot: Pilot[InitWizardResult | None]) -> None:
    await click_next(pilot, "#wizard-page-6")
    await click_next(pilot, "#wizard-page-7")
    await click_button(pilot, "#wizard-finish")


async def wait_for_finish_then_exit(pilot: Pilot[InitWizardResult | None]) -> None:
    finish_button = pilot.app.query_one("#wizard-finish", Button)
    await wait_until(lambda: finish_button.label == "Exit", timeout=UI_WAIT_SECONDS)
    await click_button(pilot, "#wizard-finish")


async def wait_for_agent_setting_error(pilot: Pilot[InitWizardResult | None], input_id: str) -> str:
    field = pilot.app.query_one(f"#{input_id}")
    error = pilot.app.query_one("#wizard-error", Static)
    await wait_until(
        lambda: (
            pilot.app.query_one("#wizard-page-5").display
            and pilot.app.focused is field
            and bool(str(error.render()).strip())
        ),
        timeout=UI_WAIT_SECONDS,
    )
    return str(error.render())
