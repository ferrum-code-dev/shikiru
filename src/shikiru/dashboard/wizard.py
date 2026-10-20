from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Rule, Select, Static

from shikiru.agents.contracts import ModelCatalog, ModelChoice, Rung, assess_model_choice
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
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.console import RenderableType
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
EDITABLE_ROLES = (Role.LEADER.value, Role.IMPLEMENTER.value)
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
NATIVE_EFFORT_VALUE = ""
NATIVE_EFFORT_LABEL = "Native default"
WIZARD_TITLE = "SHIKIRU"
WIZARD_IDENTITY = "initialize"
MODEL_CATALOG_GUIDANCE = (
    "Install or repair the agent CLI, then restart init, or choose an available agent."
)
LEGACY_CATALOG_DETAIL = "agent does not expose an authoritative model catalog"
RUNG_ACTION_PARTS = 2
RUNG_ACTION_ADD = "add"
RUNG_ACTION_REMOVE = "remove"
RUNG_ACTION_UP = "up"
RUNG_ACTION_DOWN = "down"
WIZARD_NARROW_WIDTH = 55


def _step_label(step: str) -> str:
    return (
        f"Step {WIZARD_STEPS.index(step) + 1} of {len(WIZARD_STEPS)} · {step[0].lower()}{step[1:]}"
    )


def _rung_control_id(role: str, index: int, field: str) -> str:
    return f"rung-{role}-{index}-{field}"


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


class InitWizardShell(Vertical):
    def on_mount(self) -> None:
        self._set_narrow_layout()
        self.limit_body_height()
        self.call_after_refresh(self.limit_body_height)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._set_narrow_layout()
        self.call_after_refresh(self.limit_body_height)

    def _set_narrow_layout(self) -> None:
        if self.screen.size.width < WIZARD_NARROW_WIDTH:
            self.add_class("wizard-narrow")
        else:
            self.remove_class("wizard-narrow")

    def limit_body_height(self) -> None:
        """Keep the page body within the available terminal height."""
        body = self.query_one("#wizard-body", InitWizardBody)
        app = cast("InitWizardApp", self.app)
        fixed_height = sum(
            child.region.height + child.styles.margin.height
            for child in self.children
            if child is not body and child.display
        )
        available = max(
            0,
            app.size.height
            - self.styles.margin.height
            - self.styles.gutter.height
            - self.styles.padding.height
            - fixed_height,
        )
        maximum = body.styles.max_height
        if maximum is None or maximum.cells != available:
            body.styles.max_height = available


class LadderRung(Vertical):
    def __init__(
        self,
        *,
        role: str,
        index: int,
        total: int,
        rung: Rung,
        agent_options: tuple[tuple[RenderableType, str], ...],
        model_options: tuple[tuple[RenderableType, str], ...],
        effort_options: tuple[tuple[RenderableType, str], ...],
    ) -> None:
        super().__init__(id=f"rung-{role}-{index}", classes="ladder-rung")
        self._role = role
        self._index = index
        self._total = total
        self._rung = rung
        self._agent_options = agent_options
        self._model_options = model_options
        self._effort_options = effort_options

    def compose(self) -> ComposeResult:
        with Horizontal(classes="rung-heading"):
            yield Label(f"{self._role.title()} rung {self._index + 1}")
            if self._role in EDITABLE_ROLES:
                if self._index == 0:
                    yield Button("Add", id=f"{self._role}-{RUNG_ACTION_ADD}")
                yield Button(
                    "↑",
                    id=f"rung-{self._role}-{self._index}-{RUNG_ACTION_UP}",
                    disabled=self._index == 0,
                )
                yield Button(
                    "↓",
                    id=f"rung-{self._role}-{self._index}-{RUNG_ACTION_DOWN}",
                    disabled=self._index + 1 == self._total,
                )
                yield Button("Remove", id=f"rung-{self._role}-{self._index}-{RUNG_ACTION_REMOVE}")
        with Horizontal(classes="rung-fields"):
            with Horizontal(classes="rung-field"):
                yield Label("Agent")
                yield Select(
                    self._agent_options,
                    value=self._rung.agent,
                    allow_blank=False,
                    id=_rung_control_id(self._role, self._index, "agent"),
                )
            with Horizontal(classes="rung-field rung-model-field"):
                yield Label("Model")
                yield Select(
                    self._model_options,
                    value=self._rung.model or Select.BLANK,
                    allow_blank=True,
                    prompt="Choose model",
                    id=_rung_control_id(self._role, self._index, "model"),
                )
            with Horizontal(classes="rung-field"):
                yield Label("Effort")
                yield Select(
                    self._effort_options,
                    value=(
                        self._rung.effort
                        if self._rung.model and self._rung.effort is not None
                        else Select.BLANK
                    ),
                    allow_blank=True,
                    prompt=(NATIVE_EFFORT_LABEL if self._rung.model else "Choose a model first"),
                    disabled=not self._rung.model,
                    id=_rung_control_id(self._role, self._index, "effort"),
                )
        if self._index > 0:
            yield Rule(classes="rung-rule")


