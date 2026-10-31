from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING, ClassVar, cast

from rich.console import Group
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, Rule, Select, Static

from shikiru.config.settings import (
    MERGE_INTEGRATION,
    PULL_REQUEST_INTEGRATION,
    SQUASH_INTEGRATION,
)
from shikiru.dashboard.ladder_editor import LADDER_ACTION_CSS, LadderConfiguration, LadderEditor
from shikiru.dashboard.orchestration_fields import (
    MAX_CPU,
    MAX_ITERATIONS,
    MAX_PARALLEL,
    MAX_QUOTA_WAIT,
    MIN_FREE_RAM,
    ORCHESTRATION_FIELDS,
    PUSH_INTERVAL,
    STALL_TIMEOUT,
    TOOL_TIMEOUT,
    OrchestrationFieldError,
)
from shikiru.dashboard.row_list import RowList
from shikiru.domain.tasks import Role
from shikiru.errors import ConfigError, ShikiruError
from shikiru.operator.detectors import (
    DetectedProject,
    InitWizardResult,
    OrchestrationChoices,
    ProjectDetectors,
)
from shikiru.paths import project_config_path
from shikiru.ui.keys import key_hint
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_COMPACT_CONTROL_CSS,
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
    AMBER,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.console import RenderableType
    from textual import events
    from textual.binding import BindingType
    from textual.timer import Timer

    from shikiru.agents.contracts import ModelCatalog
    from shikiru.config.settings import OrchestrationConfig
    from shikiru.dashboard.orchestration_fields import OrchestrationField
    from shikiru.operator.doctor import Doctor, DoctorReport
    from shikiru.operator.plugin_checks import AgentCapabilityResult
    from shikiru.workspace.git import LineChangeStats

STEP_REPOSITORY = "Repository"
STEP_AGENTS = "Agents and ladders"
STEP_TRACKERS = "Trackers"
STEP_CACHES = "Caches and CoW"
STEP_ORCHESTRATION = "Orchestration"
STEP_INTEGRATION = "Integration mode"
STEP_FINISH = "Finish"
TRACKERS_INPUT_ID = "trackers"
CACHES_INPUT_ID = "caches"
TRACKERS_LIST_ID = "tracker-candidates"
CACHES_LIST_ID = "cache-candidates"
INTEGRATION_SELECT_ID = "integration"
REPOSITORY_CHECKOUT_ID = "wizard-checkout"
WIZARD_STEPS = (
    STEP_REPOSITORY,
    STEP_AGENTS,
    STEP_TRACKERS,
    STEP_CACHES,
    STEP_ORCHESTRATION,
    STEP_INTEGRATION,
    STEP_FINISH,
)
REPOSITORY_STEP_INDEX = WIZARD_STEPS.index(STEP_REPOSITORY)
AGENTS_STEP_INDEX = WIZARD_STEPS.index(STEP_AGENTS)
TRACKERS_STEP_INDEX = WIZARD_STEPS.index(STEP_TRACKERS)
COW_STEP_INDEX = WIZARD_STEPS.index(STEP_CACHES)
ORCHESTRATION_STEP_INDEX = WIZARD_STEPS.index(STEP_ORCHESTRATION)
INTEGRATION_STEP_INDEX = WIZARD_STEPS.index(STEP_INTEGRATION)
FINISH_STEP_INDEX = WIZARD_STEPS.index(STEP_FINISH)
INTEGRATION_OPTIONS = (
    ("Local merge", MERGE_INTEGRATION),
    ("Local squash and merge", SQUASH_INTEGRATION),
    ("Pull request", PULL_REQUEST_INTEGRATION),
)
INTEGRATION_LABELS = {value: label for label, value in INTEGRATION_OPTIONS}
INTEGRATION_UNSELECTED = "needs a choice"
WIZARD_TITLE = "SHIKIRU"
LEGACY_CATALOG_DETAIL = "agent does not expose an authoritative model catalog"
WIZARD_FRAME_MAX_WIDTH = 96
WIZARD_FRAME_HEIGHT = 36
WIZARD_HINT_NARROW_WIDTH = 72
WIZARD_ART_WIDTH = 120
WIZARD_ART_HEIGHT = 40
WIZARD_COMPACT_HEIGHT = 12
WIZARD_COMPACT_WIDTH = 48
WIZARD_ART_NAME = "shikiru_init_ascii_art.txt"
ART_REVEAL_SECONDS = 5
FIELD_EDIT_HINT = "Tab / Enter edit"
NARROW_AGENT_HINT = "Tab / Enter select"
NARROW_CANDIDATE_HINT = "Enter add"
WIZARD_HINTS = (
    "",
    "↑↓ select scope · Enter edit",
    "↑↓ select · Enter add",
    "↑↓ select · Enter add",
    FIELD_EDIT_HINT,
    "Tab / Enter choose",
    "Enter run Doctor",
)
SCROLL_HINT = "PgUp/PgDn scroll"
EXIT_HINT = "Enter Exit"
PATH_SEPARATOR = ","
NO_CANDIDATES = "No detected candidates."
DETAIL_LABEL_WIDTH = 15
MINUS_SIGN = "\N{MINUS SIGN}"

