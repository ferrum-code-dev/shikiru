from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

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

if TYPE_CHECKING:
    from pathlib import Path

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


def _step_label(step: str) -> str:
    return f"Step {WIZARD_STEPS.index(step) + 1} of {len(WIZARD_STEPS)} · {step}"


class InitWizardApp(App[InitWizardResult | None]):
    """Guide an operator through project setup and show the final Doctor checks."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = """
    Screen { height: 80%; }
    #wizard-shell { height: 100%; padding: 0 1; }
    #wizard-title { height: 1; content-align: center middle; }
    #wizard-body { height: 1fr; border: round $accent; }
    .wizard-page { height: auto; padding: 1; }
    .wizard-page Label { height: 1; }
    .wizard-page Input, .wizard-page Select { width: 100%; margin-bottom: 1; }
    .wizard-page Static { height: auto; }
    #wizard-error { color: $error; height: auto; max-height: 3; }
    #wizard-navigation { height: 3; align: center middle; }
    #wizard-navigation Button { min-width: 12; margin: 0 1; }
    """

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
        with Vertical(id="wizard-shell"):
            yield Label(WIZARD_TITLE, id="wizard-title")
            with VerticalScroll(id="wizard-body"):
                yield from self._repository_page()
                yield from self._agents_page()
                yield from self._trackers_page()
                yield from self._caches_page()
                yield from self._parallelism_page()
                yield from self._integration_page()
                yield from self._finish_page()
            yield Static("", id="wizard-error", markup=False)
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
        self._config_path = config_path
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
                message = f"{role} ladder must contain at least one rung"
                raise ConfigError(message)
            rungs = tuple(parse_rung(entry) for entry in entries)
            unknown_agents = sorted({rung.agent for rung in rungs} - allowed_agents)
            if unknown_agents:
                names = ", ".join(unknown_agents)
                message = f"{role} ladder uses plugins that did not pass detection: {names}"
                raise ConfigError(message)
            if role == Role.HELPER.value and len(rungs) != 1:
                message = "helper ladder must contain exactly one rung"
                raise ConfigError(message)
            ladders[role] = rungs
        return ladders

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

    def _doctor_summary(self) -> str:
        if self._doctor_report is None:
            if self._config_path is not None:
                return (
                    f"Configuration written to {self._config_path}. Doctor did not return a report."
                )
            return ""
        heading = DOCTOR_SUCCESS if self._doctor_report.ok else DOCTOR_FAILURE
        checks = "\n".join(
            f"{'PASS' if check.ok else 'FAIL'} · {check.name}: {check.detail}"
            for check in self._doctor_report.checks
        )
        return f"{heading}\nConfig: {self._config_path}\n{checks}"

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
            yield Static("Checking copy-on-write on this project filesystem…", id="cow-result")

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