class InitWizardApp(App[InitWizardResult | None]):
    """Guide an operator through project setup and show the final Doctor checks."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = (
        POPUP_BUTTON_CSS
        + POPUP_CONTROL_CSS
        + """
    Screen {
        width: 100%; height: auto; max-height: 100%; align: left top;
        background: $background;
    }
    #wizard-shell {
        width: 100%; max-width: 100; height: auto; max-height: 100%; min-height: 0;
        padding: 0 1; background: $background; color: $primary-text;
    }
    #wizard-title { height: 1; color: $secondary-text; }
    #wizard-rule { height: 1; margin: 0; color: $neutral-border; }
    #wizard-step { height: 1; margin-bottom: 0; color: $primary-text; text-style: bold; }
    #wizard-body {
        height: auto; max-height: 1; min-height: 0; padding: 0;
        background: $background; color: $primary-text;
        scrollbar-size-vertical: $scrollbar-width;
    }
    .wizard-page { height: auto; padding: 0; }
    .wizard-page Label { height: auto; color: $secondary-text; }
    .wizard-page Input {
        width: 100%; height: 2; margin-bottom: 1; border: none;
        border-bottom: solid $neutral-border; padding: 0 1;
        background: $surface; color: $primary-text;
    }
    #max-parallel { width: 12; }
    .wizard-page Input:focus {
        border-bottom: solid $focus; background: $selected-surface; text-style: reverse;
    }
    .wizard-page Select { width: 100%; margin-bottom: 0; }
    #wizard-shell Select { height: 1; }
    #wizard-shell SelectCurrent {
        height: 1; border: none; border-left: solid $neutral-border;
        padding: 0 1; background: $surface;
    }
    #wizard-shell Select:focus > SelectCurrent,
    #wizard-shell Select.-expanded > SelectCurrent {
        border-left: solid $focus; background: $selected-surface; text-style: reverse;
    }
    #wizard-shell SelectOverlay { border: solid $neutral-border; }
    #wizard-shell SelectOverlay:focus { border: solid $focus; }
    .wizard-page Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
    }
    .wizard-page Static { height: auto; color: $primary-text; }
    #agent-choice-status, #wizard-finish-hint, #cow-result { color: $secondary-text; }
    #agent-choice-status { margin-bottom: 1; }
    #agent-choice-guidance { color: $secondary-text; margin-bottom: 1; }
    .ladder-role { height: auto; margin-bottom: 0; }
    .ladder-rungs { height: auto; }
    .ladder-rung { height: auto; padding: 0; }
    .rung-heading { height: 1; align: left middle; }
    .rung-heading Label { width: 1fr; color: $steel-blue; text-style: bold; }
    .rung-heading Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0; padding: 0 1; border: none;
    }
    .rung-heading Button:focus {
        color: $primary-text; background: $selected-surface; text-style: reverse;
    }
    .rung-fields { height: auto; width: 100%; }
    .rung-field { width: 1fr; height: 1; padding: 0 1 0 0; align: left middle; }
    .rung-model-field { width: 2fr; }
    .rung-field Label { width: 7; height: 1; color: $secondary-text; }
    .rung-field Select { width: 1fr; }
    .rung-rule { height: 1; margin: 0; color: $neutral-border; }
    #wizard-error {
        height: auto; margin-top: 1; color: $dusty-rose; text-style: bold;
    }
    #wizard-navigation-hint { height: 1; color: $secondary-text; content-align: left middle; }
    #wizard-navigation { height: 1; min-height: 1; width: 100%; align: left middle; }
    #wizard-navigation Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
    }
    #wizard-navigation Button:focus {
        color: $primary-text; background: $selected-surface; text-style: reverse;
    }
    #wizard-shell.wizard-narrow .rung-fields {
        layout: vertical;
    }
    #wizard-shell.wizard-narrow .rung-field {
        width: 100%; height: 1; padding: 0;
    }
    #wizard-shell.wizard-narrow .rung-model-field { width: 100%; }
    #wizard-shell.wizard-narrow .rung-field Label { width: 7; }
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
        """Keep the detected project and injected services for the seven setup steps."""
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
        self._ladder_drafts: dict[str, list[Rung]] = {role: [] for role in WIZARD_ROLES}
        self._catalogs_loaded = False

    def compose(self) -> ComposeResult:
        with InitWizardShell(id="wizard-shell"):
            yield Static(self._identity(), id="wizard-title", markup=False)
            yield Static(_step_label(WIZARD_STEPS[self._step]), id="wizard-step", markup=False)
            yield Rule(id="wizard-rule")
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
                key_hint("Tab move · Enter select · Esc cancel"),
                id="wizard-navigation-hint",
            )
            with Horizontal(id="wizard-navigation"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", variant="primary", id="wizard-next")
                yield Button("Run doctor", variant="primary", id="wizard-finish")
                yield Button("Cancel", id="wizard-cancel")

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._config_path is not None:
            self.call_after_refresh(self._update_doctor_report)

    def on_mount(self) -> None:
        self._update_pages()
        self.run_worker(self._load_model_catalogs(), group="model-catalogs", exclusive=True)

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
                await self._handle_ladder_action(button_id)

    async def on_select_changed(self, event: Select.Changed) -> None:
        control_id = event.select.id or ""
        if (
            not control_id.startswith("rung-")
            or self.query_one(f"#{control_id}", Select) is not event.select
            or event.value != event.select.value
        ):
            return
        for role, rungs in self._ladder_drafts.items():
            for index, rung in enumerate(rungs):
                if control_id == _rung_control_id(role, index, "agent"):
                    self._refresh_agent_choice(
                        role, index, self._selection_value(event.value), rung
                    )
                    return
                if control_id == _rung_control_id(role, index, "model"):
                    self._refresh_model_choice(
                        role, index, self._selection_value(event.value), rung
                    )
                    return
                if control_id == _rung_control_id(role, index, "effort"):
                    self._store_rung(
                        role, index, rung.agent, rung.model, self._selection_value(event.value)
                    )
                    return

    def action_cancel(self) -> None:
        if self._config_path is None:
            self.exit(None)
        else:
            self.exit(self._result())

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
            elif self._offered_choices(catalog):
                self._model_catalogs[agent] = catalog
            else:
                self._catalog_errors[agent] = "agent reported no offered model identifiers"
        self.query_one("#agent-choice-status", Static).update(self._catalog_summary())
        self.query_one("#agent-choice-guidance", Static).display = bool(self._catalog_errors)
        self.query_one("#agent-choice-guidance", Static).update(MODEL_CATALOG_GUIDANCE)
        self._catalogs_loaded = True
        self.query_one("#catalog-discovery", Static).update("Supported model choices are ready.")
        for role in WIZARD_ROLES:
            rung = self._default_rung()
            await self._render_ladder(role, [rung] if rung is not None else [])
        for role in EDITABLE_ROLES:
            self.query_one(f"#{role}-add", Button).disabled = not self._model_catalogs
        self._update_pages()

    def _default_rung(self) -> Rung | None:
        agent = next(iter(self._model_catalogs), None)
        if agent is None:
            return None
        return Rung(agent=agent, model="", effort=None)

    def _catalog_summary(self) -> str:
        available = ", ".join(self._model_catalogs)
        if available:
            summary = f"Available model choices: {available}. Listed identifiers can be selected."
        else:
            summary = "No detected agent reported selectable model choices."
        if self._catalog_errors:
            unavailable = "; ".join(
                f"{agent}: {detail}" for agent, detail in self._catalog_errors.items()
            )
            summary = f"{summary} Discovery unavailable for {unavailable}."
        return summary

    async def _handle_ladder_action(self, button_id: str) -> None:
        if button_id.endswith(f"-{RUNG_ACTION_ADD}"):
            await self._add_ladder_rung(button_id.removesuffix(f"-{RUNG_ACTION_ADD}"))
            return
        await self._change_ladder(button_id)

    async def _add_ladder_rung(self, role: str) -> None:
        if role not in EDITABLE_ROLES:
            return
        rungs = self._capture_ladder(role)
        rung = self._default_rung()
        if rung is not None:
            rungs.append(rung)
            await self._render_ladder(role, rungs, focus_index=len(rungs) - 1)

    async def _change_ladder(self, button_id: str) -> None:
        for role in EDITABLE_ROLES:
            prefix = f"rung-{role}-"
            if not button_id.startswith(prefix):
                continue
            parts = button_id.removeprefix(prefix).split("-")
            if len(parts) != RUNG_ACTION_PARTS or not parts[0].isdigit():
                return
            index = int(parts[0])
            action = parts[1]
            rungs = self._capture_ladder(role)
            if index >= len(rungs):
                return
            if action == RUNG_ACTION_REMOVE:
                rungs.pop(index)
                focus_index = min(index, len(rungs) - 1)
            elif action == RUNG_ACTION_UP and index > 0:
                rungs[index - 1], rungs[index] = rungs[index], rungs[index - 1]
                focus_index = index - 1
            elif action == RUNG_ACTION_DOWN and index + 1 < len(rungs):
                rungs[index + 1], rungs[index] = rungs[index], rungs[index + 1]
                focus_index = index + 1
            else:
                return
            await self._render_ladder(role, rungs, focus_index=focus_index)
            return

    async def _render_ladder(
        self, role: str, rungs: list[Rung], *, focus_index: int | None = None
    ) -> None:
        container = self.query_one(f"#{role}-rungs", Vertical)
        await container.remove_children()
        self._ladder_drafts[role] = list(rungs)
        if not rungs:
            empty_label = (
                "Add a rung to choose a supported agent and model."
                if role in EDITABLE_ROLES
                else "No supported helper agent and model choices are available."
            )
            empty_children: list[Static | Button] = [Static(empty_label, markup=False)]
            if role in EDITABLE_ROLES:
                empty_children.append(Button("Add", id=f"{role}-{RUNG_ACTION_ADD}"))
            await container.mount(*empty_children)
            if focus_index is not None and role in EDITABLE_ROLES:
                self.call_after_refresh(self._focus_add, role)
            return
        await container.mount(
            *(
                LadderRung(
                    role=role,
                    index=index,
                    total=len(rungs),
                    rung=rung,
                    agent_options=self._agent_options(),
                    model_options=self._model_options(rung.agent),
                    effort_options=self._effort_options(rung.agent, rung.model),
                )
                for index, rung in enumerate(rungs)
            )
        )
        if focus_index is not None and 0 <= focus_index < len(rungs):
            self.call_after_refresh(self._focus_rung, role, focus_index, "agent")

    def _focus_add(self, role: str) -> None:
        self.query_one(f"#{role}-add", Button).focus()

    def _focus_rung(self, role: str, index: int, field: str) -> None:
        control = cast(
            "Select[str]", self.query_one(f"#{_rung_control_id(role, index, field)}", Select)
        )
        control.focus()
        self.query_one("#wizard-body", InitWizardBody).scroll_to_widget(
            control, animate=False, immediate=True
        )

    def _refresh_agent_choice(self, role: str, index: int, agent: str, previous: Rung) -> None:
        if agent == previous.agent or agent not in self._model_catalogs:
            return
        model_options = self._model_options(agent)
        model_select = cast(
            "Select[str]", self.query_one(f"#{_rung_control_id(role, index, 'model')}", Select)
        )
        effort_select = cast(
            "Select[str]", self.query_one(f"#{_rung_control_id(role, index, 'effort')}", Select)
        )
        model_select.set_options(model_options)
        model_select.value = Select.BLANK
        effort_select.prompt = "Choose a model first"
        effort_select.set_options(())
        effort_select.disabled = True
        effort_select.value = Select.BLANK
        self._store_rung(role, index, agent, "", NATIVE_EFFORT_VALUE)

    def _refresh_model_choice(self, role: str, index: int, model: str, previous: Rung) -> None:
        if not model:
            effort_select = cast(
                "Select[str]", self.query_one(f"#{_rung_control_id(role, index, 'effort')}", Select)
            )
            effort_select.prompt = "Choose a model first"
            effort_select.set_options(())
            effort_select.disabled = True
            effort_select.value = Select.BLANK
            self._store_rung(role, index, previous.agent, "", "")
            return
        effort_values = {value for _, value in self._effort_options(previous.agent, model)}
        effort = previous.effort if previous.effort in effort_values else None
        effort_select = cast(
            "Select[str]", self.query_one(f"#{_rung_control_id(role, index, 'effort')}", Select)
        )
        effort_select.prompt = NATIVE_EFFORT_LABEL
        effort_select.set_options(self._effort_options(previous.agent, model))
        effort_select.disabled = False
        effort_select.value = effort if effort is not None else Select.BLANK
        self._store_rung(role, index, previous.agent, model, effort or NATIVE_EFFORT_VALUE)

    def _store_rung(self, role: str, index: int, agent: str, model: str, effort: str) -> None:
        rungs = self._ladder_drafts[role]
        if index < len(rungs):
            rungs[index] = Rung(
                agent=agent,
                model=model,
                effort=effort or None,
            )

    def _capture_ladder(self, role: str) -> list[Rung]:
        rungs = self._ladder_drafts[role]
        captured: list[Rung] = []
        for index, _ in enumerate(rungs):
            agent = self._select_value(role, index, "agent")
            model = self._select_value(role, index, "model")
            effort = self._select_value(role, index, "effort")
            captured.append(Rung(agent=agent, model=model, effort=effort or None))
        self._ladder_drafts[role] = captured
        return captured

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
        ladders: dict[str, tuple[Rung, ...]] = {}
        for role in WIZARD_ROLES:
            rungs = self._capture_ladder(role)
            if not rungs:
                self._focus_ladder(role, 0, "agent")
                message = f"{role} ladder must contain at least one supported rung"
                raise ConfigError(message)
            for index, rung in enumerate(rungs):
                catalog = self._model_catalogs.get(rung.agent)
                if catalog is None:
                    self._focus_ladder(role, index, "agent")
                    detail = self._catalog_errors.get(
                        rung.agent, "no authoritative choices are available"
                    )
                    message = f"{role} rung {index + 1}: {detail}"
                    raise ConfigError(message)
                choices = {choice.model for choice in self._offered_choices(catalog)}
                if rung.model not in choices:
                    self._focus_ladder(role, index, "model")
                    message = f"{role} rung {index + 1}: choose a listed model"
                    raise ConfigError(message)
                assessment = assess_model_choice(rung.model, rung.effort, catalog)
                if assessment.supported is not True:
                    self._focus_ladder(role, index, "effort")
                    message = f"{role} rung {index + 1}: {assessment.detail}"
                    raise ConfigError(message)
            if role == Role.HELPER.value and len(rungs) != 1:
                self._focus_ladder(role, 0, "agent")
                message = "helper ladder must contain exactly one rung"
                raise ConfigError(message)
            ladders[role] = tuple(rungs)
        return ladders

    def _focus_ladder(self, role: str, index: int, field: str) -> None:
        if index < len(self._ladder_drafts[role]):
            self._focus_rung(role, index, field)
        else:
            self.query_one(f"#{role}-add", Button).focus()

    def _select_value(self, role: str, index: int, field: str) -> str:
        control = cast(
            "Select[str]", self.query_one(f"#{_rung_control_id(role, index, field)}", Select)
        )
        value = control.value
        if value is Select.BLANK:
            return ""
        if not isinstance(value, str):
            message = f"{role} rung {index + 1} needs a supported {field} selection"
            raise ConfigError(message)
        return value

    @staticmethod
    def _selection_value(value: object) -> str:
        return "" if value is Select.BLANK else str(value)

    def _agent_options(self) -> tuple[tuple[RenderableType, str], ...]:
        return tuple((Text(agent), agent) for agent in self._model_catalogs)

    def _model_options(self, agent: str) -> tuple[tuple[RenderableType, str], ...]:
        return tuple(
            (Text(choice.model, style=PRIMARY_TEXT_COLOR), choice.model)
            for choice in self._offered_choices(self._model_catalogs[agent])
        )

    def _effort_options(self, agent: str, model: str) -> tuple[tuple[RenderableType, str], ...]:
        if not model:
            return ()
        choice = next(
            item
            for item in self._offered_choices(self._model_catalogs[agent])
            if item.model == model
        )
        return tuple(
            (Text(effort, style=PRIMARY_TEXT_COLOR), effort) for effort in choice.efforts or ()
        )

    @staticmethod
    def _offered_choices(catalog: ModelCatalog) -> tuple[ModelChoice, ...]:
        return tuple(choice for choice in catalog.models if choice.offered)

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

    async def _move(self, direction: int) -> None:
        if self._config_path is not None:
            return
        if direction > 0 and self._step == 0 and not self._catalogs_loaded:
            return
        next_step = self._step + direction
        if not 0 <= next_step < len(WIZARD_STEPS):
            return
        if direction > 0 and self._step == AGENTS_STEP_INDEX and not self._read_ladders():
            return
        self._error = ""
        if direction > 0 and next_step == COW_STEP_INDEX:
            await self._probe_cow()
        self._step = next_step
        self._update_pages()
        self.call_after_refresh(self._focus_step)

    def _focus_step(self) -> None:
        if self._step == AGENTS_STEP_INDEX:
            if self._ladder_drafts[Role.LEADER.value]:
                self._focus_rung(Role.LEADER.value, 0, "agent")
            else:
                self.query_one("#leader-add", Button).focus()
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
            self._doctor_error = None
            self._error = ""
        except (OSError, ShikiruError, ValueError) as error:
            self._doctor_error = f"Doctor could not complete: {error}"
            self._error = self._doctor_error
        self._update_pages()

    def _result(self) -> InitWizardResult:
        if self._config_path is None:
            message = "wizard result requested before config was written"
            raise RuntimeError(message)
        return InitWizardResult(self._config_path, self._doctor_report, self._doctor_error)

    def _update_pages(self) -> None:
        self.query_one("#wizard-step", Static).update(_step_label(WIZARD_STEPS[self._step]))
        for index in range(len(WIZARD_STEPS)):
            self.query_one(f"#wizard-page-{index}").display = index == self._step
        self.query_one("#wizard-body", InitWizardBody).scroll_home(animate=False)
        back = self.query_one("#wizard-back", Button)
        back.disabled = self._step == 0 or self._config_path is not None
        next_button = self.query_one("#wizard-next", Button)
        next_button.display = self._step < len(WIZARD_STEPS) - 1 and self._config_path is None
        next_button.disabled = self._step == 0 and not self._catalogs_loaded
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
        if self._config_path is None:
            self._update_doctor_report()
        else:
            self.call_after_refresh(self._update_doctor_report)
        self.call_after_refresh(self.query_one("#wizard-shell", InitWizardShell).limit_body_height)
        self._update_error()

    def _update_doctor_report(self) -> None:
        report_widget = self.query_one("#doctor-report", Static)
        if self._config_path is None:
            report_widget.update("")
            return
        width = report_widget.content_region.width
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

    def _update_error(self) -> None:
        error = self.query_one("#wizard-error", Static)
        error.update(self._error)
        error.display = bool(self._error)
        self.call_after_refresh(self.query_one("#wizard-shell", InitWizardShell).limit_body_height)

    def _repository_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-0", classes="wizard-page"):
            yield Static(f"Project root: {self._root}", markup=False)
            yield Static(f"Git base branch: {self._project.base}", markup=False)
            yield Static("The project root is the top level of this repository.", markup=False)
            yield Static("Discovering supported agent model choices…", id="catalog-discovery")

    def _agents_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-1", classes="wizard-page"):
            yield Static("", id="agent-choice-status", markup=False)
            yield Static("", id="agent-choice-guidance", markup=False)
            for role in WIZARD_ROLES:
                with (
                    Vertical(classes="ladder-role"),
                    Vertical(id=f"{role}-rungs", classes="ladder-rungs"),
                ):
                    yield Static("Loading model choices…", markup=False)

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
            yield Static(
                "Checking copy-on-write on this project filesystem…",
                id="cow-result",
                markup=False,
            )

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

    @staticmethod
    def _identity() -> Text:
        identity = Text()
        identity.append(WIZARD_TITLE, style=f"{STYLE_BOLD} {STEEL_BLUE}")
        identity.append(f" · {WIZARD_IDENTITY}", style=SECONDARY_TEXT_COLOR)
        return identity

    @staticmethod
    def _split_entries(value: str) -> tuple[str, ...]:
        if not value.strip():
            return ()
        entries = tuple(item.strip() for item in value.split(","))
        if any(not item for item in entries):
            message = "remove empty comma-separated entries"
            raise ConfigError(message)
        return entries