WIZARD_LAYER_CSS = f"""
#wizard-art {{
    layer: art; position: absolute; offset: 0 0;
    width: {WIZARD_ART_WIDTH}; height: {WIZARD_ART_HEIGHT}; overflow: hidden;
}}
#wizard-frame {{
    layer: panel; width: auto; min-width: 0; max-width: {WIZARD_FRAME_MAX_WIDTH};
    height: {WIZARD_FRAME_HEIGHT}; min-height: {WIZARD_FRAME_HEIGHT};
    max-height: 100%; margin: 0; padding: 1 2;
    background: $surface; color: $primary-text;
}}
"""


def _wizard_art() -> Text:
    contents = (
        resources.files("shikiru.dashboard").joinpath(WIZARD_ART_NAME).read_text(encoding="utf-8")
    )
    art = Text.from_ansi(contents)
    art.no_wrap = True
    return art


def _step_counter(step: int) -> str:
    return f"{step + 1} / {len(WIZARD_STEPS)}"


def _step_title(step: int) -> Text:
    title = Text()
    title.append(WIZARD_TITLE, style=f"{STYLE_BOLD} {STEEL_BLUE}")
    title.append(" · ", style=PRIMARY_TEXT_COLOR)
    title.append(WIZARD_STEPS[step], style=f"{STYLE_BOLD} {PRIMARY_TEXT_COLOR}")
    return title


class WizardDetailRow(Horizontal):
    """Render read-only facts with a fixed label and wrapping value column."""

    def __init__(
        self,
        label: str,
        value: str | Text,
        value_style: str = PRIMARY_TEXT_COLOR,
        *,
        widget_id: str | None = None,
    ) -> None:
        super().__init__(id=widget_id, classes="wizard-detail-row")
        self._label = label
        self._value = value if isinstance(value, Text) else Text(value, style=value_style)

    def compose(self) -> ComposeResult:
        yield Static(
            Text(self._label, style=SECONDARY_TEXT_COLOR),
            classes="wizard-detail-label",
            markup=False,
        )
        yield Static(self._value, classes="wizard-detail-value", markup=False)

    def update_value(self, value: str | Text) -> None:
        """Replace the complete read-only value while keeping its label column."""
        self._value = value if isinstance(value, Text) else Text(value, style=PRIMARY_TEXT_COLOR)
        self.query_one(".wizard-detail-value", Static).update(self._value)


def _detail_row(
    label: str,
    value: str | Text,
    value_style: str = PRIMARY_TEXT_COLOR,
    *,
    widget_id: str | None = None,
) -> WizardDetailRow:
    if isinstance(value, str):
        value = Text(value, style=value_style)
    return WizardDetailRow(label, value, widget_id=widget_id)


def _section_heading(value: str) -> Text:
    return Text(value, style=f"{STYLE_BOLD} {STEEL_BLUE}")


def _candidate_add_id(field_id: str, index: int) -> str:
    return f"{field_id}-candidate-add-{index}"


def _repository_checkout(
    base: str,
    *,
    clean: bool,
    changes: LineChangeStats | None = None,
    error: str | None = None,
) -> Text:
    checkout = Text()
    checkout.append(base, style=PRIMARY_TEXT_COLOR)
    checkout.append(" · ", style=SECONDARY_TEXT_COLOR)
    if clean:
        checkout.append("clean", style=f"{STYLE_BOLD} {SUCCESS_COLOR}")
    elif changes is None and error is None:
        checkout.append("changed · loading line totals…", style=PRIMARY_TEXT_COLOR)
    elif changes is None:
        checkout.append(
            f"changed · line totals unavailable: {error or 'unknown error'}",
            style=f"{STYLE_BOLD} {AMBER}",
        )
    else:
        checkout.append(f"+{changes.added_lines}", style=SUCCESS_COLOR)
        checkout.append("  ", style=SECONDARY_TEXT_COLOR)
        checkout.append(f"{MINUS_SIGN}{changes.deleted_lines}", style=ALERT_COLOR)
        details: list[str] = []
        if changes.untracked_paths:
            label = "path" if changes.untracked_paths == 1 else "paths"
            details.append(f"{changes.untracked_paths} untracked {label}")
        if changes.binary_entries:
            label = "entry" if changes.binary_entries == 1 else "entries"
            details.append(f"{changes.binary_entries} binary {label}")
        if details:
            checkout.append(f" · {' · '.join(details)}", style=AMBER)
    return checkout


class _CandidatePath(Static):
    class Chosen(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, path: str, index: int) -> None:
        super().__init__(path, markup=False, classes="candidate-path")
        self._index = index

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self._index))


