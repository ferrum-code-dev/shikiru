from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import events, on
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Select, Static

from shikiru.agents.contracts import Rung, assess_model_choice
from shikiru.dashboard.row_list import RowCells, RowList
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import RenderableType
    from textual.app import ComposeResult

    from shikiru.agents.contracts import ModelCatalog, ModelChoice
    from shikiru.config.settings import ComplexityName

ROLE_VALUES = (Role.LEADER.value, Role.IMPLEMENTER.value, Role.HELPER.value)
EDITABLE_ROLES = (Role.LEADER.value, Role.IMPLEMENTER.value)
COMPLEXITY_VALUES: tuple[ComplexityName, ...] = ("low", "normal", "high")
BASE_CONTEXT = "base"
CONTEXT_OPTIONS = (
    ("Base ladder", BASE_CONTEXT),
    *((value.title(), value) for value in COMPLEXITY_VALUES),
)
ROLE_OPTIONS = tuple((value.title(), value) for value in ROLE_VALUES)
NARROW_WIDTH = 74
ADD_RUNG = "ladder-add"
REMOVE_RUNG = "ladder-remove"
MOVE_RUNG_UP = "ladder-up"
MOVE_RUNG_DOWN = "ladder-down"
CUSTOMIZE_OVERRIDE = "ladder-customize"
USE_BASE_LADDER = "ladder-use-base"
MODEL_PROMPT = "Choose model"
NATIVE_EFFORT_LABEL = "Native default"
MODEL_CONTEXT_LABEL = "Selected model: "
EMPTY_RUNG_LABEL = "Choose a listed agent and model"
MODEL_CATALOG_GUIDANCE = (
    "Install or repair the agent CLI, restart init, or choose an available agent."
)
LADDER_ACTION_CSS = """
#wizard-frame #ladder-editor #ladder-rung-actions Button {
    border: none; margin: 0; padding: 0;
}
"""
RUNG_SEPARATOR = " · "
ROLE_SELECT = "#ladder-role"
CONTEXT_SELECT = "#ladder-context"
AGENT_SELECT = "#ladder-agent"
MODEL_SELECT = "#ladder-model"
EFFORT_SELECT = "#ladder-effort"
MODEL_DETAIL = "#ladder-model-detail"
RUNG_SELECTORS = (AGENT_SELECT, MODEL_SELECT, EFFORT_SELECT)
RUNG_SELECT_IDS = (AGENT_SELECT[1:], MODEL_SELECT[1:], EFFORT_SELECT[1:])


@dataclass(frozen=True, slots=True)
class LadderConfiguration:
    ladders: Mapping[str, tuple[Rung, ...]]
    complexity: Mapping[ComplexityName, tuple[Rung, ...]]


