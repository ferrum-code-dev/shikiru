from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Rule, Select, Static

from shikiru.config.settings import MERGE_INTEGRATION, PULL_REQUEST_INTEGRATION
from shikiru.dashboard.ladder_editor import LADDER_ACTION_CSS, LadderConfiguration, LadderEditor
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError, ShikiruError
from shikiru.operator.detectors import (
    DetectedProject,
    InitWizardResult,
    ProjectDetectors,
)
from shikiru.ui.keys import key_hint
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
)
from shikiru.ui.theme import install_theme
from shikiru.ui.tokens import (
    ALERT_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.console import RenderableType
    from textual import events
    from textual.binding import BindingType

    from shikiru.agents.contracts import ModelCatalog
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
REPOSITORY_STEP_INDEX = WIZARD_STEPS.index(STEP_REPOSITORY)
AGENTS_STEP_INDEX = WIZARD_STEPS.index(STEP_AGENTS)
TRACKERS_STEP_INDEX = WIZARD_STEPS.index(STEP_TRACKERS)
COW_STEP_INDEX = WIZARD_STEPS.index(STEP_CACHES)
PARALLELISM_STEP_INDEX = WIZARD_STEPS.index(STEP_PARALLELISM)
INTEGRATION_STEP_INDEX = WIZARD_STEPS.index(STEP_INTEGRATION)
FINISH_STEP_INDEX = WIZARD_STEPS.index(STEP_FINISH)
INTEGRATION_OPTIONS = (
    ("Local merge", MERGE_INTEGRATION),
    ("Pull request", PULL_REQUEST_INTEGRATION),
)
WIZARD_TITLE = "SHIKIRU"
WIZARD_IDENTITY = "initialize"
LEGACY_CATALOG_DETAIL = "agent does not expose an authoritative model catalog"
WIZARD_NARROW_WIDTH = 74
WIZARD_COMPACT_WIDTH = 48
WIZARD_TINY_HEIGHT = 10
WIZARD_FRAME_MAX_WIDTH = 92
WIZARD_ART_WIDTH = 78
WIZARD_ART_HEIGHT = 14
WIZARD_ART_MIN_WIDTH = 80
WIZARD_ART_MIN_HEIGHT = 24
WIZARD_HINT = "Tab/Enter select · PgUp/PgDn scroll · Esc cancel"
WIZARD_COMPACT_HINT = "PgUp/PgDn scroll · Esc cancel"
PREFERRED_SIZE_COMPLEXITY = "low"
WIZARD_ART = """
                                     ..
        +###                        +###   ..++        +++++++++###+
        +###                  +++++++##########        +###########+.
        +###                  +######+####+...+.            .+###+.
        +###                  +++++++++#########          +####+
        +###         .+       ##############+..        .+###########++
        +##+         +##+         .++++++###+        +######+++++++####
        +##+        +###.      +##############       ###+++++++.    ###+
        +###.     .+###+      .###+       .+.         . .###+####. .###.
         #####+++#####.       .####++++++++##+          +###+++#######+
          ++#######+.          .+############+           ++########++
""".strip("\n")


def _step_label(step: str) -> str:
    return f"Step {WIZARD_STEPS.index(step) + 1} of {len(WIZARD_STEPS)} · {step}"


class InitWizardBody(VerticalScroll):
    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.scroll_to_widget(event.widget, animate=False, immediate=True)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._reveal_focused_control)

    def _reveal_focused_control(self) -> None:
        focused = self.screen.focused
        if focused is not None and self in focused.ancestors:
            self.scroll_to_widget(focused, animate=False, immediate=True, top=True)


