"""The shared Textual operator palette for the popup and dashboard (06 §5.5)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, cast, get_args

from rich.text import Text
from textual.app import App
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Input, Label, Rule, Select, Static, TextArea

from shikiru.agents.contracts import parse_rung
from shikiru.config.settings import ComplexityName
from shikiru.dashboard.contract import PaletteDraft, PaletteRequest, TaskPaletteRoute
from shikiru.domain.commands import CommandKind, CommandStatus, ResolutionKind
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.escalation_evidence import EscalationPacket, parse_escalation_packet
from shikiru.domain.tasks import Mode, Role
from shikiru.errors import ShikiruError
from shikiru.operator.commands import (
    ACK_TIMEOUT_MESSAGE,
    QUEUED_MESSAGE,
    AckStatus,
)
from shikiru.operator.evidence_export import (
    EXPORT_FORMAT_JSON,
    EXPORT_FORMAT_MARKDOWN,
    TaskEvidenceExporter,
)
from shikiru.operator.queries import (
    TASK_ACTION_OTHER_ROLE_TAKEN_OVER,
    TASK_ACTION_ROLES,
    OperatorTaskAction,
)
from shikiru.ui.compact import display_escalation_headline
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    prepare_popup,
)
from shikiru.ui.screen import widget_is_attached, widget_is_on_screen
from shikiru.ui.theme import install_theme
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR, STEEL_BLUE

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult
    from textual.worker import Worker

    from shikiru.agents.contracts import Rung
    from shikiru.dashboard.contract import OnChangeRegistration
    from shikiru.domain.commands import Command
    from shikiru.operator.commands import Ack, OperatorCommands
    from shikiru.operator.evidence_export import EvidenceFormat
    from shikiru.operator.queries import (
        CommandHistoryEntry,
        EscalationView,
        OperatorQueries,
        TaskActionAvailability,
        TaskSummary,
    )


_ACTION_SUBMIT = "submit"
_ACTION_PRIORITIZE = "prioritize"
_ACTION_PAUSE = "pause"
_ACTION_PAUSE_NOW = "pause-now"
_ACTION_RESUME = "resume"
_ACTION_TAKE_OVER = "take-over"
_ACTION_HAND_BACK = "hand-back"
_ACTION_CANCEL = "cancel"
_ACTION_RETRY = "retry"
_ACTION_RUNG = "rung"
_ACTION_MESSAGE = "message"
_ACTION_PEEK = "peek"
_ACTION_RESOLVE = "resolve"
_RESOLUTION_ROUTE_PREFIX = f"{_ACTION_RESOLVE} "
_ACTION_STOP = "stop"
_ACTION_EXPORT = "export"
_FIELD_EXPORT_DESTINATION = "export-destination"
_FIELD_EXPORT_FORMAT = "export-format"
_FIELD_EXPORT_OVERWRITE = "export-overwrite"
_FIELD_EXPORT_SIGNALS = "export-signals"
_POPUP_CLOSE_LABEL = "Esc \u00d7"
_EXPORT_REFUSE = "refuse"
_EXPORT_OVERWRITE = "overwrite"
_EXPORT_SIGNALS_EXCLUDE = "exclude"
_EXPORT_SIGNALS_INCLUDE = "include"
_EXPORT_OVERWRITE_REQUIRED = "choose how to handle an existing destination"
_EXPORT_NOT_CONFIGURED = "task evidence export is not configured"
_TASK_ACTIONS = frozenset(
    {
        _ACTION_PRIORITIZE,
        _ACTION_PAUSE,
        _ACTION_PAUSE_NOW,
        _ACTION_RESUME,
        _ACTION_TAKE_OVER,
        _ACTION_HAND_BACK,
        _ACTION_CANCEL,
        _ACTION_RETRY,
        _ACTION_RUNG,
        _ACTION_MESSAGE,
    }
)
_TASK_TARGET_ACTIONS = _TASK_ACTIONS | {_ACTION_PEEK, _ACTION_EXPORT}
PALETTE_ACTIONS = (
    ("Submit task", _ACTION_SUBMIT),
    ("Prioritize task", _ACTION_PRIORITIZE),
    ("Pause task", _ACTION_PAUSE),
    ("Pause now", _ACTION_PAUSE_NOW),
    ("Resume task", _ACTION_RESUME),
    ("Take over role", _ACTION_TAKE_OVER),
    ("Hand back role", _ACTION_HAND_BACK),
    ("Cancel task", _ACTION_CANCEL),
    ("Retry task", _ACTION_RETRY),
    ("Change rung", _ACTION_RUNG),
    ("Message agent", _ACTION_MESSAGE),
    ("Peek session", _ACTION_PEEK),
    ("Resolve escalation", _ACTION_RESOLVE),
    ("Export task evidence", _ACTION_EXPORT),
    ("Stop engine", _ACTION_STOP),
)
SUBMIT_ACTION_LABELS = {
    _ACTION_SUBMIT: "Submit task",
    _ACTION_PRIORITIZE: "Set priority",
    _ACTION_PAUSE: "Pause task",
    _ACTION_PAUSE_NOW: "Pause now",
    _ACTION_RESUME: "Resume task",
    _ACTION_TAKE_OVER: "Take over role",
    _ACTION_HAND_BACK: "Hand back role",
    _ACTION_CANCEL: "Cancel task",
    _ACTION_RETRY: "Retry task",
    _ACTION_RUNG: "Change rung",
    _ACTION_MESSAGE: "Queue message",
    _ACTION_PEEK: "Open session",
    _ACTION_RESOLVE: "Submit resolution",
    _ACTION_EXPORT: "Export evidence",
    _ACTION_STOP: "Stop engine",
}
_TASK_ROLES = TASK_ACTION_ROLES
_ROLE_OPTIONS = tuple((role.value.title(), role.value) for role in _TASK_ROLES)
_MODE_OPTIONS = (
    ("Auto detect", ""),
    ("Implement", Mode.IMPLEMENT.value),
    ("Plan", Mode.PLAN.value),
)
_COMPLEXITY_OPTIONS = (
    ("Triage decides", ""),
    ("Low", "low"),
    ("Normal", "normal"),
    ("High", "high"),
)
_EMPTY = ""
_FIELD_ACTION = "action"
_FIELD_ESCALATION = "escalation"
_FIELD_RESOLUTION = "resolution"
_FIELD_ACTION_SEARCH = "action-search"
_FIELD_TASK_SEARCH = "task-search"
_FIELD_TASK_CHOICE = "task-choice"
_FIELD_TASK_ID = "task-id"
_FIELD_ROLE = "role"
_FIELD_PRIORITY = "priority"
_FIELD_REQUEST = "request"
_FIELD_TITLE = "title"
_FIELD_MODE = "mode"
_FIELD_COMPLEXITY = "complexity"
_FIELD_SUBMISSION_PRIORITY = "submission-priority"
_FIELD_DEPENDENCIES = "dependencies"
_FIELD_LEADER_RUNG = "leader-rung"
_FIELD_IMPLEMENTER_RUNG = "implementer-rung"
_FIELD_RUNG = "rung"
_FIELD_MESSAGE = "message"
_FIELD_RESOLUTION_TEXT = "resolution-text"
_FIELD_RESOLUTION_RUNG = "resolution-rung"
_EXPORT_FORMAT_OPTIONS = (
    ("Markdown", EXPORT_FORMAT_MARKDOWN),
    ("JSON", EXPORT_FORMAT_JSON),
)
_EXPORT_OVERWRITE_OPTIONS = (
    ("Refuse an existing file", _EXPORT_REFUSE),
    ("Replace an existing file", _EXPORT_OVERWRITE),
)
_EXPORT_SIGNALS_OPTIONS = (
    ("Exclude normalized signal excerpts", _EXPORT_SIGNALS_EXCLUDE),
    ("Include normalized signal excerpts", _EXPORT_SIGNALS_INCLUDE),
)
_ESCALATION_SUMMARY_ID = "palette-escalation-summary"
_ESCALATION_STATUS_ID = "palette-escalation-status"
_EVIDENCE_ERROR_ID = "palette-evidence-error"
_EVIDENCE_CONTAINER_ID = "palette-evidence"
_ORIGINAL_PACKET_ID = "palette-original-packet"
_EVIDENCE_SECTIONS = (
    ("Timeline", "timeline"),
    ("What was tried", "what-was-tried"),
    ("Reports", "reports"),
    ("Facts", "facts"),
    ("Recent turn signals", "recent-signals"),
    ("Sessions", "sessions"),
)
_ACTION_STATE_ID = "palette-action-state"
_GUIDANCE_ID = "palette-guidance"
_SUBMIT_ID = "palette-submit"
_TASK_COUNT_ID = "palette-task-count"
_TASK_MORE_ID = "palette-task-more"
_ADVANCED_TOGGLE_ID = "palette-advanced-toggle"
_ADVANCED_FIELDS_ID = "palette-advanced-fields"
_SELECT_REQUIRED = "Select an item"
_TASK_SEARCH_PAGE_LIMIT = 50
_PALETTE_FORM_ID = "palette-form"
_PALETTE_MENU_ID = "palette-menu"
_PALETTE_MENU_ACTION_PREFIX = "palette-menu-action-"
_PALETTE_MENU_TASK_ACTIONS = "palette-menu-task-actions"
_PALETTE_MENU_MORE_ACTIONS = "palette-menu-more-actions"
_PALETTE_MENU_PROJECT_ACTIONS = "palette-menu-project-actions"
_PALETTE_MENU_TASK_VIEWS = "palette-menu-task-views"
_PALETTE_MENU_CONTEXT = "palette-menu-context"
_PALETTE_CLOSE = "palette-close"
_PALETTE_BACK_TO_MENU = "palette-back-to-menu"
_PALETTE_ROUTE_EVIDENCE = "palette-menu-view-evidence"
_PALETTE_ROUTE_USAGE = "palette-menu-view-usage"
_PALETTE_ROUTE_VIEWS = "palette-menu-view-secondary"
_PALETTE_ROUTE_ANSWER = "palette-menu-answer"
_PALETTE_ROUTE_COMMANDS = "palette-menu-commands"
_PALETTE_ROUTE_DIAGNOSTICS = "palette-menu-diagnostics"
_PALETTE_MENU_PRIMARY = "palette-menu-primary"
_PRIMARY_TASK_ACTIONS = frozenset({_ACTION_MESSAGE, _ACTION_TAKE_OVER, _ACTION_HAND_BACK})
_TASK_PALETTE_ROUTES = {
    _PALETTE_ROUTE_EVIDENCE: TaskPaletteRoute.EVIDENCE,
    _PALETTE_ROUTE_USAGE: TaskPaletteRoute.USAGE,
    _PALETTE_ROUTE_VIEWS: TaskPaletteRoute.VIEWS,
    _PALETTE_ROUTE_COMMANDS: TaskPaletteRoute.COMMANDS,
    _PALETTE_ROUTE_DIAGNOSTICS: TaskPaletteRoute.DIAGNOSTICS,
}
_PALETTE_ESCAPE_KEY = "escape"
_PALETTE_SHORT_PANE_MAX_ROWS = 18
_PALETTE_SHORT_PANE_MAX_COLUMNS = 40
_PALETTE_CONTEXT_TITLE_SHORT_LABEL_MAX_COLUMNS = 48
_PALETTE_TINY_PANE_MAX_ROWS = 8
_PALETTE_SHORT_OVERLAY_MAX_ROWS = 20
_PALETTE_SHORT_OVERLAY_MAX_COLUMNS = 48
_PALETTE_ACTION_BUTTON_MIN_COLUMNS = 16
_PALETTE_SHORT_OVERLAY_HEIGHT = "80%"
_PALETTE_COMPACT_TEXT_AREA_HEIGHT = 3
_PALETTE_COMPACT_RESULT_MAX_HEIGHT = 3
_PALETTE_RESULT_MAX_HEIGHT = 12
_PALETTE_FORM_STANDARD_FIXED_ROWS = 9
_PALETTE_FORM_SHORT_FIXED_ROWS = 8
_PALETTE_FORM_TINY_FIXED_ROWS = 4
_PALETTE_FRAME_VERTICAL_MARGIN_ROWS = 2
_PALETTE_MENU_FIXED_CONTENT_ROWS = 2
_PALETTE_TEXT_AREA_MINIMUM_WIDTH = 5
_PALETTE_INLINE_ACTIONS_ROWS = 1
_PALETTE_STACKED_ACTIONS_ROWS = 2
_PALETTE_TEXT_AREA_HEIGHTS = (
    (_FIELD_REQUEST, 6),
    (_FIELD_MESSAGE, 4),
    (_FIELD_RESOLUTION_TEXT, 5),
)
_TAKEOVER_SELECTION_FAILURE = "take-over succeeded, but the role pane could not be selected"
_PALETTE_STATE_WORKER = "palette-state-updates"
_FORM_IDENTITY_FIELDS = {
    _ACTION_SUBMIT: (
        _FIELD_REQUEST,
        _FIELD_TITLE,
        _FIELD_MODE,
        _FIELD_COMPLEXITY,
        _FIELD_SUBMISSION_PRIORITY,
        _FIELD_DEPENDENCIES,
        _FIELD_LEADER_RUNG,
        _FIELD_IMPLEMENTER_RUNG,
    ),
    _ACTION_PRIORITIZE: (_FIELD_PRIORITY,),
    _ACTION_RUNG: (_FIELD_RUNG,),
    _ACTION_MESSAGE: (_FIELD_MESSAGE,),
    _ACTION_RESOLVE: (
        _FIELD_ESCALATION,
        _FIELD_RESOLUTION,
        _FIELD_RESOLUTION_TEXT,
        _FIELD_RESOLUTION_RUNG,
    ),
    _ACTION_EXPORT: (
        _FIELD_EXPORT_DESTINATION,
        _FIELD_EXPORT_FORMAT,
        _FIELD_EXPORT_OVERWRITE,
        _FIELD_EXPORT_SIGNALS,
    ),
}
_FORM_ROLE_ACTIONS = frozenset(
    {_ACTION_TAKE_OVER, _ACTION_HAND_BACK, _ACTION_RUNG, _ACTION_MESSAGE, _ACTION_PEEK}
)
_ACTION_GUIDANCE = {
    _ACTION_SUBMIT: "Creates a new task using the request and optional submission fields.",
    _ACTION_PRIORITIZE: "Changes dispatch order while the task is queued.",
    _ACTION_PAUSE: "Holds the task before its next turn or integration effect.",
    _ACTION_PAUSE_NOW: "Stops the current turn and cancels its step, then holds the task.",
    _ACTION_RESUME: (
        "Clears task holds; dependencies, slots and other scheduling limits still apply."
    ),
    _ACTION_TAKE_OVER: (
        "Stops automation and holds this task while the selected role is owned; "
        "other tasks continue."
    ),
    _ACTION_HAND_BACK: (
        "Returns this role to automation; another taken-over role or pause can still hold the task."
    ),
    _ACTION_CANCEL: (
        "Cancels only this task and keeps its branch. Dirty or off-branch worktrees are preserved "
        "with their path returned; a clean worktree is removed during cleanup."
    ),
    _ACTION_RETRY: "Cancels and re-runs this task's current step.",
    _ACTION_RUNG: (
        "Uses the selected rung on this role's next turn; an in-flight turn is unchanged."
    ),
    _ACTION_MESSAGE: (
        "Queues guidance for this role's next turn; it does not interrupt a running turn."
    ),
    _ACTION_PEEK: "Shows recorded session activity for this task and role.",
    _ACTION_RESOLVE: "Applies one of the open escalation's allowed resolutions.",
    _ACTION_STOP: (
        "Stops this project's engine and running turns. Task state, sessions and worktrees "
        "remain for the next engine start."
    ),
    _ACTION_EXPORT: (
        "Writes retained task evidence to the selected local file path. Relative paths start at "
        "the project root, and the parent directory must already exist."
    ),
}
_TASK_ACTION_BY_ID = {action.value: action for action in OperatorTaskAction}
_NO_TASK_TARGET = "Choose a task target first"
_ACTION_DISABLED_PREFIX = "unavailable: "

PaneSelector = Callable[[str], Awaitable[None]]


def _context_menu_entry(label: str, meaning: str) -> Text:
    return Text.assemble(
        (f"\u203a {label}", STEEL_BLUE),
        (f"  {meaning}", SECONDARY_TEXT_COLOR),
    )


class PaletteScreen(ModalScreen[TaskPaletteRoute | None]):
    """Render operator actions, dispatch commands and show their acknowledgements."""

    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #palette-frame.tiny { margin: 0; padding: 0 1; }
    #palette-menu { max-height: 1fr; min-height: 0; }
    #palette-menu-primary { height: auto; }
    #palette-menu-context { height: 1; color: $secondary-text; }
    #palette-close {
        width: auto; min-width: 5; height: 1; margin: 0;
    }
    #palette-frame.tiny #palette-close { padding: 0; }
    #palette-menu-task-actions, #palette-menu-project-actions {
        height: auto; margin-top: 1; color: $secondary-text;
    }
    #palette-menu-task-views { height: auto; margin-top: 1; color: $secondary-text; }
    #palette-menu-task-actions Label, #palette-menu-project-actions Label {
        height: 1; color: $secondary-text; text-style: none;
    }
    #palette-menu-task-views Label { height: 1; color: $secondary-text; text-style: none; }
    #palette-menu Button {
        width: 100%; height: auto; min-height: 1; margin: 0; padding: 0 1 1 1;
        border: none; background: $surface; color: $primary-text;
        content-align: left middle; text-align: left; text-wrap: wrap; text-style: none;
    }
    #palette-menu-primary Button {
        width: 100%; height: auto; min-height: 2; margin: 0; padding: 0 1 1 1;
        border: none; background: $surface; text-align: left; text-style: none;
    }
    #palette-menu Button:focus, #palette-back-to-menu:focus {
        color: $primary-text; text-style: reverse;
    }
    #palette-form {
        max-height: 1fr; min-height: 0; margin-top: 0; padding-top: 0;
        border-top: none;
    }
    #field-palette-escalation-summary { height: auto; }
    #field-palette-escalation-status, #field-palette-evidence-error { height: auto; }
    .palette-field { height: auto; margin-bottom: 1; }
    .palette-field Label { height: 1; color: $secondary-text; text-style: none; }
    .palette-field Input, .palette-field Select { width: 100%; }
    .palette-field Input:focus,
    .palette-field TextArea:focus,
    #palette-submit:focus {
        border: round $focus;
        text-style: reverse;
    }
    #palette-result-scroll { height: auto; max-height: 12; margin-top: 1; }
    #palette-escalation-summary, #palette-escalation-status, #palette-evidence-error,
    #palette-original-packet, #palette-result { height: auto; }
    #palette-evidence { height: auto; }
    .palette-evidence { height: auto; }
    #request { height: 6; }
    #message { height: 4; }
    #resolution-text { height: 5; }
    #palette-submit, #palette-back-to-menu {
        height: 1; min-height: 1; margin: 0 0 0 1; padding: 0 1; border: none;
        text-style: none;
    }
    #palette-actions {
        layout: horizontal; width: 100%; height: auto; margin-top: 1; align-horizontal: right;
    }
    #palette-actions.short { margin-top: 0; }
    #palette-actions Button {
        width: auto; max-width: 100%; height: auto; min-height: 1; padding: 0;
        border: none; border-top: none; border-bottom: none;
        text-wrap: wrap; text-style: none;
    }
    #palette-submit:focus, #palette-back-to-menu:focus {
        text-style: reverse;
    }
    """
    )

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        pane_selector: PaneSelector,
        *,
        on_change: OnChangeRegistration,
        initial_action: str = _EMPTY,
        initial_context: PaletteRequest | None = None,
        evidence_exporter: TaskEvidenceExporter | None = None,
        remember_contextual_draft: Callable[[PaletteDraft], None] | None = None,
        task_routes_available: bool = False,
        project_diagnostics_available: bool = False,
    ) -> None:
        """Use the operator services for actions and queries and inject pane selection."""
        super().__init__()
        prepare_popup(self)
        self._commands = commands
        self._queries = queries
        self._contextual_palette = initial_context is not None
        self._initial_context = initial_context or PaletteRequest()
        self._initial_action = self._initial_context.action or initial_action
        self._fixed_escalation_id = (
            self._initial_action.removeprefix(_RESOLUTION_ROUTE_PREFIX)
            if self._initial_action.startswith(_RESOLUTION_ROUTE_PREFIX)
            else None
        )
        self._answer_resolution_route = self._initial_context.direct_answer
        self._menu_available = self._contextual_palette
        self._menu_visible = self._menu_available and self._initial_action == _EMPTY
        self._pane_selector = pane_selector
        self._on_change = on_change
        self._evidence_exporter = evidence_exporter
        self._remember_contextual_draft = remember_contextual_draft
        self._task_routes_available = task_routes_available
        self._project_diagnostics_available = project_diagnostics_available
        self._more_actions_expanded = False
        self._role_is_explicit = self._initial_context.role is not None
        self._task_search_tasks: tuple[TaskSummary, ...] = ()
        self._task_search_count = 0
        self._task_search_offset = 0
        self._task_search_matching_count = 0
        self._task_search_project_count = 0
        self._task_search_has_more = False
        self._action_states: dict[str, TaskActionAvailability] = {}
        self._shown_action_options: tuple[tuple[str, str], ...] = ()
        self._escalations: dict[str, EscalationView] = {}
        self._shown_escalation_id: str | None = None
        self._escalation_state_message: str | None = None
        self._escalation_packet: EscalationPacket | None = None
        self._route_error: str | None = None
        self._route_action: str | None = None
        self._advanced_expanded = False
        self._action_in_flight = False
        self._pending_form_commands: dict[tuple[str, ...], int] = {}
        self._submitting_form_identity: tuple[str, ...] | None = None
        self._command_action = _EMPTY
        self._command_target = _EMPTY
        self._action_worker: Worker[None] | None = None
        self._change_worker: Worker[None] | None = None
        self._form_scroll_y = 0
        self._form_focus_id: str | None = None
        self._form_position_action: str | None = None
        self._restored_contextual_draft_actions: set[str] = set()

    def compose(self) -> ComposeResult:
        """Show one compact form with fields enabled for the selected action."""
        with PopupFrame(widget_id="palette-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id="palette-heading", classes=POPUP_HEADER_CLASS):
                yield Label("Actions", id="palette-title", classes=POPUP_TITLE_CLASS)
                yield Button(
                    _POPUP_CLOSE_LABEL,
                    id=_PALETTE_CLOSE,
                    classes=POPUP_CLOSE_CLASS,
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            yield from self._compose_menu()
            yield from self._compose_form()
            with Vertical(id="palette-actions"):
                yield Button("Back to actions", id=_PALETTE_BACK_TO_MENU)
                yield Button("Run action", id=_SUBMIT_ID, variant="primary")

    def _compose_menu(self) -> ComposeResult:
        with VerticalScroll(id=_PALETTE_MENU_ID, classes=POPUP_BODY_CLASS):
            yield Static("", id=_PALETTE_MENU_CONTEXT, markup=False)
            with Vertical(id=_PALETTE_MENU_PRIMARY):
                yield Button(
                    _context_menu_entry("Answer decision", "Answer the recorded decision"),
                    id=_PALETTE_ROUTE_ANSWER,
                )
                yield Button(
                    _context_menu_entry(
                        "Evidence & reports",
                        "Reports, verdicts and engine facts",
                    ),
                    id=_PALETTE_ROUTE_EVIDENCE,
                )
                yield Button(
                    _context_menu_entry("Message a role", "Delivered on the next role turn"),
                    id=f"{_PALETTE_MENU_ACTION_PREFIX}{_ACTION_MESSAGE}",
                )
                yield Button(
                    _context_menu_entry("Take over a role", "Stop the turn; hold this task"),
                    id=f"{_PALETTE_MENU_ACTION_PREFIX}{_ACTION_TAKE_OVER}",
                )
                yield Button(
                    _context_menu_entry(
                        "Hand back a role",
                        "Release this role; remaining holds apply",
                    ),
                    id=f"{_PALETTE_MENU_ACTION_PREFIX}{_ACTION_HAND_BACK}",
                )
                yield Button(
                    _context_menu_entry("Usage", "Recorded session values"),
                    id=_PALETTE_ROUTE_USAGE,
                )
                yield Button(
                    _context_menu_entry("Commands", "Durable outcomes"),
                    id=_PALETTE_ROUTE_COMMANDS,
                )
                yield Button(
                    _context_menu_entry("Project diagnostics", "Secondary observations"),
                    id=_PALETTE_ROUTE_DIAGNOSTICS,
                )
            yield Button(
                _context_menu_entry("More actions", "Task changes and project controls"),
                id=_PALETTE_MENU_MORE_ACTIONS,
            )
            with Vertical(id=_PALETTE_MENU_TASK_ACTIONS):
                yield Label("More task actions")
                for label, action in PALETTE_ACTIONS:
                    if action not in {
                        _ACTION_SUBMIT,
                        _ACTION_STOP,
                        *_PRIMARY_TASK_ACTIONS,
                    }:
                        yield Button(label, id=f"{_PALETTE_MENU_ACTION_PREFIX}{action}")
            with Vertical(id=_PALETTE_MENU_TASK_VIEWS):
                yield Label("Secondary views and extensions")
                yield Button("Views and extensions", id=_PALETTE_ROUTE_VIEWS)
            with Vertical(id=_PALETTE_MENU_PROJECT_ACTIONS):
                yield Label("Project actions")
                for label, action in PALETTE_ACTIONS:
                    if action in {_ACTION_SUBMIT, _ACTION_STOP}:
                        yield Button(label, id=f"{_PALETTE_MENU_ACTION_PREFIX}{action}")

    def _compose_form(self) -> ComposeResult:
        with VerticalScroll(id=_PALETTE_FORM_ID, classes=POPUP_BODY_CLASS):
            yield from self._compose_action_target_fields()
            yield from self._compose_task_submission_fields()
            yield from self._compose_task_role_fields()
            yield from self._compose_resolution_fields()
            yield from self._compose_evidence_fields()
            with VerticalScroll(id="palette-result-scroll", classes=POPUP_BODY_CLASS):
                yield Static("", id="palette-result", markup=False)

    def _compose_action_target_fields(self) -> ComposeResult:
        yield from self._input_field("Find action", _FIELD_ACTION_SEARCH, "message")
        yield from self._select_field("Action", _FIELD_ACTION, PALETTE_ACTIONS)
        yield Static("", id=_ACTION_STATE_ID, markup=False)
        yield from self._input_field("Find task by ID or title", _FIELD_TASK_SEARCH, "T-1 or title")
        yield from self._select_field("Task match", _FIELD_TASK_CHOICE, ())
        yield Static("", id=_TASK_COUNT_ID, markup=False)
        yield Button("More matches", id=_TASK_MORE_ID)
        yield from self._input_field("Task ID · editable", _FIELD_TASK_ID, "T-1")

    def _compose_task_submission_fields(self) -> ComposeResult:
        yield Button("Advanced submission options", id=_ADVANCED_TOGGLE_ID)
        yield from self._input_field(
            "Export destination · relative to project root",
            _FIELD_EXPORT_DESTINATION,
            "evidence/T-1.md",
        )
        yield from self._select_field("Export format", _FIELD_EXPORT_FORMAT, _EXPORT_FORMAT_OPTIONS)
        yield from self._select_field(
            "Existing destination", _FIELD_EXPORT_OVERWRITE, _EXPORT_OVERWRITE_OPTIONS
        )
        yield from self._select_field(
            "Normalized agent signal excerpts", _FIELD_EXPORT_SIGNALS, _EXPORT_SIGNALS_OPTIONS
        )
        yield from self._input_field("Priority", "priority", "0")
        yield from self._text_area_field("Request", "request", "Describe the work")
        with Vertical(id=_ADVANCED_FIELDS_ID):
            yield from self._input_field("Title (optional)", "title", "Task title")
            yield from self._select_field("Mode", "mode", _MODE_OPTIONS)
            yield from self._select_field("Complexity", "complexity", _COMPLEXITY_OPTIONS)
            yield from self._input_field("Submission priority", "submission-priority", "0")
            yield from self._input_field(
                "Dependencies (comma separated)", "dependencies", "T-1, T-2"
            )
            yield from self._input_field("Leader rung", "leader-rung", "agent:model:effort")
            yield from self._input_field(
                "Implementer rung", "implementer-rung", "agent:model:effort"
            )

    def _compose_task_role_fields(self) -> ComposeResult:
        yield from self._select_field("Role", _FIELD_ROLE, _ROLE_OPTIONS)
        yield from self._input_field("Rung", "rung", "agent:model:effort")
        yield from self._text_area_field("Message", "message", "Message for the next turn")

    def _compose_resolution_fields(self) -> ComposeResult:
        yield from self._select_field("Escalation", _FIELD_ESCALATION, ())
        with Vertical(id=f"field-{_ESCALATION_STATUS_ID}"):
            yield Static("", id=_ESCALATION_STATUS_ID, markup=False)
        with Vertical(id=f"field-{_ESCALATION_SUMMARY_ID}"):
            yield Static("", id=_ESCALATION_SUMMARY_ID, markup=False)
        yield from self._select_field("Resolution", _FIELD_RESOLUTION, ())
        yield from self._text_area_field(
            "Answer guidance", "resolution-text", "Guidance for the Leader"
        )
        yield from self._input_field(
            "Retry rung (optional)", "resolution-rung", "agent:model:effort"
        )
        yield Static("", id=_GUIDANCE_ID, markup=False)
        with Vertical(id=f"field-{_EVIDENCE_ERROR_ID}"):
            yield Static("", id=_EVIDENCE_ERROR_ID, markup=False)

    def _compose_evidence_fields(self) -> ComposeResult:
        yield Vertical(
            *(
                Collapsible(
                    Static("", id=f"palette-evidence-{section_id}", markup=False),
                    title=title,
                    collapsed=True,
                    id=f"palette-evidence-toggle-{section_id}",
                    classes="palette-evidence",
                )
                for title, section_id in _EVIDENCE_SECTIONS
            ),
            Collapsible(
                Static("", id=_ORIGINAL_PACKET_ID, markup=False),
                title="Original full packet",
                collapsed=True,
                id="palette-evidence-original",
                classes="palette-evidence",
            ),
            id=_EVIDENCE_CONTAINER_ID,
        )

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        parent = event.widget.parent
        while parent is not None:
            if parent.id == _PALETTE_FORM_ID:
                self.query_one("#palette-form", VerticalScroll).scroll_to_widget(
                    event.widget,
                    animate=False,
                    immediate=True,
                    force=True,
                )
                return
            parent = parent.parent

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_mounted or not widget_is_attached(self):
            return
        form = self.query_one("#palette-form", VerticalScroll)
        scroll_y = form.scroll_y
        focused = cast("App[None]", self.app).focused
        self._set_text_area_heights(event.size.width, event.size.height)
        self._show_menu(self._menu_visible)
        self.call_after_refresh(self.update_popup_geometry)
        self.call_after_refresh(self._restore_form_layout, scroll_y, focused)

    async def on_mount(self) -> None:
        """Load open escalation packets and activate an action routed from a widget."""
        self._set_text_area_heights(self.size.width, self.size.height)
        self._refresh_escalations()
        self._initialize_action_route()
        self._initialize_form_state()
        self._refresh_action_availability()
        self._show_menu(self._menu_visible)
        self.call_after_refresh(self.query_one("#palette-frame", PopupFrame).content_ready)
        if self._menu_visible:
            self.call_after_refresh(self._focus_first_menu_action)
        elif self._contextual_palette:
            self.call_after_refresh(self._restore_context_form_position)
        else:
            self.set_focus(self.query_one(f"#{_FIELD_ACTION_SEARCH}", Input))
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_after_database_change),
            group=_PALETTE_STATE_WORKER,
        )
        if self._route_error is not None:
            self.query_one("#palette-result", Static).update(self._route_error)
            self.call_after_refresh(self._reveal_result)
            self.query_one(f"#{_SUBMIT_ID}", Button).disabled = True

    def _initialize_action_route(self) -> None:
        escalation_select = cast("Select[str]", self.query_one("#escalation", Select))
        action_select = cast("Select[str]", self.query_one("#action", Select))
        if self._initial_action == _EMPTY:
            action_select.value = _ACTION_SUBMIT
        elif self._initial_action in {action for _, action in PALETTE_ACTIONS}:
            action_select.value = self._initial_action
        elif self._initial_action.startswith(_RESOLUTION_ROUTE_PREFIX):
            action_select.value = _ACTION_RESOLVE
            escalation_id = self._initial_action.removeprefix(_RESOLUTION_ROUTE_PREFIX)
            if self._is_open_escalation(escalation_id):
                escalation_select.value = escalation_id
            else:
                item = self._queries.get_escalation(escalation_id)
                if item is None:
                    self._route_error = self._missing_escalation_message(escalation_id)
                    escalation_select.set_options(
                        [
                            *self._escalation_select_options(),
                            (f"{escalation_id} · not open", escalation_id),
                        ]
                    )
                else:
                    self._escalations[escalation_id] = item
                    self._refresh_escalation_options(preserve=escalation_id)
                escalation_select.value = escalation_id
        else:
            self._route_error = f"unknown palette action route: {self._initial_action}"
            action_select.set_options(
                (*PALETTE_ACTIONS, (self._initial_action, self._initial_action))
            )
            action_select.value = self._initial_action

    def _initialize_form_state(self) -> None:
        action_select = cast("Select[str]", self.query_one("#action", Select))
        cast("Select[str]", self.query_one("#mode", Select)).value = _EMPTY
        cast("Select[str]", self.query_one("#complexity", Select)).value = _EMPTY
        task_id = self._initial_context.task_id
        default_role = (
            self._queries.task_action_role(task_id).value
            if task_id is not None
            else Role.LEADER.value
        )
        role = self._initial_context.role or default_role
        if role not in {item.value for item in _TASK_ROLES}:
            if self._initial_context.role is not None:
                self._route_error = f"unknown role context: {role}"
            role = Role.LEADER.value
        cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = role
        self.query_one("#priority", Input).value = "0"
        self.query_one("#submission-priority", Input).value = "0"
        self.query_one(f"#{_FIELD_TASK_ID}", Input).value = self._initial_context.task_id or _EMPTY
        self._initialize_export_defaults()
        self._refresh_task_choices()
        self._route_action = self._selected_value(_FIELD_ACTION)
        self._update_fields(action_select.value)
        if self._answer_resolution_route:
            self._answer_resolution_route = self._select_direct_answer()
            self._show_escalation(self._selected_value(_FIELD_ESCALATION), force=True)
            self._update_fields(action_select.value)
        self._restore_contextual_form_draft(self._selected_value(_FIELD_ACTION))
        self._form_position_action = self._selected_value(_FIELD_ACTION)

    async def on_key(self, event: events.Key) -> None:
        if event.key != _PALETTE_ESCAPE_KEY:
            return
        if any(select.expanded for select in self.query(Select[str])):
            return
        await self.action_close()
        event.stop()
        event.prevent_default()

    def _set_text_area_heights(self, width: int, height: int) -> None:
        compact = height <= _PALETTE_SHORT_PANE_MAX_ROWS
        compact_layout = compact or width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        self.query_one("#palette-result-scroll").styles.max_height = (
            _PALETTE_COMPACT_RESULT_MAX_HEIGHT if compact_layout else _PALETTE_RESULT_MAX_HEIGHT
        )
        for field, normal_height in _PALETTE_TEXT_AREA_HEIGHTS:
            self.query_one(f"#{field}", TextArea).styles.height = (
                _PALETTE_COMPACT_TEXT_AREA_HEIGHT if compact else normal_height
            )

    def _initialize_export_defaults(self) -> None:
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_EXPORT_FORMAT}", Select)
        ).value = EXPORT_FORMAT_MARKDOWN
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_EXPORT_SIGNALS}", Select)
        ).value = _EXPORT_SIGNALS_EXCLUDE

    def _restore_form_layout(self, scroll_y: float, focused: Widget | None) -> None:
        if not self.is_mounted or not widget_is_attached(self):
            return
        form = self.query_one("#palette-form", VerticalScroll)
        if focused is not None and widget_is_on_screen(focused, self):
            parent = focused.parent
            while parent is not None:
                if parent.id == _PALETTE_FORM_ID:
                    form.scroll_to_widget(focused, animate=False, immediate=True)
                    return
                parent = parent.parent
        form.scroll_to(y=scroll_y, animate=False, immediate=True)

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Refresh visible fields and escalation packet details when a selection changes."""
        if event.select.id == _FIELD_ACTION:
            if (
                self._route_error is not None
                and isinstance(event.value, str)
                and event.value != self._route_action
            ):
                self._route_error = None
                self._route_action = None
                self.query_one(f"#{_SUBMIT_ID}", Button).disabled = False
                self._show_result(_EMPTY)
            self._update_fields(event.value)
            self._refresh_action_availability()
        elif event.select.id == _FIELD_ESCALATION:
            self._handle_escalation_selection(event.value)
        elif event.select.id == _FIELD_RESOLUTION:
            self._update_fields(self._selected_value(_FIELD_ACTION))
            self._refresh_action_availability()
        elif event.select.id == _FIELD_TASK_CHOICE:
            if isinstance(event.value, str) and event.select.has_focus:
                self.query_one(f"#{_FIELD_TASK_ID}", Input).value = event.value
        elif event.select.id == _FIELD_ROLE:
            if not self._role_is_explicit and event.select.has_focus:
                self._role_is_explicit = True
            self._refresh_action_availability()

    def _handle_escalation_selection(self, value: object) -> None:
        selected_id = value if isinstance(value, str) else None
        if selected_id != self._selected_value(_FIELD_ESCALATION):
            return
        if (
            self._route_error is not None
            and selected_id is not None
            and self._is_open_escalation(selected_id)
        ):
            self._route_error = None
            self.query_one(f"#{_SUBMIT_ID}", Button).disabled = False
            self._show_result(_EMPTY)
        self._show_escalation(value)
        self._refresh_action_availability()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Filter palette actions and task targets or refresh current eligibility."""
        if event.input.id == _FIELD_ACTION_SEARCH:
            self._refresh_action_availability()
        elif event.input.id == _FIELD_TASK_SEARCH:
            self._task_search_tasks = ()
            self._task_search_count = 0
            self._task_search_offset = 0
            self._refresh_task_choices()
        elif event.input.id == _FIELD_TASK_ID:
            self._task_search_tasks = ()
            self._task_search_count = 0
            self._task_search_offset = 0
            self._refresh_task_choices()
            if self._selected_value(_FIELD_ACTION) in _FORM_ROLE_ACTIONS:
                self._refresh_implicit_role()
            self._refresh_action_availability()
        else:
            self._refresh_action_availability()

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id in {_FIELD_REQUEST, _FIELD_MESSAGE, _FIELD_RESOLUTION_TEXT}:
            self._refresh_action_availability()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Run the selected action in a worker so the palette stays responsive."""
        button_id = event.button.id or _EMPTY
        if button_id == _PALETTE_CLOSE:
            await self.action_close()
        elif button_id == _TASK_MORE_ID:
            self._refresh_task_choices()
        elif button_id == _ADVANCED_TOGGLE_ID:
            self._advanced_expanded = not self._advanced_expanded
            self.query_one(f"#{_ADVANCED_FIELDS_ID}").display = self._advanced_expanded
        elif button_id == _PALETTE_BACK_TO_MENU:
            self._return_to_menu()
        elif button_id == _PALETTE_MENU_MORE_ACTIONS:
            self._more_actions_expanded = not self._more_actions_expanded
            self._refresh_context_menu()
        elif button_id == _PALETTE_ROUTE_ANSWER:
            self._open_menu_action(_ACTION_RESOLVE, direct_answer=True)
        elif button_id.startswith(_PALETTE_MENU_ACTION_PREFIX):
            action = button_id.removeprefix(_PALETTE_MENU_ACTION_PREFIX)
            self._open_menu_action(action)
        elif (route := _TASK_PALETTE_ROUTES.get(button_id)) is not None:
            self._remember_form_draft()
            self.dismiss(route)
        elif button_id == _SUBMIT_ID and not self._action_in_flight:
            self._action_in_flight = True
            event.button.disabled = True
            self._action_worker = self.run_worker(self._run_action(), exclusive=True)

    def on_unmount(self) -> None:
        worker = self._action_worker
        if worker is not None and worker.is_running:
            worker.cancel()
        if self._change_worker is not None:
            self._change_worker.cancel()

    async def action_close(self) -> None:
        """Dismiss the shared screen or exit its standalone popup app."""
        if self._contextual_palette and not self._menu_visible:
            self._remember_form_draft()
        app = cast("App[None]", self.app)
        if isinstance(app, PaletteApp):
            app.exit()
        else:
            self.dismiss()

    def _open_menu_action(self, action: str, *, direct_answer: bool = False) -> None:
        if action not in {value for _, value in PALETTE_ACTIONS}:
            return
        same_form = self._form_position_action == action
        if not same_form:
            self._form_focus_id = None
            self._form_scroll_y = 0
        if action == _ACTION_RESOLVE:
            self._answer_resolution_route = direct_answer
            escalation_id = self._context_escalation_id()
            if escalation_id is None:
                return
            cast(
                "Select[str]", self.query_one(f"#{_FIELD_ESCALATION}", Select)
            ).value = escalation_id
            self._show_escalation(escalation_id, force=True)
            if direct_answer:
                self._answer_resolution_route = self._select_direct_answer()
                self._show_escalation(escalation_id, force=True)
        if action in _FORM_ROLE_ACTIONS:
            self._choose_available_role(action)
        action_search = self.query_one(f"#{_FIELD_ACTION_SEARCH}", Input)
        if action_search.value:
            action_search.value = _EMPTY
            self._refresh_action_options()
        action_select = cast("Select[str]", self.query_one(f"#{_FIELD_ACTION}", Select))
        action_select.value = action
        self._route_error = None
        self._route_action = None
        self._show_result(_EMPTY)
        self._update_fields(action)
        self._restore_contextual_form_draft(action)
        self._refresh_action_availability()
        self._form_position_action = action
        self._show_menu(False)
        self.call_after_refresh(self._restore_context_form_position)

    def _return_to_menu(self) -> None:
        form = self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll)
        self._form_position_action = self._selected_value(_FIELD_ACTION)
        self._form_scroll_y = form.scroll_y
        focused = cast("App[None]", self.app).focused
        self._form_focus_id = focused.id if focused is not None else None
        self._remember_form_draft()
        self._show_menu(True)
        self.call_after_refresh(self._focus_first_menu_action)

    def _show_menu(self, visible: bool) -> None:
        state_changed = visible != self._menu_visible
        frame = self.query_one("#palette-frame", PopupFrame)
        if state_changed:
            frame.reset_preferred_size()
        self._menu_visible = visible
        menu = self.query_one(f"#{_PALETTE_MENU_ID}", VerticalScroll)
        form = self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll)
        tiny_pane = self.size.height <= _PALETTE_TINY_PANE_MAX_ROWS
        short_pane = (
            self.size.height <= _PALETTE_SHORT_PANE_MAX_ROWS
            or self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        )
        narrow_pane = self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        compact_overlay = (
            not narrow_pane
            and self.size.height <= _PALETTE_SHORT_OVERLAY_MAX_ROWS
            and self.size.width <= _PALETTE_SHORT_OVERLAY_MAX_COLUMNS
        )
        frame.styles.max_height = max(
            1,
            self.size.height - (0 if tiny_pane else _PALETTE_FRAME_VERTICAL_MARGIN_ROWS),
        )
        actions = self.query_one("#palette-actions", Vertical)
        actions.styles.layout = "horizontal" if tiny_pane or not narrow_pane else "vertical"
        actions.set_class(short_pane, "short")
        frame.set_class(tiny_pane, "tiny")
        form.set_class(tiny_pane, "tiny")
        fixed_rows = (
            _PALETTE_FORM_TINY_FIXED_ROWS
            if tiny_pane
            else _PALETTE_FORM_SHORT_FIXED_ROWS
            if short_pane
            else _PALETTE_FORM_STANDARD_FIXED_ROWS
        )
        action_rows = (
            _PALETTE_STACKED_ACTIONS_ROWS
            if narrow_pane and not tiny_pane
            else _PALETTE_INLINE_ACTIONS_ROWS
        )
        form.styles.max_height = max(1, self.size.height - fixed_rows - action_rows)
        for button_id in (_PALETTE_BACK_TO_MENU, _SUBMIT_ID):
            button = self.query_one(f"#{button_id}", Button)
            button.styles.width = "auto" if tiny_pane else "100%" if narrow_pane else "auto"
            button.styles.min_width = 1 if tiny_pane else _PALETTE_ACTION_BUTTON_MIN_COLUMNS
            button.styles.margin = 0 if narrow_pane else (0, 0, 0, 1)
            button.styles.height = 1 if narrow_pane else "auto"
        if tiny_pane:
            action = self._selected_value(_FIELD_ACTION)
            compact_submit_label = (
                "Answer"
                if self._answer_resolution_route
                else "Queue"
                if action == _ACTION_MESSAGE
                else "Resolve"
                if action == _ACTION_RESOLVE
                else "Submit"
            )
            self.query_one(f"#{_SUBMIT_ID}", Button).label = compact_submit_label
        else:
            self._update_fields(self._selected_value(_FIELD_ACTION))
        frame.styles.height = (
            _PALETTE_SHORT_OVERLAY_HEIGHT
            if visible and compact_overlay
            else "auto"
            if visible or not short_pane
            else "100%"
        )
        menu.display = visible
        form.display = not visible
        self.query_one(f"#{_PALETTE_BACK_TO_MENU}", Button).display = (
            self._contextual_palette and not visible
        )
        self.query_one(f"#{_PALETTE_BACK_TO_MENU}", Button).label = (
            "Back" if self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS else "Back to actions"
        )
        self.query_one(f"#{_SUBMIT_ID}", Button).display = not visible
        title = self.query_one("#palette-title", Label)
        if visible:
            title.update("Actions")
        else:
            title.update(self._context_form_title())
        self.call_after_refresh(self._refresh_context_form_title)
        self.call_after_refresh(self._constrain_menu_to_frame)
        self._refresh_context_menu()
        if state_changed:
            self.call_after_refresh(frame.content_ready)

    def _constrain_menu_to_frame(self) -> None:
        if not self.is_mounted or not widget_is_attached(self) or not self._menu_visible:
            return
        frame = self.query_one("#palette-frame")
        menu = self.query_one(f"#{_PALETTE_MENU_ID}", VerticalScroll)
        menu.styles.max_height = max(
            1,
            frame.content_region.height - _PALETTE_MENU_FIXED_CONTENT_ROWS,
        )

    def _context_form_title(self) -> str:
        action = self._selected_value(_FIELD_ACTION)
        label = next((label for label, value in PALETTE_ACTIONS if value == action), "Action")
        task_id = self._initial_context.task_id
        if task_id is None or action in {_ACTION_SUBMIT, _ACTION_STOP}:
            return label
        target = task_id
        if action == _ACTION_RESOLVE:
            escalation_id = self._selected_value(_FIELD_ESCALATION)
            if escalation_id is not None:
                target = f"{escalation_id} · {task_id}"
            label = "Answer decision" if self._answer_resolution_route else "Resolve escalation"
            full_title = f"{label} · {target}"
            if self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS:
                return f"{task_id} {escalation_id}" if escalation_id is not None else task_id
            short_label = "Answer" if self._answer_resolution_route else "Resolve"
            short_title = f"{short_label} · {target}"
            if self.size.width <= _PALETTE_CONTEXT_TITLE_SHORT_LABEL_MAX_COLUMNS:
                return short_title
            return full_title
        if action in _FORM_ROLE_ACTIONS:
            role = self._selected_value(_FIELD_ROLE)
            if role is not None:
                target = f"{task_id} · {role.title()}"
        return f"{label} · {target}"

    def _refresh_context_form_title(self) -> None:
        if not self.is_mounted or not widget_is_attached(self) or self._menu_visible:
            return
        self.query_one("#palette-title", Label).update(self._context_form_title())

    def update_popup_geometry(self) -> None:
        """Refresh popup geometry after the responsive layout changes."""
        if not self.is_mounted or not widget_is_attached(self):
            return
        self.query_one("#palette-frame", PopupFrame).update_preferred_size()
        self.call_after_refresh(self._refresh_context_form_title)

    def _remember_form_draft(self) -> None:
        task_id = self._initial_context.task_id
        action = self._selected_value(_FIELD_ACTION)
        resolution = self._selected_value(_FIELD_RESOLUTION)
        if task_id is None or action not in {_ACTION_MESSAGE, _ACTION_RESOLVE}:
            return
        if action == _ACTION_RESOLVE and resolution != ResolutionKind.ANSWER.value:
            return
        guidance_field = _FIELD_MESSAGE if action == _ACTION_MESSAGE else _FIELD_RESOLUTION_TEXT
        guidance = self._text(guidance_field)
        role = self._selected_value(_FIELD_ROLE)
        if self._remember_contextual_draft is not None:
            self._remember_contextual_draft(
                PaletteDraft(
                    task_id=task_id,
                    action=action,
                    guidance=guidance,
                    role=role,
                    role_explicit=self._role_is_explicit,
                    escalation_id=self._selected_value(_FIELD_ESCALATION),
                    resolution=resolution,
                )
            )

    def _restore_contextual_form_draft(self, action: str | None) -> None:
        if action is None or action in self._restored_contextual_draft_actions:
            return
        drafts = tuple(
            draft
            for draft in self._initial_context.drafts
            if draft.task_id == self._initial_context.task_id and draft.action == action
        )
        if self._initial_context.role is None:
            draft = drafts[-1] if drafts else None
        else:
            draft = next(
                (item for item in reversed(drafts) if item.role == self._initial_context.role),
                None,
            )
        if draft is None:
            return
        fixed_escalation_route = (
            self._fixed_escalation_id is not None or self._answer_resolution_route
        )
        if (
            action == _ACTION_RESOLVE
            and fixed_escalation_route
            and draft.escalation_id != self._selected_value(_FIELD_ESCALATION)
        ):
            return
        if self._initial_context.role is None and draft.role is not None:
            cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = draft.role
            self._role_is_explicit = draft.role_explicit
        if action == _ACTION_MESSAGE:
            self.query_one(f"#{_FIELD_MESSAGE}", TextArea).text = draft.guidance
        elif action == _ACTION_RESOLVE:
            if draft.escalation_id is not None:
                self._refresh_escalation_options(preserve=draft.escalation_id)
            if draft.resolution is not None:
                cast(
                    "Select[str]", self.query_one(f"#{_FIELD_RESOLUTION}", Select)
                ).value = draft.resolution
            self.query_one(f"#{_FIELD_RESOLUTION_TEXT}", TextArea).text = draft.guidance
        self._restored_contextual_draft_actions.add(action)

    def _refresh_context_menu(self) -> None:
        if not self.is_mounted:
            return
        task_id = self._initial_context.task_id if self._contextual_palette else None
        context = self.query_one(f"#{_PALETTE_MENU_CONTEXT}", Static)
        role = self._selected_value(_FIELD_ROLE)
        identity = f"Task {task_id}"
        if task_id is not None and role is not None:
            identity = f"{identity} · {role.title()}"
        context.update(identity if task_id else "Project")
        task_group = self.query_one(f"#{_PALETTE_MENU_TASK_ACTIONS}", Vertical)
        task_exists = task_id is not None and self._queries.get_task_state(task_id) is not None
        task_actions = self._contextual_task_actions(task_id if task_exists else None)
        available_actions = {item_action for _, item_action in task_actions}
        for _, action in PALETTE_ACTIONS:
            if action in _PRIMARY_TASK_ACTIONS:
                self.query_one(f"#{_PALETTE_MENU_ACTION_PREFIX}{action}", Button).display = (
                    task_exists and action in available_actions
                )
                continue
            button = self.query_one(f"#{_PALETTE_MENU_ACTION_PREFIX}{action}", Button)
            button.display = action in {_ACTION_SUBMIT, _ACTION_STOP} or any(
                item_action == action for item_action in available_actions
            )
        secondary_actions = available_actions - _PRIMARY_TASK_ACTIONS
        project_group = self.query_one(f"#{_PALETTE_MENU_PROJECT_ACTIONS}", Vertical)
        more_actions = self.query_one(f"#{_PALETTE_MENU_MORE_ACTIONS}", Button)
        more_actions.display = task_exists
        more_actions.label = _context_menu_entry(
            "Fewer actions" if self._more_actions_expanded else "More actions",
            "Hide task changes and project controls"
            if self._more_actions_expanded
            else "Task changes and project controls",
        )
        task_group.display = bool(task_exists and secondary_actions and self._more_actions_expanded)
        project_group.display = self._more_actions_expanded if task_exists else True
        self.query_one(f"#{_PALETTE_MENU_TASK_VIEWS}", Vertical).display = (
            task_exists and self._task_routes_available and self._more_actions_expanded
        )
        for route_id in (
            _PALETTE_ROUTE_EVIDENCE,
            _PALETTE_ROUTE_USAGE,
            _PALETTE_ROUTE_COMMANDS,
        ):
            self.query_one(f"#{route_id}", Button).display = (
                task_exists and self._task_routes_available
            )
        self.query_one(f"#{_PALETTE_ROUTE_ANSWER}", Button).display = (
            task_exists
            and task_id is not None
            and self._answerable_escalation_id(task_id) is not None
        )
        self.query_one(f"#{_PALETTE_ROUTE_DIAGNOSTICS}", Button).display = (
            task_exists and self._project_diagnostics_available
        )
        primary_labels = {
            _ACTION_MESSAGE: ("Message a role", "Delivered on the next role turn"),
            _ACTION_TAKE_OVER: ("Take over a role", "Stop the turn; hold this task"),
            _ACTION_HAND_BACK: (
                "Hand back a role",
                "Release this role; remaining holds apply",
            ),
        }
        for action, (label, meaning) in primary_labels.items():
            self.query_one(
                f"#{_PALETTE_MENU_ACTION_PREFIX}{action}", Button
            ).label = _context_menu_entry(label, meaning)
        self.query_one(f"#{_PALETTE_ROUTE_VIEWS}", Button).display = (
            task_exists and self._task_routes_available
        )

    def _answerable_escalation_id(self, task_id: str) -> str | None:
        escalation_id = self._context_escalation_id()
        if escalation_id is None:
            return None
        item = self._escalations.get(escalation_id)
        if item is None or item.escalation.task_id != task_id:
            return None
        if not any(option.label is ResolutionKind.ANSWER for option in item.escalation.options):
            return None
        if self._queries.escalation_resolution_disabled_reason(escalation_id) is not None:
            return None
        return escalation_id

    def _contextual_task_actions(self, task_id: str | None) -> list[tuple[str, str]]:
        if task_id is None:
            return []
        task = self._queries.get_task_state(task_id)
        if task is None:
            return []
        availability = {
            state.action: state for state in self._queries.task_action_availability(task_id, None)
        }
        actions: list[tuple[str, str]] = []
        for label, action in PALETTE_ACTIONS:
            if action in _TASK_ACTION_BY_ID:
                state = availability[_TASK_ACTION_BY_ID[action]]
                if state.enabled or self._context_action_blocked_but_relevant(
                    action,
                    state.disabled_reason,
                    has_holds=bool(task.holds),
                ):
                    actions.append((label, action))
            elif self._contextual_special_action_available(action):
                actions.append((label, action))
        return actions

    @staticmethod
    def _context_action_blocked_but_relevant(
        action: str,
        disabled_reason: str | None,
        *,
        has_holds: bool,
    ) -> bool:
        return (
            action == _ACTION_RESUME
            and has_holds
            and disabled_reason == TASK_ACTION_OTHER_ROLE_TAKEN_OVER
        )

    def _contextual_special_action_available(self, action: str) -> bool:
        return (action == _ACTION_RESOLVE and self._context_escalation_id() is not None) or (
            action == _ACTION_EXPORT
        )

    def _context_escalation_id(self) -> str | None:
        task_id = self._input(_FIELD_TASK_ID).strip()
        selected_id = self._selected_value(_FIELD_ESCALATION)
        if (
            selected_id is not None
            and self._is_open_escalation(selected_id)
            and self._escalations[selected_id].escalation.task_id == task_id
        ):
            return selected_id
        return next(
            (
                item.escalation.id
                for item in self._escalations.values()
                if item.escalation.task_id == task_id
                and item.escalation.status is EscalationStatus.OPEN
            ),
            None,
        )

    def _choose_available_role(self, action: str) -> None:
        if self._role_is_explicit:
            return
        task_id = self._input(_FIELD_TASK_ID).strip()
        if not task_id:
            return
        selected_action = _TASK_ACTION_BY_ID[action]
        current_role = self._role()
        current_state = next(
            item
            for item in self._queries.task_action_availability(task_id, current_role)
            if item.action is selected_action
        )
        if current_state.enabled:
            return
        for role in _TASK_ROLES:
            if role is current_role:
                continue
            state = next(
                item
                for item in self._queries.task_action_availability(task_id, role)
                if item.action is selected_action
            )
            if state.enabled:
                cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = role.value
                return

    def _focus_first_menu_action(self) -> None:
        if not self.is_mounted or not widget_is_attached(self) or not self._menu_visible:
            return
        button = next(
            (
                item
                for item in self.query(f"#{_PALETTE_MENU_ID} Button")
                if item.display and not item.disabled
            ),
            None,
        )
        if button is not None:
            button.focus()

    def _restore_context_form_position(self) -> None:
        if not self.is_mounted or not widget_is_attached(self) or self._menu_visible:
            return
        form = self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll)
        control = self.query_one(f"#{self._form_focus_id}") if self._form_focus_id else None
        if control is not None and self._is_visible_in_form(control) and control.can_focus:
            control.focus()
            form.scroll_to(y=self._form_scroll_y, animate=False, immediate=True)
            return
        action = self._selected_value(_FIELD_ACTION)
        resolution = self._selected_value(_FIELD_RESOLUTION)
        candidates = {
            _ACTION_SUBMIT: _FIELD_REQUEST,
            _ACTION_PRIORITIZE: _FIELD_PRIORITY,
            _ACTION_TAKE_OVER: _FIELD_ROLE,
            _ACTION_HAND_BACK: _FIELD_ROLE,
            _ACTION_RUNG: _FIELD_ROLE,
            _ACTION_MESSAGE: _FIELD_MESSAGE,
            _ACTION_RESOLVE: (
                _FIELD_RESOLUTION_TEXT
                if resolution == ResolutionKind.ANSWER.value
                else _FIELD_RESOLUTION_RUNG
                if resolution == ResolutionKind.RETRY.value
                else _FIELD_RESOLUTION
            ),
            _ACTION_EXPORT: _FIELD_EXPORT_DESTINATION,
        }
        candidate = candidates.get(action) if action is not None else None
        if candidate is not None:
            field = self.query_one(f"#{candidate}")
            if self._is_visible_in_form(field) and field.can_focus:
                field.focus()
                form.scroll_to_widget(field, animate=False, immediate=True, force=True)
                return
        submit = self.query_one(f"#{_SUBMIT_ID}", Button)
        if submit.display:
            submit.focus()

    @staticmethod
    def _is_visible_in_form(widget: Widget) -> bool:
        for ancestor in widget.ancestors_with_self:
            if not isinstance(ancestor, Widget):
                return False
            if not ancestor.display:
                return False
            if ancestor.id == _PALETTE_FORM_ID:
                return True
        return False

    async def _run_action(self) -> None:
        try:
            if self._selected_value(_FIELD_ACTION) == _ACTION_EXPORT:
                await self._run_evidence_export()
            else:
                await self._run_submission()
        except (OSError, ShikiruError, ValueError) as error:
            if self._is_current_screen():
                self._show_result(str(error))
        finally:
            self._action_in_flight = False
            self._submitting_form_identity = None
            self._action_worker = None
            if self._is_current_screen():
                self._refresh_action_availability()

    async def _run_evidence_export(self) -> None:
        if self._evidence_exporter is None:
            raise ShikiruError(_EXPORT_NOT_CONFIGURED)
        task_id = self._input(_FIELD_TASK_ID).strip()
        destination = self._input(_FIELD_EXPORT_DESTINATION)
        export_format = cast("EvidenceFormat", self._value(_FIELD_EXPORT_FORMAT))
        overwrite_value = self._selected_value(_FIELD_EXPORT_OVERWRITE)
        if overwrite_value not in {_EXPORT_REFUSE, _EXPORT_OVERWRITE}:
            raise ValueError(_EXPORT_OVERWRITE_REQUIRED)
        include_signals = self._selected_value(_FIELD_EXPORT_SIGNALS) == _EXPORT_SIGNALS_INCLUDE
        form_identity = self._form_identity()
        self._set_export_controls_disabled(True)
        try:
            result = await self._evidence_exporter.export(
                task_id,
                destination,
                export_format=export_format,
                overwrite=overwrite_value == _EXPORT_OVERWRITE,
                include_signals=include_signals,
            )
        finally:
            if self._is_current_screen():
                self._set_export_controls_disabled(False)
        if self._is_current_screen() and self._form_identity() == form_identity:
            format_label = result.format.upper()
            signal_label = (
                "Normalized signal excerpts included."
                if result.signals_included
                else "Normalized signal excerpts excluded."
            )
            destination_label = json.dumps(str(result.destination), ensure_ascii=False)
            self._show_result(
                f"Exported {format_label} task evidence to {destination_label}.\n{signal_label}"
            )

    def _set_export_controls_disabled(self, disabled: bool) -> None:
        for field in (
            _FIELD_ACTION_SEARCH,
            _FIELD_ACTION,
            _FIELD_TASK_SEARCH,
            _FIELD_TASK_CHOICE,
            _FIELD_TASK_ID,
            _FIELD_EXPORT_FORMAT,
            _FIELD_EXPORT_OVERWRITE,
            _FIELD_EXPORT_SIGNALS,
        ):
            self.query_one(f"#{field}").disabled = disabled
        self.query_one(f"#{_FIELD_EXPORT_DESTINATION}", Input).disabled = disabled

    async def _run_submission(self) -> None:
        action = self._value(_FIELD_ACTION)
        task_id = self._input(_FIELD_TASK_ID).strip()
        role = self._role() if action in _FORM_ROLE_ACTIONS | {_ACTION_PEEK} else None
        self._command_action = action
        self._command_target = self._target_for_action(action, task_id, role)
        form_identity = self._form_identity()
        if action != _ACTION_PEEK and self._observe_pending_form(form_identity):
            return
        self._submitting_form_identity = form_identity
        if action != _ACTION_PEEK:
            self._show_result(f"Sending · {self._command_target}")
        ack = await self._dispatch()
        if ack is None:
            return
        if ack.status is AckStatus.QUEUED:
            self._pending_form_commands[form_identity] = ack.id
        if self._is_current_screen():
            self._show_ack(ack)
        if action == _ACTION_TAKE_OVER and ack.status is AckStatus.DONE and role is not None:
            await self._select_role_pane(ack, task_id, role)

    async def _dispatch(self) -> Ack | None:
        action = self._value(_FIELD_ACTION)
        task_id = self._input(_FIELD_TASK_ID).strip()
        if action == _ACTION_PEEK:
            self._show_peek(task_id)
            return None
        if action in _TASK_ACTIONS:
            self._validate_task_action(action, task_id)
        command = self._command_for_current_form()
        if command is not None:
            return await self._commands.send(command)
        message = f"unsupported palette action: {action}"
        raise ValueError(message)

    def _validate_task_action(self, action: str, task_id: str) -> None:
        if not task_id:
            raise ValueError(_NO_TASK_TARGET)
        selected_action = _TASK_ACTION_BY_ID[action]
        role = self._role() if action in _FORM_ROLE_ACTIONS else None
        state = next(
            item
            for item in self._queries.task_action_availability(task_id, role)
            if item.action is selected_action
        )
        if state.disabled_reason is not None:
            raise ValueError(state.disabled_reason)

    def _refresh_action_availability(self) -> None:
        self._refresh_pending_commands()
        form_identity = self._form_identity()
        if form_identity != self._submitting_form_identity:
            self._observe_pending_form(form_identity)
        task_id = self._input(_FIELD_TASK_ID).strip()
        self._action_states = (
            {
                state.action.value: state
                for state in self._queries.task_action_availability(task_id, None)
            }
            if task_id
            else {}
        )
        self._refresh_action_options()
        action = self._selected_value(_FIELD_ACTION)
        disabled_reason = self._disabled_reason(action, task_id)
        if action in _FORM_ROLE_ACTIONS and task_id:
            selected_action = _TASK_ACTION_BY_ID[action]
            role_state = next(
                item
                for item in self._queries.task_action_availability(task_id, self._role())
                if item.action is selected_action
            )
            disabled_reason = role_state.disabled_reason
        if action == _ACTION_RESOLVE:
            disabled_reason = self._resolve_disabled_reason() or disabled_reason
        state = self.query_one(f"#{_ACTION_STATE_ID}", Static)
        state.update(f"Unavailable: {disabled_reason}" if disabled_reason is not None else _EMPTY)
        guidance = (
            self._answer_resolution_consequence()
            if action == _ACTION_RESOLVE and self._answer_resolution_route
            else _ACTION_GUIDANCE.get(action or "", _EMPTY)
        )
        self.query_one(f"#{_GUIDANCE_ID}", Static).update(guidance)
        pending_same_form = form_identity in self._pending_form_commands
        self.query_one(f"#{_SUBMIT_ID}", Button).disabled = (
            self._route_error is not None
            or disabled_reason is not None
            or self._action_in_flight
            or pending_same_form
        )
        self._refresh_context_menu()

    def _select_direct_answer(self) -> bool:
        escalation_id = self._selected_value(_FIELD_ESCALATION)
        if escalation_id is None:
            return False
        item = self._escalations.get(escalation_id)
        if item is None or not any(
            option.label is ResolutionKind.ANSWER for option in item.escalation.options
        ):
            return False
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_RESOLUTION}", Select)
        ).value = ResolutionKind.ANSWER.value
        return True

    def _resolve_disabled_reason(self) -> str | None:
        escalation_id = self._selected_value(_FIELD_ESCALATION)
        reason = None
        if escalation_id is None:
            reason = _SELECT_REQUIRED
        else:
            item = self._escalations.get(escalation_id)
            if item is None:
                reason = self._missing_escalation_message(escalation_id)
            elif self._escalation_state_message is not None:
                reason = self._escalation_state_message
            else:
                resolution_value = self._selected_value(_FIELD_RESOLUTION)
                if resolution_value is None:
                    reason = _SELECT_REQUIRED
                else:
                    kind = ResolutionKind(resolution_value)
                    if not any(option.label is kind for option in item.escalation.options):
                        reason = f"{kind.value} is not an option for {escalation_id}"
                    elif (
                        kind is ResolutionKind.ANSWER
                        and not self._text(_FIELD_RESOLUTION_TEXT).strip()
                    ):
                        reason = "answer is required"
        return reason

    def _answer_resolution_consequence(self) -> str:
        packet = self._escalation_packet
        if packet is None:
            return "Consequence unavailable in the recorded packet."
        consequence = next(
            (
                item.consequences
                for item in packet.resolutions
                if item.kind is ResolutionKind.ANSWER
            ),
            None,
        )
        return (
            f"Consequence: {consequence}"
            if consequence is not None
            else "Consequence unavailable in the recorded packet."
        )

    async def _refresh_after_database_change(self) -> None:
        if self._is_current_screen():
            self.call_after_refresh(self._refresh_if_current_screen)

    def _refresh_if_current_screen(self) -> None:
        if self._is_current_screen():
            self._refresh_escalations()
            self._refresh_action_availability()

    def _refresh_escalations(self) -> None:
        selected_id = self._selected_value(_FIELD_ESCALATION)
        same_target = selected_id is not None and selected_id == self._shown_escalation_id
        form = self.query_one("#palette-form", VerticalScroll)
        scroll_y = form.scroll_y
        focused = cast("App[None]", self.app).focused
        escalations = {item.escalation.id: item for item in self._queries.list_escalations()}
        if self._contextual_palette and self._initial_context.task_id is not None:
            escalations = {
                escalation_id: item
                for escalation_id, item in escalations.items()
                if item.escalation.task_id == self._initial_context.task_id
            }
        if selected_id is not None and selected_id not in escalations:
            item = self._queries.get_escalation(selected_id)
            if item is not None:
                escalations[selected_id] = item
        self._escalations = escalations
        self._refresh_escalation_options(preserve=selected_id)
        if selected_id is not None:
            self._show_escalation(selected_id, force=True)
        if same_target:
            self.call_after_refresh(partial(self._restore_escalation_form_state, scroll_y, focused))

    def _restore_escalation_form_state(self, scroll_y: float, focused: Widget | None) -> None:
        if not self._is_current_screen():
            return
        if focused is not None and widget_is_on_screen(focused, self):
            focused.focus()
        self.query_one("#palette-form", VerticalScroll).scroll_to(
            y=scroll_y,
            animate=False,
            immediate=True,
        )

    def _escalation_select_options(self) -> list[tuple[str, str]]:
        return [
            (
                f"{item.escalation.id} · {item.escalation.task_id} · "
                f"{display_escalation_headline(item.escalation.task_id, item.escalation.headline)}"
                + (" · resolved" if item.escalation.status is not EscalationStatus.OPEN else ""),
                item.escalation.id,
            )
            for item in self._escalations.values()
        ]

    def _refresh_escalation_options(self, *, preserve: str | None) -> None:
        select = cast("Select[str]", self.query_one(f"#{_FIELD_ESCALATION}", Select))
        options = self._escalation_select_options()
        if preserve is not None and preserve not in {value for _, value in options}:
            options.append((f"{preserve} · not open", preserve))
        select.set_options(options)
        if preserve is not None:
            select.value = preserve

    def _is_open_escalation(self, escalation_id: str) -> bool:
        item = self._escalations.get(escalation_id)
        return item is not None and item.escalation.status is EscalationStatus.OPEN

    def _missing_escalation_message(self, escalation_id: str) -> str:
        return f"escalation {escalation_id} is not open"

    def _refresh_pending_commands(self) -> None:
        completed: set[int] = set()
        for command_id in set(self._pending_form_commands.values()):
            outcome = self._queries.command_status(command_id)
            if outcome is None:
                message = f"pending command {command_id} is missing from durable history"
                raise RuntimeError(message)
            if outcome.status is not CommandStatus.PENDING:
                completed.add(command_id)
                self._show_stored_command(outcome)
        for identity, command_id in tuple(self._pending_form_commands.items()):
            if command_id in completed:
                del self._pending_form_commands[identity]

    def _observe_pending_form(self, identity: tuple[str, ...]) -> bool:
        if identity in self._pending_form_commands:
            return True
        try:
            command = self._command_for_current_form()
        except ShikiruError, ValueError:
            return False
        if command is None:
            return False
        outcome = self._queries.pending_command(command)
        if outcome is None:
            return False
        self._pending_form_commands[identity] = outcome.id
        self._show_stored_command(outcome)
        return True

    def _command_for_current_form(self) -> Command | None:
        task_id = self._input(_FIELD_TASK_ID).strip()
        action = self._selected_value(_FIELD_ACTION)
        builders: dict[str, Callable[[], Command]] = {
            _ACTION_SUBMIT: self._submit_command,
            _ACTION_PRIORITIZE: lambda: self._commands.prepare_prioritize_task(
                task_id, self._integer(_FIELD_PRIORITY)
            ),
            _ACTION_PAUSE: lambda: self._commands.prepare_pause_task(task_id),
            _ACTION_PAUSE_NOW: lambda: self._commands.prepare_pause_task(task_id, now=True),
            _ACTION_RESUME: lambda: self._commands.prepare_resume_task(task_id),
            _ACTION_TAKE_OVER: lambda: self._commands.prepare_take_over(task_id, self._role()),
            _ACTION_HAND_BACK: lambda: self._commands.prepare_hand_back(task_id, self._role()),
            _ACTION_CANCEL: lambda: self._commands.prepare_cancel_task(task_id),
            _ACTION_RETRY: lambda: self._commands.prepare_retry_task(task_id),
            _ACTION_RUNG: lambda: self._commands.prepare_set_rung(
                task_id, self._role(), parse_rung(self._input(_FIELD_RUNG))
            ),
            _ACTION_MESSAGE: lambda: self._commands.prepare_send_message(
                task_id, self._role(), self._text(_FIELD_MESSAGE)
            ),
            _ACTION_RESOLVE: self._resolve_command,
            _ACTION_STOP: self._commands.prepare_stop_engine,
        }
        builder = builders.get(action or _EMPTY)
        return builder() if builder is not None else None

    def _submit_command(self) -> Command:
        request = self._text(_FIELD_REQUEST)
        return self._commands.prepare_submit_task(
            request,
            title=self._optional_input(_FIELD_TITLE),
            mode=self._mode(),
            complexity=self._complexity(),
            priority=self._integer(_FIELD_SUBMISSION_PRIORITY),
            depends_on=self._dependencies(),
            leader=self._optional_rung(_FIELD_LEADER_RUNG),
            implementer=self._optional_rung(_FIELD_IMPLEMENTER_RUNG),
        )

    def _resolve_command(self) -> Command:
        escalation_id = self._selected_value(_FIELD_ESCALATION)
        resolution_value = self._selected_value(_FIELD_RESOLUTION)
        if escalation_id is None or resolution_value is None:
            raise ValueError(_SELECT_REQUIRED)
        kind = ResolutionKind(resolution_value)
        item = self._escalations.get(escalation_id)
        if item is None:
            message = f"escalation {escalation_id} is not open"
            raise ValueError(message)
        if not any(option.label is kind for option in item.escalation.options):
            message = f"{kind.value} is not an option for {escalation_id}"
            raise ValueError(message)
        answer = self._text(_FIELD_RESOLUTION_TEXT) if kind is ResolutionKind.ANSWER else _EMPTY
        if kind is ResolutionKind.ANSWER and not answer.strip():
            message = "answer is required"
            raise ValueError(message)
        retry_rung = (
            self._optional_rung(_FIELD_RESOLUTION_RUNG) if kind is ResolutionKind.RETRY else None
        )
        return self._commands.prepare_palette_resolution(
            escalation_id,
            kind,
            answer=answer,
            retry_rung=retry_rung,
        )

    def _form_identity(self) -> tuple[str, ...]:
        action = self._selected_value(_FIELD_ACTION) or _EMPTY
        return (
            action,
            self._input(_FIELD_TASK_ID).strip(),
            (self._selected_value(_FIELD_ROLE) or _EMPTY)
            if action in _FORM_ROLE_ACTIONS
            else _EMPTY,
            *(self._identity_value(field) for field in _FORM_IDENTITY_FIELDS.get(action, ())),
        )

    def _identity_value(self, field: str) -> str:
        control = self.query_one(f"#{field}")
        if isinstance(control, Input):
            return control.value
        if isinstance(control, TextArea):
            return control.text
        if isinstance(control, Select):
            value = cast("Select[str]", control).value
            return value if isinstance(value, str) else _EMPTY
        message = f"unsupported palette form field: {field}"
        raise TypeError(message)

    def _target_for_action(self, action: str, task_id: str, role: Role | None) -> str:
        if action in {_ACTION_SUBMIT, _ACTION_STOP}:
            return "project"
        if action == _ACTION_RESOLVE:
            return self._selected_value(_FIELD_ESCALATION) or "escalation"
        if role is not None:
            return f"{task_id} · {role.value}"
        return task_id or "task"

    def _refresh_action_options(self) -> None:
        search = self._input(_FIELD_ACTION_SEARCH).strip().casefold()
        options: list[tuple[str, str]] = []
        for label, action in PALETTE_ACTIONS:
            reason = self._disabled_reason(action, self._input(_FIELD_TASK_ID).strip())
            option_label = (
                f"{label} · {_ACTION_DISABLED_PREFIX}{reason}" if reason is not None else label
            )
            searchable = f"{label} {action} {reason or _EMPTY}".casefold()
            if not search or search in searchable:
                options.append((option_label, action))
        rendered = tuple(options)
        if rendered == self._shown_action_options:
            return
        selected = self._selected_value(_FIELD_ACTION)
        self._shown_action_options = rendered
        action_select = cast("Select[str]", self.query_one(f"#{_FIELD_ACTION}", Select))
        action_select.set_options(rendered)
        if selected in {value for _, value in rendered}:
            action_select.value = selected
        else:
            action_select.value = Select.BLANK

    def _disabled_reason(self, action: str | None, task_id: str) -> str | None:
        if action == _ACTION_EXPORT:
            return (
                _NO_TASK_TARGET
                if not task_id
                else _EXPORT_NOT_CONFIGURED
                if self._evidence_exporter is None
                else None
            )
        if action not in _TASK_TARGET_ACTIONS:
            return None
        if not task_id:
            return _NO_TASK_TARGET
        state = self._action_states.get(action)
        if state is None:
            return _NO_TASK_TARGET
        return state.disabled_reason

    def _refresh_task_choices(self) -> None:
        search = self._input(_FIELD_TASK_SEARCH).strip()
        page = self._queries.search_task_targets(
            search,
            limit=_TASK_SEARCH_PAGE_LIMIT,
            offset=self._task_search_offset,
            selected_task_id=self._input(_FIELD_TASK_ID).strip() or None,
        )
        if page.tasks:
            tasks_by_id = {task.id: task for task in self._task_search_tasks}
            tasks_by_id.update((task.id, task) for task in page.tasks)
            self._task_search_tasks = tuple(tasks_by_id.values())
            self._task_search_offset += len(page.tasks)
            self._task_search_count = len(self._task_search_tasks)
        self._task_search_matching_count = page.matching_count
        self._task_search_project_count = page.project_count
        self._task_search_has_more = page.has_more
        tasks = list(self._task_search_tasks)
        if page.selected_task is not None and all(
            task.id != page.selected_task.id for task in tasks
        ):
            tasks.append(page.selected_task)
        options = tuple((f"{task.id} · {task.title}", task.id) for task in tasks)
        task_select = cast("Select[str]", self.query_one(f"#{_FIELD_TASK_CHOICE}", Select))
        selected = self._input(_FIELD_TASK_ID).strip() or None
        task_select.set_options(options)
        if selected in {value for _, value in options}:
            task_select.value = selected
        else:
            task_select.value = Select.BLANK
        self.query_one(f"#{_TASK_COUNT_ID}", Static).update(
            f"Showing {self._task_search_count} of {self._task_search_matching_count} matches · "
            f"{self._task_search_project_count} tasks in project"
        )
        self.query_one(f"#{_TASK_MORE_ID}", Button).display = (
            self._selected_value(_FIELD_ACTION) in _TASK_TARGET_ACTIONS
            and self._task_search_has_more
        )

    def _refresh_implicit_role(self) -> None:
        if self._role_is_explicit:
            return
        task_id = self._input(_FIELD_TASK_ID).strip()
        role = self._queries.task_action_role(task_id).value if task_id else Role.LEADER.value
        if role not in {item.value for item in _TASK_ROLES}:
            role = Role.LEADER.value
        role_select = cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select))
        if role_select.value != role:
            role_select.value = role

    async def _select_role_pane(self, ack: Ack, task_id: str, role: Role) -> None:
        detail = self._queries.get_task(task_id)
        task = detail.task if detail is not None else None
        pane_id = task.panes.get(role) if task is not None else None
        if pane_id is None:
            condition = "task is missing" if task is None else f"{role.value} pane is missing"
            if self._is_current_screen():
                self._show_ack(ack, condition)
            return
        try:
            await self._pane_selector(pane_id)
        except ShikiruError as error:
            if self._is_current_screen():
                self._show_ack(ack, f"{_TAKEOVER_SELECTION_FAILURE}: {error}")

    def _show_peek(self, task_id: str) -> None:
        selected_role = self._selected_value(_FIELD_ROLE)
        view = self._queries.peek_session(task_id, Role(selected_role) if selected_role else None)
        if view is None:
            self._show_result(f"task not found: {task_id}")
            return
        lines: list[str] = [view.task_id]
        for role, pane in view.panes.items():
            lines.extend(
                (
                    f"{role} · {pane.agent}",
                    f"last message: {pane.last_message or '-'}",
                    f"tokens: {pane.usage.input_tokens} in / {pane.usage.output_tokens} out",
                    *pane.view,
                )
            )
        self._show_result("\n".join(lines))

    def _show_escalation(self, value: object, *, force: bool = False) -> None:
        escalation_id = value if isinstance(value, str) else _EMPTY
        selected_id = escalation_id or None
        target_changed = selected_id != self._shown_escalation_id
        if not force and not target_changed:
            return
        self._shown_escalation_id = selected_id
        item = self._escalations.get(escalation_id)
        status = self.query_one(f"#{_ESCALATION_STATUS_ID}", Static)
        summary = self.query_one(f"#{_ESCALATION_SUMMARY_ID}", Static)
        evidence_error = self.query_one(f"#{_EVIDENCE_ERROR_ID}", Static)
        status_field = self.query_one(f"#field-{_ESCALATION_STATUS_ID}")
        resolution = cast("Select[str]", self.query_one("#resolution", Select))
        if item is None:
            self._escalation_packet = None
            self._escalation_state_message = None
            status.update(_EMPTY)
            status_field.display = True
            summary.update(self._route_error or self._missing_escalation_message(escalation_id))
            evidence_error.update(_EMPTY)
            self._show_packet_evidence(None, None)
            resolution.set_options(())
            return

        self._escalation_state_message = self._queries.escalation_resolution_disabled_reason(
            escalation_id
        )
        status.update(self._escalation_state_message or "Open escalation")
        status_field.display = (
            not self._answer_resolution_route or self._escalation_state_message is not None
        )
        self._escalation_packet = (
            parse_escalation_packet(item.packet) if item.packet is not None else None
        )
        parsed = self._escalation_packet
        options = tuple(
            (
                f"{'★ ' if option.recommended else ''}{option.label.value}",
                option.label.value,
            )
            for option in item.escalation.options
        )
        selected_resolution = self._selected_value(_FIELD_RESOLUTION)
        resolution.set_options(options)
        if target_changed or selected_resolution not in {value for _, value in options}:
            recommended = next(
                option.label.value for option in item.escalation.options if option.recommended
            )
            resolution.value = recommended
        else:
            resolution.value = selected_resolution
        question = (
            parsed.agent_request.question
            if parsed is not None and parsed.agent_request is not None
            else parsed.question
            if parsed is not None
            else None
        )
        summary.update(self._escalation_summary(item, parsed, question))
        errors = [item.packet_error] if item.packet_error is not None else []
        if parsed is not None:
            errors.extend(parsed.errors)
        evidence_error.update("Evidence error: " + "; ".join(errors) if errors else _EMPTY)
        self._show_packet_evidence(item.packet, parsed)

    def _escalation_summary(
        self,
        item: EscalationView,
        packet: EscalationPacket | None,
        question: str | None,
    ) -> str:
        identity = (
            f"{item.escalation.task_id} · {item.escalation.id} · "
            f"{display_escalation_headline(item.escalation.task_id, item.escalation.headline)}"
        )
        if self._answer_resolution_route:
            lines = [identity, f"Question: {question or 'unavailable'}"]
            if packet is not None and packet.agent_request is not None:
                suggestion = next(
                    (option for option in packet.agent_request.options if option.recommended),
                    None,
                )
                if suggestion is not None:
                    lines.append(f"Leader suggestion: {suggestion.label}")
                    lines.append(f"Suggestion consequence: {suggestion.consequences}")
            return "\n".join(lines)
        lines = [identity]
        lines.append(f"Question: {question}" if question else "Question: unavailable")
        lines.append("Agent alternatives")
        if packet is not None and packet.agent_request is not None:
            lines.extend(
                f"  {'★ ' if option.recommended else ''}{option.label}\n"
                f"    Consequences: {option.consequences}"
                for option in packet.agent_request.options
            )
        elif packet is None:
            lines.append("  Agent alternatives are unavailable with the packet.")
        elif packet.errors:
            lines.append("  Agent alternatives could not be read.")
        else:
            lines.append("  No structured agent alternatives are recorded.")
        consequences = (
            {option.kind: option.consequences for option in packet.resolutions}
            if packet is not None
            else {}
        )
        lines.append("Allowed engine resolutions")
        lines.extend(
            f"  {'★ ' if option.recommended else ''}{option.label.value}"
            f" · {consequences.get(option.label, 'Consequence unavailable')}"
            for option in item.escalation.options
        )
        return "\n".join(lines)

    def _show_packet_evidence(
        self,
        original_packet: str | None,
        packet: EscalationPacket | None,
    ) -> None:
        sections = dict(packet.evidence_sections) if packet is not None else {}
        for title, section_id in _EVIDENCE_SECTIONS:
            control_id = f"palette-evidence-toggle-{section_id}"
            control = self.query_one(f"#{control_id}", Collapsible)
            content = sections.get(title)
            control.display = content is not None
            self.query_one(f"#palette-evidence-{section_id}", Static).update(
                content if content is not None else _EMPTY
            )
        original = self.query_one("#palette-evidence-original", Collapsible)
        original.display = original_packet is not None
        self.query_one(f"#{_ORIGINAL_PACKET_ID}", Static).update(
            original_packet if original_packet is not None else _EMPTY
        )

    def _update_fields(self, value: object) -> None:
        action = value if isinstance(value, str) else _EMPTY
        fixed_task_target = self._contextual_palette and self._initial_context.task_id is not None
        show_task_target = action in _TASK_TARGET_ACTIONS and not fixed_task_target
        groups = {
            _FIELD_ACTION_SEARCH: not self._contextual_palette,
            _FIELD_ACTION: not self._contextual_palette,
            _FIELD_TASK_SEARCH: show_task_target,
            _FIELD_TASK_CHOICE: show_task_target,
            _FIELD_TASK_ID: show_task_target,
            _FIELD_EXPORT_DESTINATION: action == _ACTION_EXPORT,
            _FIELD_EXPORT_FORMAT: action == _ACTION_EXPORT,
            _FIELD_EXPORT_OVERWRITE: action == _ACTION_EXPORT,
            _FIELD_EXPORT_SIGNALS: action == _ACTION_EXPORT,
            "request": action == _ACTION_SUBMIT,
            "title": action == _ACTION_SUBMIT,
            "mode": action == _ACTION_SUBMIT,
            "complexity": action == _ACTION_SUBMIT,
            "priority": action == _ACTION_PRIORITIZE,
            "submission-priority": action == _ACTION_SUBMIT,
            "dependencies": action == _ACTION_SUBMIT,
            "leader-rung": action == _ACTION_SUBMIT,
            "implementer-rung": action == _ACTION_SUBMIT,
            _FIELD_ROLE: action
            in {_ACTION_TAKE_OVER, _ACTION_HAND_BACK, _ACTION_RUNG, _ACTION_MESSAGE, _ACTION_PEEK},
            "rung": action == _ACTION_RUNG,
            "message": action == _ACTION_MESSAGE,
            _FIELD_ESCALATION: action == _ACTION_RESOLVE and not self._answer_resolution_route,
            _ESCALATION_STATUS_ID: action == _ACTION_RESOLVE
            and (
                not self._answer_resolution_route
                or self._escalation_state_message is not None
                or self._selected_value(_FIELD_ESCALATION) not in self._escalations
            ),
            _ESCALATION_SUMMARY_ID: action == _ACTION_RESOLVE,
            _EVIDENCE_ERROR_ID: action == _ACTION_RESOLVE,
            _FIELD_RESOLUTION: action == _ACTION_RESOLVE and not self._answer_resolution_route,
            "resolution-text": action == _ACTION_RESOLVE
            and self._selected_value(_FIELD_RESOLUTION) == ResolutionKind.ANSWER,
            "resolution-rung": action == _ACTION_RESOLVE
            and self._selected_value(_FIELD_RESOLUTION) == ResolutionKind.RETRY,
        }
        for field, visible in groups.items():
            self.query_one(f"#field-{field}").display = visible
        submit_label = SUBMIT_ACTION_LABELS.get(action, "Submit action")
        if action == _ACTION_RESOLVE:
            selected_resolution = self._selected_value(_FIELD_RESOLUTION)
            submit_label = (
                "Submit Answer"
                if selected_resolution == ResolutionKind.ANSWER
                else "Submit retry"
                if selected_resolution == ResolutionKind.RETRY
                else SUBMIT_ACTION_LABELS[_ACTION_RESOLVE]
            )
        self.query_one(f"#{_SUBMIT_ID}", Button).label = submit_label
        self.query_one(f"#{_ACTION_STATE_ID}").display = action in _TASK_TARGET_ACTIONS
        self.query_one(f"#{_GUIDANCE_ID}").display = action in _ACTION_GUIDANCE
        self.query_one(f"#{_TASK_COUNT_ID}").display = show_task_target
        self.query_one(f"#{_TASK_MORE_ID}").display = (
            show_task_target and self._task_search_has_more
        )
        self.query_one(f"#{_ADVANCED_TOGGLE_ID}").display = action == _ACTION_SUBMIT
        self.query_one(f"#{_ADVANCED_FIELDS_ID}").display = (
            action == _ACTION_SUBMIT and self._advanced_expanded
        )
        self.query_one(f"#{_EVIDENCE_CONTAINER_ID}").display = action == _ACTION_RESOLVE
        self._show_escalation(self._selected_value(_FIELD_ESCALATION))

    def _show_ack(self, ack: Ack, additional_detail: str | None = None) -> None:
        outcome = self._queries.command_status(ack.id)
        if outcome is None:
            message = f"acknowledged command {ack.id} is missing from durable history"
            raise RuntimeError(message)
        target = outcome.target
        if ack.status is AckStatus.QUEUED:
            label = "Pending"
            if ack.message == QUEUED_MESSAGE:
                details = "queued while the engine was stopped"
            elif ack.message == ACK_TIMEOUT_MESSAGE:
                engine_state = "running" if self._queries.command_history().engine_up else "stopped"
                details = (
                    f"acknowledgement wait elapsed; engine is currently {engine_state}; "
                    "still pending"
                )
            else:
                details = "waiting for engine acknowledgement"
        elif ack.status is AckStatus.DONE:
            label = "Applied"
            details = self._applied_result(ack)
        else:
            label = "Rejected"
            details = ack.message or "engine rejected the command"
            returned = self._returned_result(ack)
            if returned is not None:
                details = f"{details}\nReturned result · {returned}"
        message = f"{label} · command {ack.id} · {target}\n{details}"
        if additional_detail is not None:
            message = f"{message}\n{additional_detail}"
        self._show_result(message)

    def _show_stored_command(self, entry: CommandHistoryEntry) -> None:
        if entry.status is CommandStatus.DONE:
            label = "Applied"
            details = (
                "guidance queued for the selected role's next turn"
                if entry.action == CommandKind.SAY.value.title()
                else "engine applied the command effect"
            )
            returned = self._returned_result_value(entry.result)
            if returned is not None:
                details = f"{details}\nReturned result · {returned}"
        elif entry.status is CommandStatus.REJECTED:
            label = "Rejected"
            details = self._returned_result_value(entry.result) or "engine rejected the command"
        else:
            label = "Pending"
            engine_state = "running" if self._queries.command_history().engine_up else "stopped"
            details = (
                f"command remains pending; engine currently {engine_state}; "
                "original acknowledgement wait context unknown"
            )
        self._show_result(f"{label} · command {entry.id} · {entry.target}\n{details}")

    def _applied_result(self, ack: Ack) -> str:
        if self._command_action == _ACTION_MESSAGE:
            details = "guidance queued for the selected role's next turn"
        elif self._command_action == _ACTION_RUNG:
            details = "selected rung applies to this role's next turn"
        else:
            details = "engine applied the command effect"
        returned = self._returned_result(ack)
        return f"{details}\nReturned result · {returned}" if returned is not None else details

    def _returned_result(self, ack: Ack) -> str | None:
        return self._returned_result_value(ack.result)

    @staticmethod
    def _returned_result_value(result: dict[str, object] | None) -> str | None:
        return (
            json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None
        )

    def _show_result(self, text: str) -> None:
        if self._is_current_screen():
            self.query_one("#palette-result", Static).update(text)
            self.call_after_refresh(self._reveal_result)

    def _reveal_result(self) -> None:
        if self._is_current_screen():
            form = self.query_one("#palette-form", VerticalScroll)
            result = self.query_one("#palette-result-scroll", VerticalScroll)
            form.scroll_to_widget(result, animate=False, immediate=True)

    def _is_current_screen(self) -> bool:
        app = cast("App[None]", self.app)
        return app.is_running and self.is_mounted and app.screen is self

    def _input(self, name: str) -> str:
        return self.query_one(f"#{name}", Input).value

    def _text(self, name: str) -> str:
        return self.query_one(f"#{name}", TextArea).text

    def _optional_input(self, name: str) -> str | None:
        return self._input(name).strip() or None

    def _integer(self, name: str) -> int:
        try:
            return int(self._input(name))
        except ValueError:
            message = f"{name.replace('-', ' ')} must be an integer"
            raise ValueError(message) from None

    def _dependencies(self) -> tuple[str, ...]:
        return tuple(
            item.strip() for item in self._input("dependencies").split(",") if item.strip()
        )

    def _optional_rung(self, name: str) -> Rung | None:
        value = self._input(name).strip()
        return parse_rung(value) if value else None

    def _mode(self) -> Mode | None:
        value = self._selected_value("mode")
        return Mode(value) if value else None

    def _complexity(self) -> ComplexityName | None:
        value = self._selected_value("complexity")
        if not value:
            return None
        if value in get_args(ComplexityName):
            return cast("ComplexityName", value)
        message = f"unsupported complexity: {value}"
        raise ValueError(message)

    def _role(self) -> Role:
        return Role(self._value(_FIELD_ROLE))

    def _value(self, name: str) -> str:
        value = self._selected_value(name)
        if value is None:
            message = f"{name.replace('-', ' ')} is required"
            raise ValueError(message)
        return value

    def _selected_value(self, name: str) -> str | None:
        value = cast("Select[str]", self.query_one(f"#{name}", Select)).value
        return value if isinstance(value, str) else None

    @staticmethod
    def _select_field(
        label: str,
        field_id: str,
        options: tuple[tuple[str, str], ...],
    ) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes="palette-field"):
            yield Label(label)
            yield Select(options, prompt=_SELECT_REQUIRED, allow_blank=True, id=field_id)

    @staticmethod
    def _input_field(label: str, field_id: str, placeholder: str) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes="palette-field"):
            yield Label(label)
            yield Input(placeholder=placeholder, id=field_id)

    @staticmethod
    def _text_area_field(label: str, field_id: str, placeholder: str) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes="palette-field"):
            yield Label(label)
            text_area = TextArea(placeholder=placeholder, id=field_id)
            text_area.styles.min_width = _PALETTE_TEXT_AREA_MINIMUM_WIDTH
            yield text_area


class PaletteApp(App[None]):
    """Run the shared operator screen as the tmux popup entrypoint."""

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        pane_selector: PaneSelector,
        *,
        on_change: OnChangeRegistration,
        initial_context: PaletteRequest | None = None,
        evidence_exporter: TaskEvidenceExporter | None = None,
    ) -> None:
        """Retain the services used by the palette screen."""
        super().__init__()
        install_theme(self)
        self._commands = commands
        self._queries = queries
        self._pane_selector = pane_selector
        self._on_change = on_change
        self._initial_context = initial_context
        self._evidence_exporter = evidence_exporter

    def compose(self) -> ComposeResult:
        """Keep a neutral base screen beneath the modal palette."""
        yield Static()

    async def on_mount(self) -> None:
        """Open the same screen used by Ctrl+P in the dashboard."""
        self.push_screen(
            PaletteScreen(
                self._commands,
                self._queries,
                self._pane_selector,
                on_change=self._on_change,
                initial_context=self._initial_context,
                evidence_exporter=self._evidence_exporter,
            )
        )