class LadderEditor(Vertical):
    """Edit role ladders and explicit Implementer complexity overrides."""

    DEFAULT_CSS = """
    LadderEditor { height: auto; min-height: 0; }
    #ladder-role-row, #ladder-context-row { height: auto; width: 100%; }
    #ladder-role-row Label, #ladder-context-row Label {
        width: 12; height: 1; color: $secondary-text;
    }
    #ladder-role, #ladder-context { width: 1fr; height: 1; }
    #ladder-state { height: auto; color: $secondary-text; }
    #ladder-workspace { height: auto; width: 100%; layout: horizontal; }
    #ladder-list-column { width: 1fr; height: auto; padding: 0 1 0 0; }
    #ladder-rungs { height: auto; }
    #ladder-rung-form { width: 2fr; height: auto; }
    #ladder-rung-form Label { width: 100%; height: 1; color: $secondary-text; }
    #ladder-rung-form Select { width: 100%; height: 1; margin-bottom: 0; }
    #ladder-rung-actions { height: auto; width: 100%; }
    #ladder-rung-actions-main, #ladder-rung-actions-override { height: 1; width: 100%; }
    #ladder-editor #ladder-rung-actions Button.-style-default {
        width: auto; min-width: 0; height: 1; min-height: 1; margin: 0 1 0 0;
        padding: 0;
    }
    #ladder-editor #ladder-rung-actions Button.-style-default:focus {
        color: $primary-text; background: $selected-surface; text-style: reverse;
    }
    #ladder-model-detail { height: auto; color: $primary-text; }
    #ladder-guidance { height: auto; color: $secondary-text; }
    LadderEditor.narrow #ladder-workspace { layout: vertical; }
    LadderEditor.narrow #ladder-list-column { width: 100%; padding: 0; }
    LadderEditor.narrow #ladder-rung-form { width: 100%; }
    LadderEditor.narrow #ladder-role-row Label,
    LadderEditor.narrow #ladder-context-row Label { width: 9; }
    LadderEditor.narrow #ladder-rung-actions Button { padding: 0; }
    """

    def __init__(self) -> None:
        super().__init__(id="ladder-editor")
        self._catalogs: dict[str, ModelCatalog] = {}
        self._catalog_errors: dict[str, str] = {}
        self._drafts: dict[tuple[str, ComplexityName | None], list[Rung]] = {
            (role, None): [Rung(agent="", model="")] for role in ROLE_VALUES
        }
        self._overrides: dict[ComplexityName, list[Rung]] = {}
        self._selected: dict[tuple[str, ComplexityName | None], int] = {}
        self._role = Role.LEADER.value
        self._complexity: ComplexityName | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="ladder-role-row"):
            yield Label("Role")
            yield Select(ROLE_OPTIONS, value=self._role, allow_blank=False, id=ROLE_SELECT[1:])
        with Horizontal(id="ladder-context-row"):
            yield Label("Context")
            yield Select(
                CONTEXT_OPTIONS,
                value=BASE_CONTEXT,
                allow_blank=False,
                id=CONTEXT_SELECT[1:],
            )
        yield Static("Base ladder", id="ladder-state", markup=False)
        with Horizontal(id="ladder-workspace"):
            with Vertical(id="ladder-list-column"):
                yield Label("Ordered rungs")
                yield RowList(widget_id="ladder-rungs")
                with Vertical(id="ladder-rung-actions"):
                    with Horizontal(id="ladder-rung-actions-main"):
                        yield Button("Add", id=ADD_RUNG)
                        yield Button("Remove", id=REMOVE_RUNG)
                        yield Button("Up", id=MOVE_RUNG_UP)
                        yield Button("Down", id=MOVE_RUNG_DOWN)
                    with Horizontal(id="ladder-rung-actions-override"):
                        yield Button("Customize", id=CUSTOMIZE_OVERRIDE)
                        yield Button("Use base", id=USE_BASE_LADDER)
            with Vertical(id="ladder-rung-form"):
                yield Label("Edit selected rung", id="ladder-rung-title")
                yield Label("Agent")
                yield Select((), allow_blank=True, prompt="Choose agent", id=AGENT_SELECT[1:])
                yield Label("Model")
                yield Select((), allow_blank=True, prompt=MODEL_PROMPT, id=MODEL_SELECT[1:])
                yield Label("Effort")
                yield Select(
                    (),
                    allow_blank=True,
                    prompt="Choose a model first",
                    id=EFFORT_SELECT[1:],
                )
                yield Static(EMPTY_RUNG_LABEL, id="ladder-model-detail", markup=False)
        yield Static("", id="ladder-guidance", markup=False)

    def on_mount(self) -> None:
        self._set_narrow_layout()
        self._refresh_scope()

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._set_narrow_layout()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == ADD_RUNG:
            self._add_rung()
        elif button_id == REMOVE_RUNG:
            self._remove_rung()
        elif button_id == MOVE_RUNG_UP:
            self._move_rung(-1)
        elif button_id == MOVE_RUNG_DOWN:
            self._move_rung(1)
        elif button_id == CUSTOMIZE_OVERRIDE:
            self._customize_override()
        elif button_id == USE_BASE_LADDER:
            self._use_base_ladder()
        else:
            return
        event.stop()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value != event.select.value:
            event.stop()
            return
        control_id = event.select.id or ""
        if control_id == ROLE_SELECT[1:]:
            self._change_role(event.value)
        elif control_id == CONTEXT_SELECT[1:]:
            self._change_context(event.value)
        elif control_id in RUNG_SELECT_IDS:
            self._change_rung(control_id, event.value)
        else:
            return
        event.stop()

    def _change_role(self, value: object) -> None:
        if isinstance(value, str) and value in ROLE_VALUES:
            self._role = value
            if self._role != Role.IMPLEMENTER.value:
                self._complexity = None
            self._refresh_scope()

    def _change_context(self, value: object) -> None:
        if isinstance(value, str):
            if value == BASE_CONTEXT:
                self._complexity = None
            elif value in COMPLEXITY_VALUES:
                self._complexity = value
            self._refresh_scope()

    def set_model_catalogs(
        self,
        catalogs: Mapping[str, ModelCatalog],
        errors: Mapping[str, str],
    ) -> None:
        self._catalogs = {
            agent: catalog for agent, catalog in catalogs.items() if self._offered_choices(catalog)
        }
        self._catalog_errors = dict(errors)
        agent_select = cast("Select[str]", self.query_one(AGENT_SELECT, Select))
        with agent_select.prevent(Select.Changed):
            agent_select.set_options(self._agent_options())
        self._refresh_scope()

    def set_scope(self, role: Role, complexity: ComplexityName | None = None) -> None:
        """Select a role/context scope without changing any saved draft."""
        if complexity is not None and (
            role is not Role.IMPLEMENTER or complexity not in COMPLEXITY_VALUES
        ):
            message = "complexity scopes belong to the Implementer role"
            raise ValueError(message)
        self._role = role.value
        self._complexity = complexity
        role_select = cast("Select[str]", self.query_one(ROLE_SELECT, Select))
        self._set_select_value(role_select, role.value)
        self._refresh_scope()

    def configuration(self) -> LadderConfiguration:
        for role in ROLE_VALUES:
            self._validate_scope(role, None, self._drafts[(role, None)])
        for complexity, rungs in self._overrides.items():
            self._validate_scope(Role.IMPLEMENTER.value, complexity, rungs)
        ladders = {role: tuple(self._drafts[(role, None)]) for role in ROLE_VALUES}
        complexity_ladders: dict[ComplexityName, tuple[Rung, ...]] = {
            complexity: tuple(rungs) for complexity, rungs in self._overrides.items()
        }
        return LadderConfiguration(ladders, complexity_ladders)

    def _validate_scope(
        self,
        role: str,
        complexity: ComplexityName | None,
        rungs: list[Rung],
    ) -> None:
        context = self._scope_label(role, complexity)
        if not rungs:
            self._focus_invalid(role, complexity, 0, "agent")
            message = f"{context}: ladder must contain at least one rung"
            raise ConfigError(message)
        if role == Role.HELPER.value and len(rungs) != 1:
            self._focus_invalid(role, complexity, 0, "agent")
            message = f"{context}: Helper must contain exactly one rung"
            raise ConfigError(message)
        for index, rung in enumerate(rungs):
            if rung.agent not in self._catalogs:
                self._focus_invalid(role, complexity, index, "agent")
                detail = self._catalog_errors.get(rung.agent, "choose a listed agent")
                message = f"{context} · rung {index + 1}: {detail}"
                raise ConfigError(message)
            catalog = self._catalogs[rung.agent]
            choices = {choice.model for choice in self._offered_choices(catalog)}
            if rung.model not in choices:
                self._focus_invalid(role, complexity, index, "model")
                message = f"{context} · rung {index + 1}: choose a listed model"
                raise ConfigError(message)
            assessment = assess_model_choice(rung.model, rung.effort, catalog)
            if assessment.supported is not True:
                self._focus_invalid(role, complexity, index, "effort")
                message = f"{context} · rung {index + 1}: {assessment.detail}"
                raise ConfigError(message)

    def _change_rung(self, control_id: str, value: object) -> None:
        index = self._selected_index()
        rungs = self._editing_rungs()
        if index >= len(rungs) or not self._is_editable():
            return
        current = rungs[index]
        if control_id == AGENT_SELECT[1:]:
            agent = value if isinstance(value, str) and value in self._catalogs else ""
            self._store_rung(index, Rung(agent=agent, model=""))
            model_select = cast("Select[str]", self.query_one(MODEL_SELECT, Select))
            effort_select = cast("Select[str]", self.query_one(EFFORT_SELECT, Select))
            self._set_select_options(
                model_select, self._model_options(agent) if agent else (), None
            )
            effort_select.prompt = "Choose a model first"
            self._set_select_options(effort_select, (), None)
            effort_select.disabled = True
        elif control_id == MODEL_SELECT[1:]:
            model = value if isinstance(value, str) else ""
            catalog = self._catalogs.get(current.agent)
            offered: set[str] = (
                {choice.model for choice in self._offered_choices(catalog)}
                if catalog is not None
                else set()
            )
            self._store_rung(
                index,
                Rung(agent=current.agent, model=model if model in offered else ""),
            )
            selected_model = model if model in offered else ""
            effort_select = cast("Select[str]", self.query_one(EFFORT_SELECT, Select))
            effort_select.prompt = NATIVE_EFFORT_LABEL if selected_model else "Choose a model first"
            self._set_select_options(
                effort_select,
                self._effort_options(current.agent, selected_model),
                None,
            )
            effort_select.disabled = not selected_model
        else:
            effort = value if isinstance(value, str) and value else None
            self._store_rung(index, Rung(agent=current.agent, model=current.model, effort=effort))
        self._update_model_detail(self._editing_rungs()[index])
        self._refresh_rows()

    def _add_rung(self) -> None:
        if self._role not in EDITABLE_ROLES or not self._is_editable():
            return
        rungs = self._editing_rungs()
        rungs.append(Rung(agent="", model=""))
        self._set_selected_index(len(rungs) - 1)
        self._refresh_scope()
        self.call_after_refresh(self.query_one(AGENT_SELECT, Select).focus)

    def _remove_rung(self) -> None:
        if self._role not in EDITABLE_ROLES or not self._is_editable():
            return
        rungs = self._editing_rungs()
        index = self._selected_index()
        if index >= len(rungs):
            return
        rungs.pop(index)
        self._set_selected_index(min(index, len(rungs) - 1))
        self._refresh_scope()

    def _move_rung(self, direction: int) -> None:
        if self._role not in EDITABLE_ROLES or not self._is_editable():
            return
        rungs = self._editing_rungs()
        index = self._selected_index()
        target = index + direction
        if not 0 <= target < len(rungs):
            return
        rungs[index], rungs[target] = rungs[target], rungs[index]
        self._set_selected_index(target)
        self._refresh_scope()

    def _customize_override(self) -> None:
        if self._role != Role.IMPLEMENTER.value or self._complexity is None:
            return
        self._overrides[self._complexity] = list(self._drafts[(self._role, None)])
        self._selected[(self._role, self._complexity)] = self._selected_index()
        self._refresh_scope()
        self.call_after_refresh(self.query_one(AGENT_SELECT, Select).focus)

    def _use_base_ladder(self) -> None:
        if self._complexity is None:
            return
        self._overrides.pop(self._complexity, None)
        self._selected.pop((self._role, self._complexity), None)
        self._refresh_scope()

    def _refresh_scope(self) -> None:
        if not self.is_mounted:
            return
        context_select = cast("Select[str]", self.query_one(CONTEXT_SELECT, Select))
        self._set_select_value(context_select, self._complexity or BASE_CONTEXT)
        context_select.disabled = self._role != Role.IMPLEMENTER.value
        self.query_one("#ladder-context-row", Horizontal).display = (
            self._role == Role.IMPLEMENTER.value
        )
        context = self._complexity if self._role == Role.IMPLEMENTER.value else None
        inherited = context is not None and context not in self._overrides
        rungs = self._drafts[(self._role, None)] if inherited else self._editing_rungs()
        state = self.query_one("#ladder-state", Static)
        state.update(self._scope_label(self._role, context, inherited=inherited))
        self.query_one("#ladder-rung-actions-main", Horizontal).display = (
            self._role in EDITABLE_ROLES
        )
        self.query_one("#ladder-rung-actions-override", Horizontal).display = (
            self._role == Role.IMPLEMENTER.value and context is not None
        )
        self.query_one(f"#{CUSTOMIZE_OVERRIDE}", Button).display = inherited
        self.query_one(f"#{USE_BASE_LADDER}", Button).display = (
            context is not None and not inherited
        )
        for selector in RUNG_SELECTORS:
            self.query_one(selector, Select).disabled = inherited or not rungs
        self._refresh_rows()
        self._refresh_controls()
        self.query_one("#ladder-guidance", Static).update(self._guidance())

    def _refresh_rows(self) -> None:
        row_list = self.query_one("#ladder-rungs", RowList)
        rungs = self._editing_rungs()
        rows = tuple(
            RowCells(
                lead=str(index + 1),
                text=self._rung_summary(rung),
            )
            for index, rung in enumerate(rungs)
        )
        selected = self._selected_index()
        row_list.set_rows(rows, selected=selected, empty="No rungs")
        if self._is_editable():
            self._set_selected_index(row_list.selected)
        self._refresh_actions(rungs)

    def _refresh_actions(self, rungs: list[Rung]) -> None:
        editable = self._role in EDITABLE_ROLES and self._is_editable()
        selected = self._selected_index()
        self.query_one(f"#{ADD_RUNG}", Button).disabled = not editable
        self.query_one(f"#{REMOVE_RUNG}", Button).disabled = not editable or not rungs
        self.query_one(f"#{MOVE_RUNG_UP}", Button).disabled = not editable or selected == 0
        self.query_one(f"#{MOVE_RUNG_DOWN}", Button).disabled = (
            not editable or selected >= len(rungs) - 1
        )

    def _refresh_controls(self) -> None:
        rungs = self._editing_rungs()
        index = self._selected_index()
        rung = rungs[index] if index < len(rungs) else None
        agent_select = cast("Select[str]", self.query_one(AGENT_SELECT, Select))
        model_select = cast("Select[str]", self.query_one(MODEL_SELECT, Select))
        effort_select = cast("Select[str]", self.query_one(EFFORT_SELECT, Select))
        self._set_select_value(
            agent_select,
            rung.agent if rung and rung.agent in self._catalogs else None,
        )
        model_options = (
            self._model_options(rung.agent) if rung and rung.agent in self._catalogs else ()
        )
        self._set_select_options(
            model_select,
            model_options,
            rung.model if rung and rung.model in {value for _, value in model_options} else None,
        )
        has_model = bool(rung and rung.model)
        effort_options = self._effort_options(rung.agent, rung.model) if has_model and rung else ()
        effort_select.prompt = NATIVE_EFFORT_LABEL if has_model else "Choose a model first"
        effort_select.disabled = not self._is_editable() or not has_model
        self._set_select_options(
            effort_select,
            effort_options,
            rung.effort if rung and rung.effort in {value for _, value in effort_options} else None,
        )
        self._update_model_detail(rung)
        self.query_one("#ladder-rung-title", Label).update(
            f"Edit rung {index + 1}" if rung else "Edit selected rung"
        )

    def _update_model_detail(self, rung: Rung | None) -> None:
        if rung and rung.model:
            self.query_one(MODEL_DETAIL, Static).update(f"{MODEL_CONTEXT_LABEL}{rung.model}")
        else:
            self.query_one(MODEL_DETAIL, Static).update(EMPTY_RUNG_LABEL)

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

    def _set_select_value(self, select: Select[str], value: str | None) -> None:
        target = Select.BLANK if value is None else value
        if select.value != target:
            with select.prevent(Select.Changed):
                select.value = target

    @on(RowList.Moved, "#ladder-rungs")
    def _on_ladder_rungs_moved(self, message: RowList.Moved) -> None:
        message.stop()
        self._set_selected_index(message.index)
        self._refresh_controls()
        self._refresh_rows()

    @on(RowList.Opened, "#ladder-rungs")
    def _on_ladder_rungs_opened(self, message: RowList.Opened) -> None:
        message.stop()
        self._set_selected_index(message.index)
        self._refresh_controls()

    def _editing_rungs(self) -> list[Rung]:
        context = self._active_context()
        if context is not None and context in self._overrides:
            return self._overrides[context]
        return self._drafts[(self._role, None)]

    def _active_context(self) -> ComplexityName | None:
        return self._complexity if self._role == Role.IMPLEMENTER.value else None

    def _is_editable(self) -> bool:
        context = self._active_context()
        return context is None or context in self._overrides

    def _store_rung(self, index: int, rung: Rung) -> None:
        rungs = self._editing_rungs()
        if index < len(rungs):
            rungs[index] = rung

    def _selected_index(self) -> int:
        return self._selected.get((self._role, self._active_context()), 0)

    def _set_selected_index(self, index: int) -> None:
        self._selected[(self._role, self._active_context())] = max(index, 0)

    def _focus_invalid(
        self,
        role: str,
        complexity: ComplexityName | None,
        index: int,
        field: str,
    ) -> None:
        self._role = role
        self._complexity = complexity
        self._set_selected_index(index)
        self._set_select_value(cast("Select[str]", self.query_one(ROLE_SELECT, Select)), role)
        self._refresh_scope()
        control = cast("Select[str]", self.query_one(f"#ladder-{field}", Select))
        self.call_after_refresh(control.focus)

    def _set_narrow_layout(self) -> None:
        if self.screen.size.width < NARROW_WIDTH:
            self.add_class("narrow")
        else:
            self.remove_class("narrow")

    def _guidance(self) -> str:
        if self._catalog_errors:
            failures = "; ".join(
                f"{agent}: {detail}" for agent, detail in self._catalog_errors.items()
            )
            return f"Model discovery unavailable for {failures}. {MODEL_CATALOG_GUIDANCE}"
        return "Choose only listed models and their reported efforts."

    def _scope_label(
        self,
        role: str,
        complexity: ComplexityName | None,
        *,
        inherited: bool = False,
    ) -> str:
        if complexity is None:
            return f"{role.title()} base ladder"
        label = f"Implementer · {complexity.title()}"
        return f"{label} · inherits base ladder" if inherited else f"{label} · custom override"

    @staticmethod
    def _rung_summary(rung: Rung) -> Text:
        parts = [rung.agent or "Choose agent", rung.model or "Choose model"]
        if rung.effort:
            parts.append(rung.effort)
        return Text(RUNG_SEPARATOR.join(parts), style=PRIMARY_TEXT_COLOR)

    def _agent_options(self) -> tuple[tuple[RenderableType, str], ...]:
        return tuple((Text(agent, style=PRIMARY_TEXT_COLOR), agent) for agent in self._catalogs)

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

    @staticmethod
    def _offered_choices(catalog: ModelCatalog) -> tuple[ModelChoice, ...]:
        return tuple(choice for choice in catalog.models if choice.offered)