class InitWizardApp(App[InitWizardResult | None]):
    """Guide an operator through project setup and show the final Doctor checks."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "cancel", "Cancel"),
        ("pageup", "scroll_body_up", "Scroll up"),
        ("pagedown", "scroll_body_down", "Scroll down"),
    ]
    CSS = (
        POPUP_CSS
        + LADDER_ACTION_CSS
        + """
    Screen {
        layers: art panel;
        align: center middle;
        background: $background;
        overflow: hidden;
    }
    #wizard-art {
        layer: art; position: absolute; offset: 0 0;
        width: 78; height: 14; color: $secondary-text; text-style: dim;
    }
    #wizard-frame {
        layer: panel; width: auto; min-width: 52; max-width: 92;
        height: auto; max-height: 100%; margin: 0; padding: 1 2;
        background: $surface; color: $primary-text;
    }
    #wizard-header { width: 100%; height: 1; }
    #wizard-title { width: 1fr; height: 1; color: $secondary-text; }
    #wizard-step { width: auto; height: 1; color: $primary-text; text-style: bold; }
    #wizard-body {
        height: auto; max-height: 1fr; min-height: 12; padding: 0;
        background: $surface; color: $primary-text;
        scrollbar-size-vertical: $scrollbar-width;
    }
    #wizard-frame.stable > #wizard-body { height: 1fr; min-height: 0; }
    .wizard-page { height: auto; padding: 0; }
    .wizard-page Label { height: auto; color: $secondary-text; }
    .wizard-page Input {
        width: 100%; height: 2; margin-bottom: 1; border: none;
        border-bottom: solid $neutral-border; padding: 0 1;
        background: $detail-surface; color: $primary-text;
    }
    .wizard-page Input:focus {
        border-bottom: solid $focus; background: $selected-surface; text-style: reverse;
    }
    .wizard-page Select { width: 100%; margin-bottom: 0; }
    #wizard-frame Select { height: 1; }
    #wizard-frame SelectCurrent {
        height: 1; border: none; border-left: solid $neutral-border;
        padding: 0 1; background: $surface;
    }
    #wizard-frame Select:focus > SelectCurrent,
    #wizard-frame Select.-expanded > SelectCurrent {
        border-left: solid $focus; background: $selected-surface; text-style: reverse;
    }
    #wizard-frame SelectOverlay { border: solid $neutral-border; }
    #wizard-frame SelectOverlay:focus { border: solid $focus; }
    .wizard-page Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
    }
    .wizard-page Static { height: auto; color: $primary-text; }
    #catalog-discovery, #wizard-error { height: auto; }
    #wizard-error { color: $dusty-rose; text-style: bold; }
    #wizard-navigation-hint { height: 1; color: $secondary-text; content-align: left middle; }
    #wizard-navigation { height: 1; min-height: 1; width: 100%; align: left middle; }
    #wizard-navigation Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
        border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-navigation Button:focus {
        color: $primary-text; background: $selected-surface; text-style: reverse;
        border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-navigation Button:disabled {
        color: $secondary-text;
        border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-frame.wizard-narrow #wizard-header { layout: vertical; height: auto; }
    #wizard-frame.wizard-narrow #wizard-title,
    #wizard-frame.wizard-narrow #wizard-step { width: 100%; }
    #wizard-frame.wizard-narrow { width: 100%; min-width: 0; padding: 0 1; }
    #wizard-frame.wizard-narrow.wizard-frame-clamped { width: 1fr; }
    #wizard-frame.wizard-frame-clamped { margin: 0 1; }
    #wizard-frame.wizard-compact { min-height: 0; padding: 0 1; }
    #wizard-frame.wizard-compact #wizard-rule { display: none; }
    #wizard-frame.wizard-compact > #wizard-body { height: 1fr; min-height: 0; }
    #wizard-frame.wizard-tiny #wizard-title { display: none; }
    #wizard-frame.wizard-tiny { padding: 0; }
    #wizard-frame.wizard-tiny #wizard-body { min-height: 0; }
    """
    )

    def __init__(
        self,
        *,
        project_root: Path,
        project: DetectedProject,
        detectors: ProjectDetectors,
        doctor: Doctor,
        doctor_renderer: Callable[..., RenderableType],
        orchestration_defaults: OrchestrationConfig,
    ) -> None:
        super().__init__()
        install_theme(self)
        self._root = project_root.resolve()
        self._project = project
        self._detectors = detectors
        self._doctor = doctor
        self._doctor_renderer = doctor_renderer
        self._orchestration_defaults = orchestration_defaults
        self._step = 0
        self._cow_supported: bool | None = None
        self._config_path: Path | None = None
        self._doctor_report: DoctorReport | None = None
        self._doctor_error: str | None = None
        self._error = ""
        self._model_catalogs: dict[str, ModelCatalog] = {}
        self._catalog_errors: dict[str, str] = {}
        self._catalogs_loaded = False
        self._frame_ready = False
        self._writing_config = False
        self._finishing = False
        self._last_focus_by_step: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        art = Text(WIZARD_ART, style=SECONDARY_TEXT_COLOR)
        yield Static(art, id="wizard-art", markup=False)
        with PopupFrame(widget_id="wizard-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS, id="wizard-header"):
                title = Text()
                title.append(WIZARD_TITLE, style=f"{STYLE_BOLD} {STEEL_BLUE}")
                title.append(f" · {WIZARD_IDENTITY}", style=SECONDARY_TEXT_COLOR)
                yield Static(title, id="wizard-title", classes=POPUP_TITLE_CLASS, markup=False)
                yield Static(_step_label(WIZARD_STEPS[self._step]), id="wizard-step", markup=False)
            yield Rule(classes=POPUP_RULE_CLASS, id="wizard-rule")
            with InitWizardBody(id="wizard-body", classes=POPUP_BODY_CLASS):
                yield from self._repository_page()
                with Vertical(id="wizard-page-1", classes="wizard-page"):
                    yield LadderEditor()
                yield from self._trackers_page()
                yield from self._caches_page()
                yield from self._parallelism_page()
                yield from self._integration_page()
                yield from self._finish_page()
                yield Static("", id="wizard-error", markup=False)
            yield Static(
                key_hint(WIZARD_HINT),
                id="wizard-navigation-hint",
            )
            with Horizontal(id="wizard-navigation"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", variant="primary", id="wizard-next")
                yield Button("Run doctor", variant="primary", id="wizard-finish")
                yield Button("Cancel", id="wizard-cancel")

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._adapt_to_viewport)

    def _adapt_to_viewport(self) -> None:
        self._set_compact_layout()
        self._place_art()
        self.query_one("#wizard-frame", PopupFrame).update_preferred_size()
        if self._config_path is not None:
            self.call_after_refresh(self._update_doctor_report)

    def on_mount(self) -> None:
        self.query_one("#wizard-frame", PopupFrame).visible = False
        self._update_pages()
        self._set_compact_layout()
        self._place_art()
        self.call_after_refresh(self._capture_agent_page_size)
        self.run_worker(self._load_model_catalogs(), group="model-catalogs", exclusive=True)

    def _capture_agent_page_size(self) -> None:
        editor = self.query_one("#ladder-editor", LadderEditor)
        editor.set_scope(Role.IMPLEMENTER, PREFERRED_SIZE_COMPLEXITY)
        self._step = AGENTS_STEP_INDEX
        self._update_pages()
        self.call_after_refresh(self._mark_frame_content_ready)

    def _mark_frame_content_ready(self) -> None:
        self.query_one("#wizard-frame", PopupFrame).content_ready()
        self.call_after_refresh(self._restore_repository_page)

    def _restore_repository_page(self) -> None:
        self.query_one("#ladder-editor", LadderEditor).set_scope(Role.LEADER)
        self._step = REPOSITORY_STEP_INDEX
        self._update_pages()
        self.call_after_refresh(self._enable_wizard)

    def _enable_wizard(self) -> None:
        self.query_one("#wizard-frame", PopupFrame).visible = True
        self._frame_ready = True
        self._update_pages()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        match button_id:
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
        if self._writing_config:
            return
        if self._config_path is None:
            self.exit(None)
        else:
            self.exit(self._result())

    def action_scroll_body_up(self) -> None:
        self.query_one("#wizard-body", InitWizardBody).action_page_up()

    def action_scroll_body_down(self) -> None:
        self.query_one("#wizard-body", InitWizardBody).action_page_down()

    async def _load_model_catalogs(self) -> None:
        for agent in self._project.agents:
            try:
                catalog = await self._detectors.model_catalog(agent, {})
            except (OSError, ShikiruError, ValueError) as error:
                self._catalog_errors[agent] = str(error) or type(error).__name__
                continue
            if catalog is None:
                self._catalog_errors[agent] = LEGACY_CATALOG_DETAIL
            elif catalog.error is not None:
                self._catalog_errors[agent] = catalog.error
            elif any(choice.offered for choice in catalog.models):
                self._model_catalogs[agent] = catalog
            else:
                self._catalog_errors[agent] = "agent reported no offered model identifiers"
        self.query_one("#catalog-discovery", Static).update(self._catalog_summary())
        self.query_one("#ladder-editor", LadderEditor).set_model_catalogs(
            self._model_catalogs, self._catalog_errors
        )
        self._catalogs_loaded = True
        self._update_pages()

    async def _move(self, direction: int) -> None:
        if self._config_path is not None or self._writing_config or self._finishing:
            return
        if direction > 0 and self._step == REPOSITORY_STEP_INDEX and not self._catalogs_loaded:
            return
        next_step = self._step + direction
        if not 0 <= next_step < len(WIZARD_STEPS):
            return
        if direction > 0 and self._step == AGENTS_STEP_INDEX:
            try:
                self._read_ladders()
            except ConfigError as error:
                self._show_error(str(error))
                return
        focused = self.focused
        if focused is not None and focused.id:
            self._last_focus_by_step[self._step] = focused.id
        self._error = ""
        if direction > 0 and next_step == COW_STEP_INDEX:
            await self._probe_cow()
        self._step = next_step
        self._update_pages()
        self.call_after_refresh(self._focus_step)

    def _focus_step(self) -> None:
        previous_id = self._last_focus_by_step.get(self._step)
        if previous_id:
            previous = self.query(f"#{previous_id}")
            if previous:
                previous.first().focus()
                return
        if self._step == AGENTS_STEP_INDEX:
            self.query_one("#ladder-role", Select).focus()
        elif self._step == TRACKERS_STEP_INDEX:
            self.query_one("#trackers", Input).focus()
        elif self._step == COW_STEP_INDEX:
            self.query_one("#caches", Input).focus()
        elif self._step == PARALLELISM_STEP_INDEX:
            self.query_one("#max-parallel", Input).focus()
        elif self._step == INTEGRATION_STEP_INDEX:
            self.query_one("#integration", Select).focus()
        elif self._step == FINISH_STEP_INDEX:
            self.query_one("#wizard-finish", Button).focus()

    async def _probe_cow(self) -> None:
        try:
            self._cow_supported = await self._detectors.check_cow_support()
        except (OSError, ShikiruError) as error:
            self._error = f"CoW check failed: {error}"
            self.query_one("#cow-result", Static).update(self._error)

    async def _finish(self) -> None:
        if self._finishing:
            return
        self._finishing = True
        self.query_one("#wizard-finish", Button).disabled = True
        try:
            ladders = self._read_ladders()
            trackers = self._read_paths("trackers")
            caches = self._read_paths("caches")
            max_parallel = self._read_parallelism()
            integration = self._read_integration()
            self._writing_config = True
            config_path = await self._detectors.initialize_config(
                trackers=trackers,
                caches=caches,
                ladders=ladders.ladders,
                complexity=ladders.complexity,
                max_parallel=max_parallel,
                integration=integration,
            )
            self._config_path = config_path.resolve()
            self._writing_config = False
            self._doctor_report = await self._doctor.run()
            self._doctor_error = None
            self._error = ""
        except (OSError, ShikiruError, ValueError) as error:
            self._writing_config = False
            if self._config_path is None:
                self._show_error(str(error))
            else:
                self._doctor_error = f"Doctor could not complete: {error}"
                self._error = self._doctor_error
        finally:
            self._finishing = False
        self._update_pages()

    def _result(self) -> InitWizardResult:
        if self._config_path is None:
            message = "wizard result requested before config was written"
            raise RuntimeError(message)
        return InitWizardResult(self._config_path, self._doctor_report, self._doctor_error)

    def _read_ladders(self) -> LadderConfiguration:
        return self.query_one("#ladder-editor", LadderEditor).configuration()

    def _read_paths(self, field: str) -> tuple[str, ...]:
        value = self.query_one(f"#{field}", Input).value
        if not value.strip():
            return ()
        entries = tuple(item.strip() for item in value.split(","))
        if any(not item for item in entries):
            message = "remove empty comma-separated entries"
            raise ConfigError(message)
        return entries

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

    def _show_error(self, message: str) -> None:
        self._error = message
        error = self.query_one("#wizard-error", Static)
        error.update(message)
        error.display = True
        self.query_one("#wizard-body", InitWizardBody).scroll_to_widget(
            error, animate=False, immediate=True
        )

    def _update_pages(self) -> None:
        self.query_one("#wizard-step", Static).update(_step_label(WIZARD_STEPS[self._step]))
        for index in range(len(WIZARD_STEPS)):
            self.query_one(f"#wizard-page-{index}").display = index == self._step
        self.query_one("#wizard-body", InitWizardBody).scroll_home(animate=False)
        back = self.query_one("#wizard-back", Button)
        back.disabled = self._step == 0 or self._config_path is not None or self._finishing
        next_button = self.query_one("#wizard-next", Button)
        next_button.display = self._step < FINISH_STEP_INDEX and self._config_path is None
        next_button.disabled = (
            (self._step == REPOSITORY_STEP_INDEX and not self._catalogs_loaded)
            or not self._frame_ready
            or self._finishing
            or self._writing_config
        )
        finish_button = self.query_one("#wizard-finish", Button)
        finish_button.display = self._step == FINISH_STEP_INDEX or self._config_path is not None
        finish_button.label = "Exit" if self._config_path is not None else self._finish_label()
        finish_button.disabled = self._finishing
        if self.focused is next_button and not next_button.display:
            finish_button.focus()
        cancel = self.query_one("#wizard-cancel", Button)
        cancel.disabled = self._writing_config
        cow_result = self.query_one("#cow-result", Static)
        if self._cow_supported is not None:
            cow_result.update(
                "Copy-on-write supported on this project filesystem."
                if self._cow_supported
                else "Copy-on-write is unavailable; Shikiru will skip cache copies."
            )
        self.query_one("#wizard-error", Static).update(self._error)
        self.query_one("#wizard-error", Static).display = bool(self._error)
        if self._config_path is None:
            self._update_doctor_report()
        else:
            self.call_after_refresh(self._update_doctor_report)
        self._set_compact_layout()

    def _update_doctor_report(self) -> None:
        report_widget = self.query_one("#doctor-report", Static)
        if self._config_path is None:
            report_widget.update("")
            return
        width = self.query_one("#wizard-body", InitWizardBody).content_region.width
        if width < 1:
            return
        path = Text(f"Config: {self._config_path}", style=PRIMARY_TEXT_COLOR)
        if self._doctor_report is not None:
            report_widget.update(
                Group(path, self._doctor_renderer(self._doctor_report, width=width))
            )
        else:
            detail = self._doctor_error or "Doctor did not return a report."
            report_widget.update(Group(path, Text(detail, style=ALERT_COLOR)))

    def _set_compact_layout(self) -> None:
        frame = self.query_one("#wizard-frame", PopupFrame)
        if self.size.width <= WIZARD_FRAME_MAX_WIDTH:
            frame.add_class("wizard-frame-clamped")
        else:
            frame.remove_class("wizard-frame-clamped")
        if self.size.width < WIZARD_NARROW_WIDTH:
            frame.add_class("wizard-narrow")
        else:
            frame.remove_class("wizard-narrow")
        if self.size.width < WIZARD_COMPACT_WIDTH:
            frame.add_class("wizard-compact")
            self.query_one("#wizard-navigation-hint", Static).update(key_hint(WIZARD_COMPACT_HINT))
            self.query_one("#wizard-next", Button).label = "Next"
            self.query_one("#wizard-finish", Button).label = (
                "Exit" if self._config_path is not None else "Doctor"
            )
        else:
            frame.remove_class("wizard-compact")
            self.query_one("#wizard-navigation-hint", Static).update(key_hint(WIZARD_HINT))
            self.query_one("#wizard-finish", Button).label = (
                "Exit" if self._config_path is not None else "Run doctor"
            )
        if self.size.height < WIZARD_TINY_HEIGHT:
            frame.add_class("wizard-tiny")
        else:
            frame.remove_class("wizard-tiny")
        art = self.query_one("#wizard-art", Static)
        art.display = (
            self.size.width >= WIZARD_ART_MIN_WIDTH and self.size.height >= WIZARD_ART_MIN_HEIGHT
        )

    def _place_art(self) -> None:
        art = self.query_one("#wizard-art", Static)
        art.styles.offset = (
            max(self.size.width - WIZARD_ART_WIDTH, 0),
            max(self.size.height - WIZARD_ART_HEIGHT, 0),
        )

    def _finish_label(self) -> str:
        return "Doctor" if self.size.width < WIZARD_COMPACT_WIDTH else "Run doctor"

    def _catalog_summary(self) -> str:
        if self._model_catalogs:
            summary = "Available model choices: " + ", ".join(self._model_catalogs)
        else:
            summary = "No detected agent reported selectable model choices."
        if self._catalog_errors:
            unavailable = "; ".join(
                f"{agent}: {detail}" for agent, detail in self._catalog_errors.items()
            )
            summary = f"{summary}. Discovery unavailable for {unavailable}."
        return summary

    def _repository_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-0", classes="wizard-page"):
            yield Static(f"Project root: {self._root}", markup=False)
            yield Static(f"Git base branch: {self._project.base}", markup=False)
            yield Static("The project root is the top level of this repository.", markup=False)
            yield Static("Discovering supported agent model choices…", id="catalog-discovery")

    def _trackers_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-2", classes="wizard-page"):
            yield Static(
                "Detected Markdown trackers; edit the comma-separated paths.", markup=False
            )
            yield Input(value=", ".join(self._project.trackers), id="trackers")

    def _caches_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-3", classes="wizard-page"):
            yield Static(
                "Detected ignored cache directories; edit the glob patterns.", markup=False
            )
            yield Input(value=", ".join(self._project.caches), id="caches")
            yield Static("Checking copy-on-write on this project filesystem…", id="cow-result")

    def _parallelism_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-4", classes="wizard-page"):
            yield Label("Maximum parallel tasks")
            yield Input(value=str(self._orchestration_defaults.max_parallel), id="max-parallel")

    def _integration_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-5", classes="wizard-page"):
            yield Label("Task integration strategy")
            yield Select(
                INTEGRATION_OPTIONS,
                value=self._orchestration_defaults.integration,
                allow_blank=False,
                id="integration",
            )

    def _finish_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-6", classes="wizard-page"):
            yield Static(
                "Finish writes .shikiru/config.toml, excludes .shikiru/ in Git, and runs Doctor.",
                id="wizard-finish-hint",
                markup=False,
            )
            yield Static("", id="doctor-report", markup=False)
