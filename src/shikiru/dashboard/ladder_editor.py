from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from shikiru.agents.contracts import Rung, assess_model_choice
from shikiru.dashboard.row_list import RowCells, RowList
from shikiru.domain.ladder_text import LadderTextError, parse_ladder_text
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError
from shikiru.ui.section import BOLD_STEEL
from shikiru.ui.tokens import DUSTY_ROSE, PRIMARY_TEXT_COLOR, SECONDARY_TEXT_COLOR, STYLE_BOLD

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import RenderableType
    from textual.app import ComposeResult

    from shikiru.agents.contracts import ModelCatalog, ModelChoice
    from shikiru.config.settings import ComplexityName
    from shikiru.operator.plugin_checks import AgentCapabilityResult

COMPLEXITY_VALUES: tuple[ComplexityName, ...] = ("low", "normal", "high")
SCOPE_KEYS: tuple[tuple[str, ComplexityName | None], ...] = (
    (Role.LEADER.value, None),
    (Role.IMPLEMENTER.value, None),
    *((Role.IMPLEMENTER.value, value) for value in COMPLEXITY_VALUES),
    (Role.HELPER.value, None),
)
SCOPE_LABELS: dict[tuple[str, ComplexityName | None], str] = {
    (Role.LEADER.value, None): "Leader",
    (Role.IMPLEMENTER.value, None): "Implementer · Base",
    **{
        (Role.IMPLEMENTER.value, value): f"Implementer · {value.title()}"
        for value in COMPLEXITY_VALUES
    },
    (Role.HELPER.value, None): "Helper",
}
ADD_RUNG = "ladder-add"
AGENT_SELECT = "#ladder-agent"
MODEL_SELECT = "#ladder-model"
EFFORT_SELECT = "#ladder-effort"
LADDER_INPUT = "#ladder-input"
MODEL_PROMPT = "Choose model"
AGENT_PROMPT = "Choose installed agent"
EFFORT_PROMPT = "Native default"
EMPTY_LADDER_LABEL = "No entries yet"
LADDER_SEPARATOR = ", "
RUNG_SEPARATOR = ":"
SCOPE_LABEL_WIDTH = 22
HELPER_CAPABILITY_GUIDANCE = "Helper entries require an installed agent with headless support."
MODEL_CATALOG_GUIDANCE = (
    "Install or repair the agent CLI, restart init, or choose an available agent."
)
LADDER_ACTION_CSS = """
#wizard-frame #ladder-editor #ladder-add {
    width: auto; min-width: 0; height: 1; min-height: 1;
    margin: 0; padding: 0 1; border: none; border-top: none;
    border-right: none; border-bottom: none; border-left: none;
    background: $selected-surface; color: $primary-text; text-opacity: 1; text-style: bold;
}
#wizard-frame #ladder-editor #ladder-add:disabled {
    background: $surface; color: $secondary-text; text-opacity: 1; text-style: none;
}
#wizard-frame #ladder-editor #ladder-add:focus {
    color: $background; background: $focus; text-style: bold;
}
"""


@dataclass(frozen=True, slots=True)
class LadderConfiguration:
    ladders: Mapping[str, tuple[Rung, ...]]
    complexity: Mapping[ComplexityName, tuple[Rung, ...]]


