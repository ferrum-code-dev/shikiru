from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from shikiru.agents.contracts import Rung, parse_rung
from shikiru.config.settings import MERGE_INTEGRATION, PULL_REQUEST_INTEGRATION
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError, ShikiruError
from shikiru.operator.detectors import (
    DetectedProject,
    InitWizardResult,
    ProjectDetectors,
)
from shikiru.ui.keys import key_hint
from shikiru.ui.popup import POPUP_BUTTON_CSS, POPUP_CONTROL_CSS
from shikiru.ui.theme import install_theme
from shikiru.ui.tokens import (
    ALERT_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from pathlib import Path

    from textual import events
    from textual.binding import BindingType

    from shikiru.config.settings import OrchestrationConfig
    from shikiru.operator.doctor import Doctor, DoctorReport

STEP_REPOSITORY = "Repository"
STEP_AGENTS = "Agents and ladders"
STEP_TRACKERS = "Trackers"
STEP_CACHES = "Caches and CoW"
STEP_PARALLELISM = "Parallelism"
STEP_INTEGRATION = "Integration mode"
STEP_FINISH = "Finish"
WIZARD_STEPS = (
    STEP_REPOSITORY,
    STEP_AGENTS,
    STEP_TRACKERS,
    STEP_CACHES,
    STEP_PARALLELISM,
    STEP_INTEGRATION,
    STEP_FINISH,
)
WIZARD_ROLES = (Role.LEADER.value, Role.IMPLEMENTER.value, Role.HELPER.value)
COW_STEP_INDEX = WIZARD_STEPS.index(STEP_CACHES)
INTEGRATION_OPTIONS = (
    ("Local merge", MERGE_INTEGRATION),
    ("Pull request", PULL_REQUEST_INTEGRATION),
)
WIZARD_TITLE = "Initialize Shikiru"
DOCTOR_SUCCESS = "Doctor passed."
DOCTOR_FAILURE = "Doctor found issues; review the checks below."
WIZARD_TITLE_MINIMUM_HEIGHT = 9


def _step_label(step: str) -> str:
    return (
        f"Step {WIZARD_STEPS.index(step) + 1} of {len(WIZARD_STEPS)} · {step[0].lower()}{step[1:]}"
    )


class InitWizardBody(VerticalScroll):
    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._reveal_focused_control)

    def _reveal_focused_control(self) -> None:
        focused = self.screen.focused
        if focused is not None and self in focused.ancestors:
            self.scroll_to_widget(focused, animate=False)


class InitWizardShell(Vertical):
    def on_mount(self) -> None:
        self._set_compact_height()

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._set_compact_height()

    def _set_compact_height(self) -> None:
        if self.screen.size.height < WIZARD_TITLE_MINIMUM_HEIGHT:
            self.add_class("wizard-tiny")
        else:
            self.remove_class("wizard-tiny")