class CandidateRows(Vertical, can_focus=True):
    """Render detected paths beside their own Add actions, wrapping paths in place."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("enter", "open", "Add", show=False),
    ]

    class Opened(Message):
        """The selected candidate was added."""

        def __init__(self, candidate_rows: CandidateRows, index: int) -> None:
            super().__init__()
            self._candidate_rows = candidate_rows
            self.index = index

        @property
        def control(self) -> CandidateRows:
            """The candidate rows that posted this message."""
            return self._candidate_rows

    def __init__(self, widget_id: str, field_id: str, candidates: tuple[str, ...]) -> None:
        super().__init__(id=widget_id, classes="candidate-list")
        self._field_id = field_id
        self._candidates = candidates
        self._selected = 0
        self._rows: list[Horizontal] = []

    @property
    def candidates(self) -> tuple[str, ...]:
        """Detected project-relative paths in display order."""
        return self._candidates

    def compose(self) -> ComposeResult:
        if not self._candidates:
            yield Static(NO_CANDIDATES, classes="candidate-empty", markup=False)
        for index, path in enumerate(self._candidates):
            with Horizontal(classes="wizard-candidate-row") as row:
                row.set_class(index == self._selected, "-selected")
                yield _CandidatePath(path, index)
                yield Button(
                    "Add",
                    id=_candidate_add_id(self._field_id, index),
                    variant="primary",
                )
            self._rows.append(row)

    @on(_CandidatePath.Chosen)
    def _path_chosen(self, message: _CandidatePath.Chosen) -> None:
        message.stop()
        self._choose(message.index)
        self.action_open()

    def set_included(self, included: set[str]) -> None:
        """Disable Add for paths already included in the editable field."""
        for index, path in enumerate(self._candidates):
            button = self.query_one(f"#{_candidate_add_id(self._field_id, index)}", Button)
            button.disabled = path in included

    def action_move(self, step: int) -> None:
        """Move the selected candidate within the detected paths."""
        self._choose(min(max(self._selected + step, 0), max(len(self._candidates) - 1, 0)))

    def action_open(self) -> None:
        """Add the selected candidate to its editable path field."""
        if self._candidates:
            self.post_message(self.Opened(self, self._selected))

    def _choose(self, index: int) -> None:
        if not 0 <= index < len(self._candidates) or index == self._selected:
            return
        self._selected = index
        for row_index, row in enumerate(self._rows):
            row.set_class(row_index == index, "-selected")
        self._rows[index].scroll_visible(animate=False)


class InitWizardBody(VerticalScroll):
    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.scroll_to_widget(event.widget, animate=False, immediate=True)


class InitWizardApp(App[InitWizardResult | None]):
    """Guide an operator through project setup and show the final Doctor checks."""

    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "block_quit", show=False, priority=True),
        Binding("ctrl+f", "show_art", "Show artwork", show=False),
        ("pageup", "scroll_body_up", "Scroll up"),
        ("pagedown", "scroll_body_down", "Scroll down"),
    ]
    CSS = (
        POPUP_CSS
        + WIZARD_LAYER_CSS
        + """
    Screen {
        layers: art panel;
        align: center middle;
        border: solid $neutral-border;
        background: $background;
        overflow: hidden;
    }
    #wizard-header { width: 100%; height: 1; }
    #wizard-title { width: 1fr; height: 1; color: $primary-text; text-style: bold; }
    #wizard-step { width: auto; height: 1; color: $steel-blue; text-style: bold; }
    #wizard-body {
        height: auto; max-height: 1fr; min-height: 0; padding: 1 0 0 0;
        background: $surface; color: $primary-text;
        scrollbar-size-vertical: $scrollbar-width;
    }
    #wizard-frame.stable > #wizard-body { height: 1fr; min-height: 0; }
    .wizard-page { height: auto; padding: 0; }
    #wizard-frame.height-clamped { min-height: 0; }
    #wizard-frame.compact { padding: 0 1; }
    #wizard-frame.compact > #wizard-header { height: auto; min-height: 1; align: left top; }
    #wizard-frame.compact #wizard-title { height: auto; min-height: 1; }
    #wizard-frame.compact > #wizard-body { padding: 0; }
    #wizard-frame.narrow #wizard-hint-left { height: 2; min-height: 2; }
    #wizard-finish-before, #wizard-finish-after { height: auto; min-height: 0; padding: 0; }
    #wizard-frame Select { height: 1; margin: 0; }
    .wizard-description { margin-bottom: 1; }
    .wizard-page Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
    }
    .wizard-page Static { height: auto; color: $primary-text; }
    #catalog-discovery, #wizard-error { height: auto; }
    #wizard-error { color: $dusty-rose; text-style: bold; }
    #wizard-navigation-hint {
        layout: grid; grid-size: 2 0; grid-columns: 1fr auto; grid-gutter: 0 1;
        height: auto; min-height: 1; width: 100%;
    }
    #wizard-hint-left { width: 100%; height: auto; min-height: 1; color: $secondary-text; }
    #wizard-hint-right {
        width: auto; height: 1; color: $secondary-text; text-align: right;
    }
    #wizard-navigation { height: 1; min-height: 1; width: 100%; align: right middle; }
    #wizard-navigation Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        margin: 0 1 0 0; padding: 0 1; border: none;
        border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-navigation Button:focus {
        color: $primary-text; background: $selected-surface; text-style: none;
        border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-navigation Button:disabled {
        color: $secondary-text;
        border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    }
    #wizard-navigation Button.-primary {
        color: $steel-blue; background: $surface; text-style: bold;
    }
    #wizard-navigation Button.-primary:focus {
        color: $steel-blue; background: $selected-surface; text-style: bold;
    }
    #wizard-navigation Button.-primary:disabled {
        color: $secondary-text; background: $surface; text-style: none;
    }
    .candidate-list { width: 100%; height: auto; }
    .wizard-candidate-row { width: 100%; height: auto; }
    .wizard-candidate-row.-selected { background: $selected-surface; }
    .wizard-candidate-row .candidate-path { width: 1fr; height: auto; }
    #wizard-frame .wizard-candidate-row Button {
        width: auto; min-width: 4; height: 1; min-height: 1;
        margin: 0; padding: 0 1; border: none;
        color: $steel-blue; background: $surface; text-style: bold;
    }
    #wizard-frame .wizard-candidate-row Button:focus {
        border: none; color: $primary-text; background: $selected-surface; text-style: bold;
    }
    #wizard-frame .wizard-candidate-row Button:disabled {
        border: none; color: $secondary-text; background: $surface; text-style: none;
    }
    #wizard-page-2 .section-heading, #wizard-page-3 .section-heading {
        height: auto; color: $steel-blue; text-style: bold;
    }
    #wizard-cow-result { height: auto; }
    """
        + LADDER_ACTION_CSS
        + POPUP_COMPACT_CONTROL_CSS
        + """
    .wizard-page .palette-row Label { color: $secondary-text; text-style: none; }
    .wizard-page .wizard-long-label Label { height: auto; }
    """
        + f"""
    #wizard-frame .wizard-wide-label Label {{ width: {DETAIL_LABEL_WIDTH}; }}
    #wizard-frame .wizard-long-label Label {{ width: {DETAIL_LABEL_WIDTH}; }}
    .orchestration-field {{ height: auto; margin-bottom: 1; }}
    .wizard-page .orchestration-unit {{
        width: auto; height: 1; padding: 0 1; color: $secondary-text;
    }}
    .wizard-page .orchestration-help {{
        height: auto; padding-left: {DETAIL_LABEL_WIDTH}; color: $secondary-text;
    }}
    .wizard-detail-row {{ width: 100%; height: auto; }}
    .wizard-detail-row .wizard-detail-label {{
        width: {DETAIL_LABEL_WIDTH}; height: auto; color: $secondary-text; text-style: none;
    }}
    .wizard-detail-row .wizard-detail-value {{ width: 1fr; height: auto; }}
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
        self._config_target = project_config_path(self._root)
        self._project = project
        self._detectors = detectors
        self._doctor = doctor
        self._doctor_renderer = doctor_renderer
        self._orchestration_defaults = orchestration_defaults
        self._step = 0
        self._cow_supported: bool | None = None
        self._config_path: Path | None = None
        self._doctor_report: DoctorReport | None = None
        self._doctor_report_width: int | None = None
        self._doctor_error: str | None = None
        self._error = ""
        self._model_catalogs: dict[str, ModelCatalog] = {}
        self._catalog_errors: dict[str, str] = {}
        self._agent_capabilities: dict[str, AgentCapabilityResult] = {}
        self._catalogs_loaded = False
        self._frame_ready = False
        self._writing_config = False
        self._finishing = False
        self._last_focus_by_step: dict[int, str] = {}
        self._art_timer: Timer | None = None
        self._art_focus_id = ""
        self._art_scroll_y = 0.0

    def compose(self) -> ComposeResult:
        art = _wizard_art()
        yield Static(art, id="wizard-art", markup=False)
        with PopupFrame(widget_id="wizard-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS, id="wizard-header"):
                yield Static(
                    _step_title(self._step),
                    id="wizard-title",
                    classes=POPUP_TITLE_CLASS,
                    markup=False,
                )
                yield Static(_step_counter(self._step), id="wizard-step", markup=False)
            yield Rule(classes=POPUP_RULE_CLASS, id="wizard-rule")
            with InitWizardBody(id="wizard-body", classes=POPUP_BODY_CLASS):
                yield from self._repository_page()
                with Vertical(id="wizard-page-1", classes="wizard-page"):
                    yield LadderEditor()
                yield from self._trackers_page()
                yield from self._caches_page()
                yield from self._orchestration_page()
                yield from self._integration_page()
                yield from self._finish_page()
                yield Static("", id="wizard-error", markup=False)
            with Horizontal(id="wizard-navigation-hint"):
                yield Static("", id="wizard-hint-left", markup=False)
                right_hint = Text()
                right_hint.append("Ctrl+F", style=f"{STYLE_BOLD} {STEEL_BLUE}")
                right_hint.append(" ")
                right_hint.append("富士山", style=SECONDARY_TEXT_COLOR)
                yield Static(right_hint, id="wizard-hint-right", markup=False)
            yield Rule(classes=POPUP_RULE_CLASS, id="wizard-navigation-rule")
            with Horizontal(id="wizard-navigation"):
                yield Button("Back", id="wizard-back")
                yield Button("Next", variant="primary", id="wizard-next")
                yield Button("Run doctor", variant="primary", id="wizard-finish")
                yield Button("Cancel", id="wizard-cancel")

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._adapt_to_viewport)

    def _adapt_to_viewport(self) -> None:
        frame = self.query_one("#wizard-frame", PopupFrame)
        compact = (
            self.size.height <= WIZARD_COMPACT_HEIGHT or self.size.width <= WIZARD_COMPACT_WIDTH
        )
        frame.set_class(compact, "compact")
        frame.set_class(self.size.width <= WIZARD_HINT_NARROW_WIDTH, "narrow")
        frame.set_class(self.size.height < WIZARD_FRAME_HEIGHT, "height-clamped")
        frame.update_preferred_size()
        if self._config_path is not None:
            self.call_after_refresh(self._update_doctor_report)
        self.call_after_refresh(self._update_navigation_hint)

    def on_mount(self) -> None:
        self.query_one("#wizard-frame", PopupFrame).visible = False
        for input_widget in self.query(Input):
            input_widget.cursor_blink = False
        self._update_pages()
        self._refresh_candidate_rows()
        self.call_after_refresh(self._capture_agent_page_size)
        self.run_worker(self._load_model_catalogs(), group="model-catalogs", exclusive=True)
        if not self._project.worktree_clean:
            self.run_worker(
                self._load_checkout_line_changes(), group="checkout-lines", exclusive=True
            )

    def on_unmount(self) -> None:
        self._stop_art_timer()

    def _capture_agent_page_size(self) -> None:
        editor = self.query_one("#ladder-editor", LadderEditor)
        editor.set_scope(Role.HELPER)
        self._step = AGENTS_STEP_INDEX
        self._update_pages()
        self.call_after_refresh(self._mark_frame_content_ready)

    def _mark_frame_content_ready(self) -> None:
        frame = self.query_one("#wizard-frame", PopupFrame)
        frame.content_ready()
        self.call_after_refresh(self._capture_completed_frame_size)

    def _capture_completed_frame_size(self) -> None:
        self.query_one("#wizard-frame", PopupFrame).update_preferred_size()
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
        self._adapt_to_viewport()
        self.call_after_refresh(self._focus_step)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self.query_one("#wizard-frame", PopupFrame).visible:
            return
        button_id = event.button.id or ""
        for field_id, _, candidates in self._candidate_rows():
            for index, candidate in enumerate(candidates):
                if button_id == _candidate_add_id(field_id, index):
                    self._add_candidate(field_id, candidate)
                    event.stop()
                    return
        match button_id:
            case "wizard-back":
                await self._move(-1)
            case "wizard-next":
                await self._move(1)
            case "wizard-finish":
                if self._config_path is None:
                    await self._finish()
                else:
                    self._stop_art_timer()
                    self.exit(self._result())
            case "wizard-cancel":
                self.action_cancel()
            case _:
                return

    @on(Input.Changed)
    def _input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {TRACKERS_INPUT_ID, CACHES_INPUT_ID}:
            self._refresh_candidate_rows()
            self.call_after_refresh(self._update_navigation_hint)
        self._update_finish_summary()

    @on(Select.Changed)
    def _select_changed(self, event: Select.Changed) -> None:
        if event.select.id == INTEGRATION_SELECT_ID:
            self._update_finish_summary()

    @on(CandidateRows.Opened)
    def _candidate_opened(self, message: CandidateRows.Opened) -> None:
        list_id = message.control.id
        if list_id == TRACKERS_LIST_ID:
            field_id = TRACKERS_INPUT_ID
        elif list_id == CACHES_LIST_ID:
            field_id = CACHES_INPUT_ID
        else:
            return
        message.stop()
        self._add_candidate(field_id, message.control.candidates[message.index])

    def _add_candidate(self, field_id: str, candidate: str) -> None:
        field = self.query_one(f"#{field_id}", Input)
        entries = [entry.strip() for entry in field.value.split(PATH_SEPARATOR) if entry.strip()]
        if candidate not in entries:
            entries.append(candidate)
            field.value = f"{PATH_SEPARATOR} ".join(entries)
            field.cursor_position = len(field.value)
        self._refresh_candidate_rows()
        self.call_after_refresh(field.focus)

    def _refresh_candidate_rows(self) -> None:
        for field_id, list_id, _candidates in self._candidate_rows():
            input_widget = self.query_one(f"#{field_id}", Input)
            candidate_list = self.query_one(f"#{list_id}", CandidateRows)
            included = {
                entry.strip() for entry in input_widget.value.split(PATH_SEPARATOR) if entry.strip()
            }
            candidate_list.set_included(included)

    def _candidate_rows(
        self,
    ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        return (
            (TRACKERS_INPUT_ID, TRACKERS_LIST_ID, self._project.trackers),
            (CACHES_INPUT_ID, CACHES_LIST_ID, self._project.caches),
        )

    async def _load_checkout_line_changes(self) -> None:
        try:
            changes = await self._detectors.working_tree_line_changes()
        except (OSError, ShikiruError) as error:
            checkout = _repository_checkout(
                self._project.base,
                clean=False,
                error=str(error) or type(error).__name__,
            )
        else:
            checkout = _repository_checkout(
                self._project.base,
                clean=False,
                changes=changes,
            )
        if self._config_path is not None:
            return
        self.query_one(f"#{REPOSITORY_CHECKOUT_ID}", WizardDetailRow).update_value(checkout)

    def action_show_art(self) -> None:
        if self._art_timer is not None:
            self._stop_art_timer()
            self._art_timer = self.set_timer(ART_REVEAL_SECONDS, self._restore_art_panel)
            return
        focused = self.focused
        if focused is not None and (
            isinstance(focused, (Input, Select))
            or any(isinstance(ancestor, Select) for ancestor in focused.ancestors)
        ):
            return
        if any(select.expanded for select in self.query(Select[str])):
            return
        frame = self.query_one("#wizard-frame", PopupFrame)
        if not frame.visible:
            return
        body = self.query_one("#wizard-body", InitWizardBody)
        self._art_focus_id = focused.id if focused is not None and focused.id else ""
        self._art_scroll_y = body.scroll_y
        frame.visible = False
        self._art_timer = self.set_timer(ART_REVEAL_SECONDS, self._restore_art_panel)

    def _restore_art_panel(self) -> None:
        self._art_timer = None
        self.query_one("#wizard-frame", PopupFrame).visible = True
        self.call_after_refresh(self._restore_art_state)

    def _restore_art_state(self) -> None:
        if self._art_focus_id:
            target = self.query(f"#{self._art_focus_id}")
            if target:
                target.first().focus(scroll_visible=False)
        self.call_after_refresh(self._restore_art_scroll)

    def _restore_art_scroll(self) -> None:
        body = self.query_one("#wizard-body", InitWizardBody)
        body.scroll_to(
            y=min(max(self._art_scroll_y, 0), body.max_scroll_y),
            animate=False,
            immediate=True,
        )
        self._update_navigation_hint()

    def _stop_art_timer(self) -> None:
        if self._art_timer is not None:
            self._art_timer.stop()
            self._art_timer = None

    def _update_navigation_hint(self) -> None:
        self.call_after_refresh(self._render_navigation_hint)

    def _render_navigation_hint(self) -> None:
        text = EXIT_HINT if self._config_path is not None else self._step_hint()
        body = self.query_one("#wizard-body", InitWizardBody)
        overflows = body.max_scroll_y > 0
        if self.size.width <= WIZARD_HINT_NARROW_WIDTH:
            hint = Group(key_hint(text), key_hint(SCROLL_HINT)) if overflows else key_hint(text)
        else:
            if overflows:
                text = f"{text} · {SCROLL_HINT}" if text else SCROLL_HINT
            hint = key_hint(text)
        self.query_one("#wizard-hint-left", Static).update(hint)

    def _step_hint(self) -> str:
        if self._step == TRACKERS_STEP_INDEX and not self._project.trackers:
            return FIELD_EDIT_HINT
        if self._step == COW_STEP_INDEX and not self._project.caches:
            return FIELD_EDIT_HINT
        if self.size.width <= WIZARD_HINT_NARROW_WIDTH:
            if self._step == AGENTS_STEP_INDEX:
                return NARROW_AGENT_HINT
            if self._step in {TRACKERS_STEP_INDEX, COW_STEP_INDEX}:
                return NARROW_CANDIDATE_HINT
        return WIZARD_HINTS[self._step]

    def _update_finish_summary(self) -> None:
        integration_value = cast(
            "Select[str]", self.query_one(f"#{INTEGRATION_SELECT_ID}", Select)
        ).value
        integration = (
            INTEGRATION_LABELS[integration_value]
            if isinstance(integration_value, str)
            else INTEGRATION_UNSELECTED
        )
        parts = (*(self._field_summary(field) for field in ORCHESTRATION_FIELDS), integration)
        self.query_one("#wizard-summary", WizardDetailRow).update_value(" · ".join(parts))

    def _field_summary(self, field: OrchestrationField) -> str:
        try:
            value = field.parse(self.query_one(f"#{field.input_id}", Input).value)
        except OrchestrationFieldError:
            return f"{field.summary} needs a value"
        return f"{field.summary} {value} {field.unit}"

    def action_cancel(self) -> None:
        if self._writing_config:
            return
        self._stop_art_timer()
        if self._config_path is None:
            self.exit(None)
        else:
            self.exit(self._result())

    def action_block_quit(self) -> None:
        return

    def action_scroll_body_up(self) -> None:
        self.query_one("#wizard-body", InitWizardBody).action_page_up()

    def action_scroll_body_down(self) -> None:
        self.query_one("#wizard-body", InitWizardBody).action_page_down()

    async def _load_model_catalogs(self) -> None:
        for agent in self._project.agents:
            self._agent_capabilities[agent] = self._detectors.agent_capabilities(agent, {})
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
        self.query_one("#ladder-editor", LadderEditor).set_model_catalogs(
            self._model_catalogs, self._catalog_errors
        )
        self.query_one("#ladder-editor", LadderEditor).set_agent_capabilities(
            self._agent_capabilities
        )
        self.query_one("#catalog-discovery", Static).display = False
        self._catalogs_loaded = True
        self._update_pages()

    async def _move(self, direction: int) -> None:
        if (
            not self.query_one("#wizard-frame", PopupFrame).visible
            or self._config_path is not None
            or self._writing_config
            or self._finishing
        ):
            return
        if direction > 0 and self._step == REPOSITORY_STEP_INDEX and not self._catalogs_loaded:
            return
        next_step = self._step + direction
        if not 0 <= next_step < len(WIZARD_STEPS):
            return
        self._stop_art_timer()
        if direction > 0 and self._step == AGENTS_STEP_INDEX:
            try:
                self._read_ladders()
            except ConfigError as error:
                self._show_step_error(AGENTS_STEP_INDEX, "ladder-input", error)
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
        if self._step == REPOSITORY_STEP_INDEX:
            self.query_one("#wizard-next", Button).focus()
        elif self._step == AGENTS_STEP_INDEX:
            self.query_one("#ladder-scopes", RowList).focus()
        elif self._step == TRACKERS_STEP_INDEX:
            self._focus_candidate_list(TRACKERS_LIST_ID, TRACKERS_INPUT_ID)
        elif self._step == COW_STEP_INDEX:
            self._focus_candidate_list(CACHES_LIST_ID, CACHES_INPUT_ID)
        elif self._step == ORCHESTRATION_STEP_INDEX:
            self.query_one(f"#{MAX_PARALLEL.input_id}", Input).focus()
        elif self._step == INTEGRATION_STEP_INDEX:
            self.query_one(f"#{INTEGRATION_SELECT_ID}", Select).focus()
        elif self._step == FINISH_STEP_INDEX:
            self.query_one("#wizard-finish", Button).focus()

    def _focus_candidate_list(self, list_id: str, input_id: str) -> None:
        candidate_list = self.query_one(f"#{list_id}", CandidateRows)
        if candidate_list.candidates:
            candidate_list.focus()
        else:
            self.query_one(f"#{input_id}", Input).focus()

    async def _probe_cow(self) -> None:
        try:
            self._cow_supported = await self._detectors.check_cow_support()
        except (OSError, ShikiruError) as error:
            self._error = f"CoW check failed: {error}"
            self.query_one("#cow-result", WizardDetailRow).update_value(
                Text(self._error, style=f"{STYLE_BOLD} {ALERT_COLOR}")
            )

    async def _finish(self) -> None:
        if self._finishing:
            return
        try:
            ladders = self._read_ladders()
        except ConfigError as error:
            self._show_step_error(AGENTS_STEP_INDEX, "ladder-input", error)
            return
        try:
            trackers = self._read_paths(TRACKERS_INPUT_ID)
        except ConfigError as error:
            self._show_step_error(TRACKERS_STEP_INDEX, TRACKERS_INPUT_ID, error)
            return
        try:
            caches = self._read_paths(CACHES_INPUT_ID)
        except ConfigError as error:
            self._show_step_error(COW_STEP_INDEX, CACHES_INPUT_ID, error)
            return
        try:
            integration = self._read_integration()
        except ConfigError as error:
            self._show_step_error(INTEGRATION_STEP_INDEX, INTEGRATION_SELECT_ID, error)
            return
        try:
            orchestration = self._read_orchestration(integration)
        except OrchestrationFieldError as error:
            self._show_step_error(ORCHESTRATION_STEP_INDEX, error.input_id, error)
            return
        self._finishing = True
        self.query_one("#wizard-finish", Button).disabled = True
        try:
            self._writing_config = True
            config_path = await self._detectors.initialize_config(
                trackers=trackers,
                caches=caches,
                ladders=ladders.ladders,
                complexity=ladders.complexity,
                orchestration=orchestration,
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
        finally:
            self._finishing = False
        self._update_pages()

    def _show_step_error(self, step: int, focus_id: str, error: ConfigError) -> None:
        self._step = step
        self._error = ""
        self._update_pages()
        if step == AGENTS_STEP_INDEX:
            self.query_one("#ladder-editor", LadderEditor).show_validation_error(str(error))
        else:
            self._show_error(str(error))
        self.call_after_refresh(lambda: self.query_one(f"#{focus_id}").focus())

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
        entries = tuple(item.strip() for item in value.split(PATH_SEPARATOR))
        if any(not item for item in entries):
            message = "remove empty comma-separated entries"
            raise ConfigError(message)
        return entries

    def _read_field(self, field: OrchestrationField) -> int | float:
        return field.parse(self.query_one(f"#{field.input_id}", Input).value)

    def _read_orchestration(self, integration: str) -> OrchestrationChoices:
        return OrchestrationChoices(
            max_parallel=int(self._read_field(MAX_PARALLEL)),
            max_iterations=int(self._read_field(MAX_ITERATIONS)),
            integration=integration,
            push_interval_seconds=int(self._read_field(PUSH_INTERVAL)),
            max_quota_wait=int(self._read_field(MAX_QUOTA_WAIT)),
            stall=int(self._read_field(STALL_TIMEOUT)),
            tool=int(self._read_field(TOOL_TIMEOUT)),
            max_cpu_pct=int(self._read_field(MAX_CPU)),
            min_free_ram_gb=float(self._read_field(MIN_FREE_RAM)),
        )

    def _read_integration(self) -> str:
        value = cast("Select[str]", self.query_one(f"#{INTEGRATION_SELECT_ID}", Select)).value
        if not isinstance(value, str) or value not in INTEGRATION_LABELS:
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
        self.query_one("#wizard-title", Static).update(_step_title(self._step))
        self.query_one("#wizard-step", Static).update(_step_counter(self._step))
        for index in range(len(WIZARD_STEPS)):
            self.query_one(f"#wizard-page-{index}").display = index == self._step
        self.query_one("#wizard-body", InitWizardBody).scroll_home(animate=False)
        back = self.query_one("#wizard-back", Button)
        back.display = self._config_path is None
        back.disabled = self._step == 0 or self._finishing
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
        finish_button.label = "Exit" if self._config_path is not None else "Run doctor"
        finish_button.disabled = self._finishing
        if self.focused is next_button and not next_button.display:
            finish_button.focus()
        cancel = self.query_one("#wizard-cancel", Button)
        cancel.display = self._config_path is None
        cancel.disabled = self._writing_config
        cow_result = self.query_one("#cow-result", WizardDetailRow)
        if self._cow_supported is not None:
            cow_result.update_value(self._cow_status())
        wizard_error = self.query_one("#wizard-error", Static)
        wizard_error.update(self._error if self._config_path is None else "")
        wizard_error.display = self._config_path is None and bool(self._error)
        self._update_finish_summary()
        self.query_one("#wizard-finish-before").display = self._config_path is None
        self.query_one("#wizard-finish-after").display = self._config_path is not None
        if self._config_path is None:
            self._update_doctor_report()
        else:
            self.call_after_refresh(self._update_doctor_report)
        self.call_after_refresh(self._update_navigation_hint)

    def _update_doctor_report(self) -> None:
        report_widget = self.query_one("#doctor-report", Static)
        if self._config_path is None:
            report_widget.update("")
            self._doctor_report_width = None
            return
        width = report_widget.content_region.width
        if width < 1:
            return
        if width == self._doctor_report_width:
            self.call_after_refresh(self._update_navigation_hint)
            return
        self._doctor_report_width = width
        self._render_doctor_report(report_widget, width)
        self.call_after_refresh(self._settle_doctor_report_width)

    def _settle_doctor_report_width(self) -> None:
        report_widget = self.query_one("#doctor-report", Static)
        width = report_widget.content_region.width
        if width > 0 and width != self._doctor_report_width:
            self._doctor_report_width = width
            self._render_doctor_report(report_widget, width)
        self.call_after_refresh(self._update_navigation_hint)

    def _render_doctor_report(self, report_widget: Static, width: int) -> None:
        self.query_one("#doctor-config", WizardDetailRow).update_value(str(self._config_path))
        if self._doctor_report is not None:
            report_widget.update(self._doctor_renderer(self._doctor_report, width=width))
        else:
            detail = self._doctor_error or "Doctor did not return a report."
            report_widget.update(Text(detail, style=ALERT_COLOR))

    def _cow_status(self) -> Text:
        status = Text()
        if self._cow_supported is None:
            status.append("checking this project filesystem", style=PRIMARY_TEXT_COLOR)
        elif self._cow_supported:
            status.append(
                "supported on this project filesystem", style=f"{STYLE_BOLD} {SUCCESS_COLOR}"
            )
        else:
            status.append(
                "unavailable · cache copies will be skipped",
                style=f"{STYLE_BOLD} {AMBER}",
            )
        return status

    def _repository_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-0", classes="wizard-page"):
            yield Static(
                "Read-only facts from the current checkout.",
                classes="wizard-description",
                markup=False,
            )
            yield _detail_row("Project", str(self._root))
            yield _detail_row("Base", self._project.base)
            yield _detail_row("Config target", str(self._config_target))
            yield _detail_row(
                "Checkout",
                _repository_checkout(self._project.base, clean=self._project.worktree_clean),
                widget_id=REPOSITORY_CHECKOUT_ID,
            )
            yield Static("Discovering supported agent model choices…", id="catalog-discovery")

    def _trackers_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-2", classes="wizard-page"):
            yield Static(
                "Tracker files start blank. Select project-relative Markdown candidates "
                "used as task trackers; an empty field sends no tracker files to tasks.",
                classes="wizard-description",
                markup=False,
            )
            yield Static(_section_heading("Detected tracker files"), classes="section-heading")
            yield CandidateRows(TRACKERS_LIST_ID, TRACKERS_INPUT_ID, self._project.trackers)
            with Horizontal(classes="palette-row wizard-wide-label"):
                yield Label("Tracker files")
                yield Input(id=TRACKERS_INPUT_ID)

    def _caches_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-3", classes="wizard-page"):
            yield Static(
                "Cache paths start blank and are registered for task worktrees. Select "
                "detected ignored directories to add; an empty field copies no caches. "
                "CoW is checked on this project filesystem.",
                classes="wizard-description",
                markup=False,
            )
            yield Static(_section_heading("Detected cache directories"), classes="section-heading")
            yield _detail_row("Copy-on-write", self._cow_status(), widget_id="cow-result")
            yield CandidateRows(CACHES_LIST_ID, CACHES_INPUT_ID, self._project.caches)
            with Horizontal(classes="palette-row wizard-wide-label"):
                yield Label("Cache paths")
                yield Input(id=CACHES_INPUT_ID)

    def _orchestration_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-4", classes="wizard-page"):
            yield Static(
                "Set how many tasks run at once and when new work may start. "
                "Each field starts at the project default.",
                classes="wizard-description",
                markup=False,
            )
            for field in ORCHESTRATION_FIELDS:
                yield self._orchestration_field(field)

    def _orchestration_field(self, field: OrchestrationField) -> Vertical:
        return Vertical(
            Horizontal(
                Label(field.label),
                Input(
                    value=str(field.default(self._orchestration_defaults)),
                    id=field.input_id,
                ),
                Static(field.unit, classes="orchestration-unit", markup=False),
                classes="palette-row wizard-long-label",
            ),
            Static(field.explanation, classes="orchestration-help", markup=False),
            classes="orchestration-field",
        )

    def _integration_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-5", classes="wizard-page"):
            yield Static(
                "Choose how completed work is integrated back into the repository.",
                classes="wizard-description",
                markup=False,
            )
            with Horizontal(classes="palette-row wizard-long-label"):
                yield Label("Integration mode")
                yield Select(
                    INTEGRATION_OPTIONS,
                    value=self._orchestration_defaults.integration,
                    allow_blank=False,
                    id=INTEGRATION_SELECT_ID,
                )
            yield _detail_row("Local merge", "merge a finished task branch locally")
            yield _detail_row(
                "Local squash and merge", "land a finished task as one commit on the base"
            )
            yield _detail_row("Pull request", "open a pull request for review")

    def _finish_page(self) -> ComposeResult:
        with Vertical(id="wizard-page-6", classes="wizard-page"):
            with Vertical(id="wizard-finish-before"):
                yield Static(
                    "Finish writes .shikiru/config.toml, excludes .shikiru/ in Git, "
                    "and runs Doctor.",
                    id="wizard-finish-hint",
                    classes="wizard-description",
                    markup=False,
                )
                yield _detail_row(
                    "Config", str(self._config_target), widget_id="wizard-config-target"
                )
                yield _detail_row("Summary", "", widget_id="wizard-summary")
            with Vertical(id="wizard-finish-after"):
                yield _detail_row("Config", str(self._config_target), widget_id="doctor-config")
                yield Static("", id="doctor-report", markup=False)