class LadderEditor(Vertical):
    """Edit the six initializer ladder scopes using text and catalog-backed additions."""

    DEFAULT_CSS = """
    LadderEditor { height: auto; min-height: 0; }
    #ladder-description { margin-bottom: 1; }
    #ladder-scope-title, #ladder-input-title {
        width: 100%; height: auto; color: $steel-blue; text-style: bold;
    }
    #ladder-inheritance-context { width: 100%; height: auto; color: $secondary-text; }
    #ladder-scopes { height: auto; }
    #ladder-scopes .row-list-row { height: auto; }
    #ladder-input { width: 1fr; height: 1; margin: 0; }
    #ladder-input:focus { background: $selected-surface; }
    #ladder-problem { height: auto; color: $dusty-rose; }
    #ladder-form { height: auto; width: 100%; }
    #ladder-add-row { height: 1; width: 100%; align: left middle; }
    #ladder-add {
        width: auto; min-width: 0; height: 1; min-height: 1; margin: 0;
        padding: 0 1; border: none;
    }
    #ladder-guidance { height: auto; color: $secondary-text; }
    """

    def __init__(self) -> None:
        super().__init__(id="ladder-editor")
        self._catalogs: dict[str, ModelCatalog] = {}
        self._catalog_errors: dict[str, str] = {}
        self._capabilities: dict[str, AgentCapabilityResult] = {}
        self._drafts: dict[tuple[str, ComplexityName | None], str] = dict.fromkeys(SCOPE_KEYS, "")
        self._draft_cursors: dict[tuple[str, ComplexityName | None], int] = dict.fromkeys(
            SCOPE_KEYS, 0
        )
        self._selected_scope = 0
        self._new_agent = ""
        self._new_model = ""
        self._new_effort: str | None = None
        self._validation_requested = False
        self._validation_errors: dict[tuple[str, ComplexityName | None], tuple[str, str]] = {}

    def compose(self) -> ComposeResult:
        yield Static(
            "Leader, Implementer and Helper use ordered agent:model[:effort] entries. "
            "Blank Implementer Low, Normal and High fields inherit Base; entering text "
            "creates an override. Choose installed agents and supported models, then Add "
            "appends an entry.",
            id="ladder-description",
            markup=False,
        )
        yield Label(Text("Role and complexity ladders", style=BOLD_STEEL), id="ladder-scope-title")
        yield RowList(widget_id="ladder-scopes")
        yield Label("", id="ladder-input-title")
        yield Static("", id="ladder-inheritance-context", markup=False)
        with Horizontal(classes="palette-row wizard-long-label"):
            yield Label("Ladder entries")
            yield Input(value="", id=LADDER_INPUT[1:], select_on_focus=False)
        yield Static("", id="ladder-problem", markup=False)
        with Vertical(id="ladder-form"):
            with Horizontal(classes="palette-row"):
                yield Label("Agent")
                yield Select((), allow_blank=True, prompt=AGENT_PROMPT, id=AGENT_SELECT[1:])
            with Horizontal(classes="palette-row"):
                yield Label("Model")
                yield Select((), allow_blank=True, prompt=MODEL_PROMPT, id=MODEL_SELECT[1:])
            with Horizontal(classes="palette-row"):
                yield Label("Effort")
                yield Select((), allow_blank=True, prompt=EFFORT_PROMPT, id=EFFORT_SELECT[1:])
            with Horizontal(id="ladder-add-row"):
                yield Button("Add", id=ADD_RUNG, variant="primary")
        yield Static("", id="ladder-guidance", markup=False)

    def on_mount(self) -> None:
        self._refresh_scope()

    @on(RowList.Moved, "#ladder-scopes")
    def _scope_moved(self, message: RowList.Moved) -> None:
        message.stop()
        self._remember_field()
        self._selected_scope = message.index
        self._clear_form()
        self._refresh_scope()

    @on(RowList.Opened, "#ladder-scopes")
    def _scope_opened(self, message: RowList.Opened) -> None:
        message.stop()
        self._remember_field()
        self._selected_scope = message.index
        self._clear_form()
        self._refresh_scope()
        self.call_after_refresh(self.query_one(LADDER_INPUT, Input).focus)

    @on(Input.Changed, LADDER_INPUT)
    def _ladder_changed(self, event: Input.Changed) -> None:
        self._drafts[self._scope_key()] = event.input.value
        self._draft_cursors[self._scope_key()] = event.input.cursor_position
        self._refresh_rows()
        self._refresh_problem()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value != event.select.value:
            event.stop()
            return
        control_id = event.select.id
        if control_id == AGENT_SELECT[1:]:
            selected = event.value if isinstance(event.value, str) else ""
            self._new_agent = selected if selected in self._agent_names() else ""
            self._new_model = ""
            self._new_effort = None
            self._refresh_form()
            self.call_after_refresh(self.query_one(MODEL_SELECT, Select).focus)
        elif control_id == MODEL_SELECT[1:]:
            selected = event.value if isinstance(event.value, str) else ""
            self._new_model = selected if self._model_offered(self._new_agent, selected) else ""
            self._new_effort = None
            self._refresh_form()
            effort_select = cast("Select[str]", self.query_one(EFFORT_SELECT, Select))
            if not effort_select.disabled:
                self.call_after_refresh(effort_select.focus)
        elif control_id == EFFORT_SELECT[1:]:
            selected = event.value if isinstance(event.value, str) else ""
            self._new_effort = selected or None
            self._refresh_add_button()
        else:
            return
        event.stop()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != ADD_RUNG:
            return
        self._append_entry()
        event.stop()

    def set_model_catalogs(
        self,
        catalogs: Mapping[str, ModelCatalog],
        errors: Mapping[str, str],
    ) -> None:
        self._catalogs = {
            agent: catalog for agent, catalog in catalogs.items() if self._offered_choices(catalog)
        }
        self._catalog_errors = dict(errors)
        self._refresh_scope()

    def set_agent_capabilities(self, capabilities: Mapping[str, AgentCapabilityResult]) -> None:
        self._capabilities = dict(capabilities)
        self._refresh_scope()

    def set_scope(self, role: Role, complexity: ComplexityName | None = None) -> None:
        """Select a scope for measuring the wizard's preferred content size."""
        key = (role.value, complexity)
        if key not in SCOPE_KEYS:
            message = "complexity scopes belong to the Implementer role"
            raise ValueError(message)
        if self.is_mounted:
            self._remember_field()
        self._selected_scope = SCOPE_KEYS.index(key)
        if self.is_mounted:
            self._clear_form()
            self._refresh_scope()

    def configuration(self) -> LadderConfiguration:
        self._validation_requested = True
        self._validation_errors.clear()
        ladders: dict[str, tuple[Rung, ...]] = {}
        complexity: dict[ComplexityName, tuple[Rung, ...]] = {}
        for index, key in enumerate(SCOPE_KEYS):
            role, context = key
            raw = self._drafts[key]
            if role == Role.IMPLEMENTER.value and context is not None and not raw.strip():
                continue
            rungs = self._validated_scope(index, key, raw)
            if context is None:
                ladders[role] = rungs
            else:
                complexity[context] = rungs
        return LadderConfiguration(ladders, complexity)

    def show_validation_error(self, message: str) -> None:
        """Show the submitted scope error beside its editable ladder field."""
        self._validation_requested = True
        key = self._scope_key()
        self._validation_errors[key] = (self._drafts[key], message)
        self._update_problem(message)

    def _validated_scope(
        self,
        index: int,
        key: tuple[str, ComplexityName | None],
        raw: str,
    ) -> tuple[Rung, ...]:
        label = SCOPE_LABELS[key]
        try:
            rungs = parse_ladder_text(raw)
        except LadderTextError as error:
            self._focus_invalid(index)
            message = f"{label} · entry {error.entry_index + 1}: {error.detail}"
            raise ConfigError(message) from error
        if not rungs:
            self._focus_invalid(index)
            message = f"{label}: enter at least one ladder entry"
            raise ConfigError(message)
        for rung_index, rung in enumerate(rungs):
            if _is_helper_scope(key) and not self._headless_available(rung.agent):
                self._focus_invalid(index)
                message = f"{label} · entry {rung_index + 1}: {self._headless_detail(rung.agent)}"
                raise ConfigError(message)
            catalog = self._catalogs.get(rung.agent)
            if catalog is None:
                self._focus_invalid(index)
                detail = self._catalog_errors.get(rung.agent, "choose a listed agent")
                message = f"{label} · entry {rung_index + 1}: {detail}"
                raise ConfigError(message)
            assessment = assess_model_choice(rung.model, rung.effort, catalog)
            if assessment.supported is not True:
                self._focus_invalid(index)
                message = f"{label} · entry {rung_index + 1}: {assessment.detail}"
                raise ConfigError(message)
        return rungs

    def _append_entry(self) -> None:
        rung = self._selected_rung()
        if rung is None or not self._valid_catalog_choice(rung):
            self._update_problem(
                "Choose an available agent and model, plus a supported effort when offered."
            )
            return
        key = self._scope_key()
        raw = self._drafts[key]
        try:
            parse_ladder_text(raw)
        except LadderTextError as error:
            self._validation_requested = True
            self._update_problem(f"Entry {error.entry_index + 1}: {error.detail}")
            self.call_after_refresh(self.query_one(LADDER_INPUT, Input).focus)
            return
        if _is_helper_scope(key) and not self._headless_available(rung.agent):
            self._update_problem(HELPER_CAPABILITY_GUIDANCE)
            return
        value = f"{rung.agent}{RUNG_SEPARATOR}{rung.model}"
        if rung.effort is not None:
            value = f"{value}{RUNG_SEPARATOR}{rung.effort}"
        updated = f"{raw}{LADDER_SEPARATOR}{value}" if raw.strip() else value
        field = self.query_one(LADDER_INPUT, Input)
        self._drafts[key] = updated
        self._draft_cursors[key] = len(updated)
        field.value = updated
        field.cursor_position = len(updated)
        self._refresh_scope()
        self.call_after_refresh(field.focus)

    def _refresh_scope(self) -> None:
        if not self.is_mounted:
            return
        field = self.query_one(LADDER_INPUT, Input)
        key = self._scope_key()
        self.query_one("#ladder-input-title", Label).update(
            Text(f"Edit {SCOPE_LABELS[key]}", style=BOLD_STEEL)
        )
        raw = self._drafts[key]
        inherited = key[0] == Role.IMPLEMENTER.value and key[1] is not None and not raw.strip()
        base = self._drafts[(Role.IMPLEMENTER.value, None)]
        inheritance = f"Blank inherits Implementer · Base: {base or '(empty)'}" if inherited else ""
        inheritance_widget = self.query_one("#ladder-inheritance-context", Static)
        inheritance_widget.update(inheritance)
        inheritance_widget.display = bool(inheritance)
        if field.value != raw:
            field.value = raw
        field.cursor_position = min(self._draft_cursors[key], len(raw))
        self._refresh_rows()
        self._refresh_form()
        self._refresh_problem()
        guidance = self.query_one("#ladder-guidance", Static)
        guidance_text = self._guidance()
        guidance.update(Text(guidance_text, style=SECONDARY_TEXT_COLOR))
        guidance.display = bool(guidance_text)

    def _refresh_rows(self) -> None:
        rows = tuple(self._scope_row(key) for key in SCOPE_KEYS)
        self.query_one("#ladder-scopes", RowList).set_rows(
            rows,
            selected=self._selected_scope,
            empty=EMPTY_LADDER_LABEL,
        )

    def _scope_row(self, key: tuple[str, ComplexityName | None]) -> RowCells:
        raw = self._drafts[key]
        label = SCOPE_LABELS[key]
        selected = SCOPE_KEYS.index(key) == self._selected_scope
        emphasis = f"{STYLE_BOLD} " if selected else ""
        if key[0] == Role.IMPLEMENTER.value and key[1] is not None and not raw.strip():
            base = self._drafts[(Role.IMPLEMENTER.value, None)]
            summary = f"inherits Base · {base or '(empty)'}"
        else:
            summary = raw or "(empty)"
        return RowCells(
            lead="",
            text="",
            columns=(
                Text(label, style=f"{emphasis}{SECONDARY_TEXT_COLOR}"),
                Text(summary, style=f"{emphasis}{PRIMARY_TEXT_COLOR}"),
            ),
            column_width=SCOPE_LABEL_WIDTH,
        )

    def _refresh_form(self) -> None:
        agent_select = cast("Select[str]", self.query_one(AGENT_SELECT, Select))
        model_select = cast("Select[str]", self.query_one(MODEL_SELECT, Select))
        effort_select = cast("Select[str]", self.query_one(EFFORT_SELECT, Select))
        agents = self._agent_options()
        self._set_select_options(agent_select, agents, self._new_agent or None)
        agent_select.disabled = not agents
        models = self._model_options(self._new_agent) if self._new_agent else ()
        self._set_select_options(model_select, models, self._new_model or None)
        model_select.disabled = not self._new_agent or not models
        efforts = self._effort_options(self._new_agent, self._new_model)
        self._set_select_options(effort_select, efforts, self._new_effort)
        effort_select.disabled = not self._new_model or not efforts
        self._refresh_add_button()

    def _refresh_add_button(self) -> None:
        button = self.query_one(f"#{ADD_RUNG}", Button)
        rung = self._selected_rung()
        button.disabled = rung is None or not self._valid_catalog_choice(rung)

    def _refresh_problem(self) -> None:
        if not self._validation_requested:
            self._update_problem("")
            return
        key = self._scope_key()
        raw = self._drafts[key]
        validation = self._validation_errors.get(key)
        if validation is not None:
            if validation[0] == raw:
                self._update_problem(validation[1])
                return
            del self._validation_errors[key]
        try:
            parse_ladder_text(raw)
        except LadderTextError as error:
            self._update_problem(f"Entry {error.entry_index + 1}: {error.detail}")
        else:
            self._update_problem("")

    def _update_problem(self, message: str) -> None:
        problem = self.query_one("#ladder-problem", Static)
        problem.update(Text(message, style=DUSTY_ROSE))
        problem.display = bool(message)

    def _set_select_options(
        self,
        select: Select[str],
        options: tuple[tuple[RenderableType, str], ...],
        value: str | None,
    ) -> None:
        target = Select.BLANK if value is None else value
        with select.prevent(Select.Changed):
            select.set_options(options)
            if select.value != target:
                select.value = target

    def _clear_form(self) -> None:
        self._new_agent = ""
        self._new_model = ""
        self._new_effort = None

    def _remember_field(self) -> None:
        if self.is_mounted:
            field = self.query_one(LADDER_INPUT, Input)
            key = self._scope_key()
            self._drafts[key] = field.value
            self._draft_cursors[key] = field.cursor_position

    def _focus_invalid(self, scope_index: int) -> None:
        self._remember_field()
        self._clear_form()
        self._selected_scope = scope_index
        self._refresh_scope()
        self.call_after_refresh(self.query_one(LADDER_INPUT, Input).focus)

    def _scope_key(self) -> tuple[str, ComplexityName | None]:
        return SCOPE_KEYS[self._selected_scope]

    def _selected_rung(self) -> Rung | None:
        if not self._new_agent or not self._new_model:
            return None
        return Rung(agent=self._new_agent, model=self._new_model, effort=self._new_effort)

    def _valid_catalog_choice(self, rung: Rung) -> bool:
        catalog = self._catalogs.get(rung.agent)
        if (
            catalog is None
            or assess_model_choice(rung.model, rung.effort, catalog).supported is not True
        ):
            return False
        return not _is_helper_scope(self._scope_key()) or self._headless_available(rung.agent)

    def _agent_names(self) -> tuple[str, ...]:
        if _is_helper_scope(self._scope_key()):
            return tuple(agent for agent in self._catalogs if self._headless_available(agent))
        return tuple(self._catalogs)

    def _agent_options(self) -> tuple[tuple[RenderableType, str], ...]:
        return tuple(
            (Text(agent, style=PRIMARY_TEXT_COLOR), agent) for agent in self._agent_names()
        )

    def _model_options(self, agent: str) -> tuple[tuple[RenderableType, str], ...]:
        catalog = self._catalogs.get(agent)
        if catalog is None:
            return ()
        return tuple(
            (Text(choice.model, style=PRIMARY_TEXT_COLOR), choice.model)
            for choice in self._offered_choices(catalog)
        )

    def _effort_options(self, agent: str, model: str) -> tuple[tuple[RenderableType, str], ...]:
        catalog = self._catalogs.get(agent)
        if catalog is None or not model:
            return ()
        choice = next(item for item in self._offered_choices(catalog) if item.model == model)
        return tuple(
            (Text(effort, style=PRIMARY_TEXT_COLOR), effort) for effort in choice.efforts or ()
        )

    def _model_offered(self, agent: str, model: str) -> bool:
        catalog = self._catalogs.get(agent)
        return catalog is not None and model in {
            choice.model for choice in self._offered_choices(catalog)
        }

    def _headless_available(self, agent: str) -> bool:
        capability = self._capabilities.get(agent)
        return capability is not None and capability.headless

    def _headless_detail(self, agent: str) -> str:
        capability = self._capabilities.get(agent)
        if capability is not None and capability.error:
            return f"headless support unavailable: {capability.error}"
        return "Helper requires an agent with headless support"

    def _guidance(self) -> str:
        parts: list[str] = []
        if not self._catalogs and not self._catalog_errors:
            parts.append(MODEL_CATALOG_GUIDANCE)
        if self._catalog_errors:
            failures = "; ".join(
                f"{agent}: {detail}" for agent, detail in self._catalog_errors.items()
            )
            parts.append(f"Model discovery unavailable for {failures}. {MODEL_CATALOG_GUIDANCE}")
        if _is_helper_scope(self._scope_key()) and not any(
            self._headless_available(agent) for agent in self._catalogs
        ):
            parts.append(HELPER_CAPABILITY_GUIDANCE)
        return " ".join(parts)

    @staticmethod
    def _offered_choices(catalog: ModelCatalog) -> tuple[ModelChoice, ...]:
        return tuple(choice for choice in catalog.models if choice.offered)


def _is_helper_scope(key: tuple[str, ComplexityName | None]) -> bool:
    return key[0] == Role.HELPER.value