class InitWizardApp(App[InitWizardResult | None]):
    """Guide an operator through project setup and show the final Doctor checks."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = (
        POPUP_BUTTON_CSS
        + POPUP_CONTROL_CSS
        + """
    Screen { width: 100%; height: 100%; align: center middle; background: $background; }
    #wizard-shell {
        width: 100%; max-width: 100; height: 100%; min-height: 0; padding: 0 1;
        background: $background; color: $primary-text;
    }
    #wizard-title {
        height: 1; color: $steel-blue; text-style: bold;
        content-align: center middle;
    }
    #wizard-body {
        height: 1fr; min-height: 0; border: solid $neutral-border; padding: 0 1;
        background: $surface; color: $primary-text;
        scrollbar-size-vertical: $scrollbar-width;
    }
    .wizard-page { height: auto; padding: 0 1; }
    .wizard-page Label { height: auto; color: $secondary-text; }
    .wizard-page Input {
        width: 100%; height: 2; margin-bottom: 1; border: none;
        border-bottom: solid $neutral-border; padding: 0 1;
        background: $surface; color: $primary-text;
    }
    .wizard-page Input:focus {
        border-bottom: solid $focus; background: $selected-surface; text-style: reverse;
    }
    .wizard-page Select { width: 100%; margin-bottom: 1; }
    .wizard-page Static { height: auto; color: $primary-text; }
    #wizard-finish-hint, #cow-result { color: $secondary-text; }
    #wizard-error {
        height: auto; margin-top: 1; color: $dusty-rose; text-style: bold;
    }
    #wizard-navigation-hint {
        height: 1; color: $secondary-text; content-align: center middle;
    }
    #wizard-shell.wizard-tiny #wizard-title { display: none; }
    #wizard-navigation { height: 1; min-height: 1; align: center middle; }
    #wizard-navigation Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0; padding: 0 1; border: none;
    }
    #wizard-navigation Button:focus {
        color: $primary-text; background: $selected-surface; text-style: reverse;
    }
    """
    )

    def __init__(
        self,
        *,
        project_root: Path,
        project: DetectedProject,
        detectors: ProjectDetectors,
        doctor: Doctor,
        orchestration_defaults: OrchestrationConfig,
    ) -> None:
        """Keep the detected project and injected services for the seven setup steps."""
        super().__init__()
        install_theme(self)
        self._root = project_root.resolve()
        self._project = project
        self._detectors = detectors
        self._doctor = doctor
        self._orchestration_defaults = orchestration_defaults
        self._step = 0
        self._cow_supported: bool | None = None
        self._config_path: Path | None = None
        self._doctor_report: DoctorReport | None = None
        self._error = ""

    def compose(self) -> ComposeResult:
        with InitWizardShell(id="wizard-shell", classes="popup-frame"):
            yield Label(WIZARD_TITLE, id="wizard-title")
            with InitWizardBody(id="wizard-body"):
                yield from self._repository_page()
                yield from self._agents_page()
                yield from self._trackers_page()
                yield from self._caches_page()
                yield from self._parallelism_page()
                yield from self._integration_page()
                yield from self._finish_page()
            yield Static("", id="wizard-error", markup=False)
            yield Static(
                key_hint("Tab move · Enter activate · Esc cancel"),
                id="wizard-navigation-hint",
            )
            with Horizontal(id="wizard-navigation"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", variant="primary", id="wizard-next")
                yield Button("Run doctor", variant="primary", id="wizard-finish")
                yield Button("Cancel", id="wizard-cancel")

    def on_mount(self) -> None:
        self._update_pages()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "wizard-back":
                await self._move(-1)
            case "wizard-next":
                await self._move(1)
            case "wizard-finish":
                if self._config_path is None:
                    await self._finish()
                else:
                    self.exit(self._result())
            case "wizard-cancel":
                self.action_cancel()
            case _:
                return

    def action_cancel(self) -> None:
        if self._config_path is None:
            self.exit(None)
        else:
            self.exit(self._result())

    async def _move(self, direction: int) -> None:
        if self._config_path is not None:
            return
        next_step = self._step + direction
        if not 0 <= next_step < len(WIZARD_STEPS):
            return
        if direction > 0 and self._step == 1 and not self._read_ladders():
            return
        self._error = ""
        if direction > 0 and next_step == COW_STEP_INDEX:
            await self._probe_cow()
        self._step = next_step
        self._update_pages()

    async def _probe_cow(self) -> None:
        try:
            self._cow_supported = await self._detectors.check_cow_support()
        except (OSError, ShikiruError) as error:
            self._error = f"CoW check failed: {error}"
            self.query_one("#cow-result", Static).update(self._error)

    async def _finish(self) -> None:
        ladders = self._read_ladders()
        if ladders is None:
            return
        try:
            trackers = self._read_paths("trackers")
            caches = self._read_paths("caches")
            max_parallel = self._read_parallelism()
            integration = self._read_integration()
            config_path = await self._detectors.initialize_config(
                trackers=trackers,
                caches=caches,
                ladders=ladders,
                max_parallel=max_parallel,
                integration=integration,
            )
        except (OSError, ShikiruError, ValueError) as error:
            self._error = str(error)
            self._update_pages()
            return
        self._config_path = config_path.resolve()
        try:
            self._doctor_report = await self._doctor.run()
            self._error = ""
        except (OSError, ShikiruError, ValueError) as error:
            self._error = f"Doctor could not complete: {error}"
        self._update_pages()

    def _result(self) -> InitWizardResult:
        if self._config_path is None:
            message = "wizard result requested before config was written"
            raise RuntimeError(message)
        return InitWizardResult(self._config_path, self._doctor_report, self._error or None)

    def _read_ladders(self) -> dict[str, tuple[Rung, ...]] | None:
        try:
            ladders = self._parse_ladders()
        except (ConfigError, ValueError) as error:
            self._error = str(error)
            self._update_error()
            return None
        self._error = ""
        self._update_error()
        return ladders

    def _parse_ladders(self) -> dict[str, tuple[Rung, ...]]:
        allowed_agents = set(self._project.agents)
        ladders: dict[str, tuple[Rung, ...]] = {}
        for role in WIZARD_ROLES:
            raw = self.query_one(f"#{role}-ladder", Input).value
            entries = self._split_entries(raw)
            if not entries:
                self._focus_ladder(role)
                message = f"{role} ladder must contain at least one rung"
                raise ConfigError(message)
            try:
                rungs = tuple(parse_rung(entry) for entry in entries)
            except ConfigError, ValueError:
                self._focus_ladder(role)
                raise
            unknown_agents = sorted({rung.agent for rung in rungs} - allowed_agents)
            if unknown_agents:
                self._focus_ladder(role)
                names = ", ".join(unknown_agents)
                message = f"{role} ladder uses plugins that did not pass detection: {names}"
                raise ConfigError(message)
            if role == Role.HELPER.value and len(rungs) != 1:
                self._focus_ladder(role)
                message = "helper ladder must contain exactly one rung"
                raise ConfigError(message)
            ladders[role] = rungs
        return ladders

    def _focus_ladder(self, role: str) -> None:
        field = self.query_one(f"#{role}-ladder", Input)
        field.focus()
        body = self.query_one("#wizard-body", InitWizardBody)
        body.scroll_to_widget(field, animate=False)

    def _read_paths(self, field: str) -> tuple[str, ...]:
        value = self.query_one(f"#{field}", Input).value
        return self._split_entries(value)

    def _read_parallelism(self) -> int:
        raw = self.query_one("#max-parallel", Input).value.strip()
        try:
            value = int(raw)
        except ValueError as error:
            message = "max parallel must be a positive whole number"
            raise ConfigError(message) from error
        if value <= 0:
            message = "max parallel must be a positive whole number"
            raise ConfigError(message)
        return value

    def _read_integration(self) -> str:
        value = cast("Select[str]", self.query_one("#integration", Select)).value
        if not isinstance(value, str) or value not in {
            MERGE_INTEGRATION,
            PULL_REQUEST_INTEGRATION,
        }:
            message = "choose an integration mode"
            raise ConfigError(message)
        return value

    def _update_pages(self) -> None:
        for index in range(len(WIZARD_STEPS)):
            self.query_one(f"#wizard-page-{index}").display = index == self._step
        self.query_one("#wizard-body", InitWizardBody).scroll_home(animate=False)
        back = self.query_one("#wizard-back", Button)
        back.disabled = self._step == 0 or self._config_path is not None
        next_button = self.query_one("#wizard-next", Button)
        next_button.display = self._step < len(WIZARD_STEPS) - 1 and self._config_path is None
        finish_button = self.query_one("#wizard-finish", Button)
        finish_button.display = self._step == len(WIZARD_STEPS) - 1 or self._config_path is not None
        finish_button.label = "Exit" if self._config_path is not None else "Run doctor"
        if self.focused is next_button and not next_button.display:
            finish_button.focus()
        cow_result = self.query_one("#cow-result", Static)
        if self._cow_supported is not None:
            cow_result.update(
                "Copy-on-write supported on this project filesystem."
                if self._cow_supported
                else "Copy-on-write is unavailable; Shikiru will skip cache copies."
            )
        self.query_one("#doctor-report", Static).update(self._doctor_summary())
        self._update_error()

    def _doctor_summary(self) -> Text:
        summary = Text()
        if self._doctor_report is None:
            if self._config_path is not None:
                summary.append(
                    f"Configuration written to {self._config_path}. "
                    "Doctor did not return a report.",
                    style=PRIMARY_TEXT_COLOR,
                )
            return summary
        heading = DOCTOR_SUCCESS if self._doctor_report.ok else DOCTOR_FAILURE
        heading_color = SUCCESS_COLOR if self._doctor_report.ok else ALERT_COLOR
        summary.append(heading, style=f"{STYLE_BOLD} {heading_color}")
        summary.append("\nConfig: ", style=SECONDARY_TEXT_COLOR)
        summary.append(str(self._config_path), style=PRIMARY_TEXT_COLOR)
        for check in self._doctor_report.checks:
            outcome = "PASS" if check.ok else "FAIL"
            outcome_color = SUCCESS_COLOR if check.ok else ALERT_COLOR
            summary.append("\n")
            summary.append(outcome, style=f"{STYLE_BOLD} {outcome_color}")
            summary.append(" · ", style=SECONDARY_TEXT_COLOR)
            summary.append(check.name, style=PRIMARY_TEXT_COLOR)
            summary.append(": ", style=SECONDARY_TEXT_COLOR)
            summary.append(check.detail, style=PRIMARY_TEXT_COLOR)
        return summary

    def _update_error(self) -> None:
        error = self.query_one("#wizard-error", Static)
        error.update(self._error)
        error.display = bool(self._error)

    def _repository_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-0", classes="wizard-page"):
            yield Label(_step_label(STEP_REPOSITORY))
            yield Static(f"Project root: {self._root}", markup=False)
            yield Static(f"Git base branch: {self._project.base}", markup=False)
            yield Static("The project root is the top level of this repository.", markup=False)

    def _agents_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-1", classes="wizard-page"):
            yield Label(_step_label(STEP_AGENTS))
            yield Static(
                f"Detected plugins that passed doctor(): {', '.join(self._project.agents)}",
                markup=False,
            )
            yield Static("Enter an explicit model supported by each selected plugin.")
            for role in WIZARD_ROLES:
                yield Label(f"{role.title()} ladder (comma-separated agent:model:effort)")
                yield Input(
                    value=f"{self._project.agents[0]}:",
                    placeholder="agent:model:effort",
                    id=f"{role}-ladder",
                )

    def _trackers_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-2", classes="wizard-page"):
            yield Label(_step_label(STEP_TRACKERS))
            yield Static(
                "Detected Markdown trackers; edit the comma-separated paths.", markup=False
            )
            yield Input(value=", ".join(self._project.trackers), id="trackers")

    def _caches_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-3", classes="wizard-page"):
            yield Label(_step_label(STEP_CACHES))
            yield Static(
                "Detected ignored cache directories; edit the glob patterns.", markup=False
            )
            yield Input(value=", ".join(self._project.caches), id="caches")
            yield Static(
                "Checking copy-on-write on this project filesystem…",
                id="cow-result",
                markup=False,
            )

    def _parallelism_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-4", classes="wizard-page"):
            yield Label(_step_label(STEP_PARALLELISM))
            yield Label("Maximum parallel tasks")
            yield Input(value=str(self._orchestration_defaults.max_parallel), id="max-parallel")

    def _integration_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-5", classes="wizard-page"):
            yield Label(_step_label(STEP_INTEGRATION))
            yield Label("Task integration strategy")
            yield Select(
                INTEGRATION_OPTIONS,
                value=self._orchestration_defaults.integration,
                allow_blank=False,
                id="integration",
            )

    def _finish_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-6", classes="wizard-page"):
            yield Label(_step_label(STEP_FINISH))
            yield Static(
                "Finish writes .shikiru/config.toml, excludes .shikiru/ in Git, and runs Doctor.",
                id="wizard-finish-hint",
                markup=False,
            )
            yield Static("", id="doctor-report", markup=False)

    @staticmethod
    def _split_entries(value: str) -> tuple[str, ...]:
        if not value.strip():
            return ()
        entries = tuple(item.strip() for item in value.split(","))
        if any(not item for item in entries):
            message = "remove empty comma-separated entries"
            raise ConfigError(message)
        return entries
