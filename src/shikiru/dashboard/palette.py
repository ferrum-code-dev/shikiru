"""The shared Textual operator palette for the popup and dashboard (06 §5.5)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar, cast, get_args

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Rule, Select, Static, TextArea

from shikiru.agents.contracts import parse_rung
from shikiru.config.settings import ComplexityName
from shikiru.dashboard.contract import (
    PaletteAction,
    PaletteDraft,
    PaletteRequest,
)
from shikiru.domain.commands import (
    COMMAND_REJECTED_FALLBACK,
    CommandKind,
    CommandStatus,
    command_rejection_cause,
)
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
from shikiru.operator.lifecycle import StartResult
from shikiru.operator.queries import (
    TASK_ACTION_ROLES,
    OperatorTaskAction,
)
from shikiru.ui.fold import FoldLine, fold_arrow
from shikiru.ui.keys import key_hint
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_COMPACT_CONTROL_CSS,
    POPUP_CSS,
    POPUP_EDIT_CLASS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    popup_close_label,
    prepare_popup,
)
from shikiru.ui.screen import widget_is_attached
from shikiru.ui.tokens import (
    AMBER,
    DUSTY_ROSE,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.timer import Timer
    from textual.worker import Worker

    from shikiru.agents.contracts import Rung
    from shikiru.dashboard.contract import OnChangeRegistration
    from shikiru.domain.commands import Command
    from shikiru.operator.commands import Ack, OperatorCommands
    from shikiru.operator.evidence_export import EvidenceFormat
    from shikiru.operator.queries import (
        CommandHistoryEntry,
        OperatorQueries,
        TaskActionAvailability,
    )


_ACTION_SUBMIT = PaletteAction.SUBMIT.value
_ACTION_PRIORITIZE = PaletteAction.PRIORITIZE.value
_ACTION_PAUSE = PaletteAction.PAUSE.value
_ACTION_PAUSE_NOW = PaletteAction.PAUSE_NOW.value
_ACTION_RESUME = PaletteAction.RESUME.value
_ACTION_TAKE_OVER = PaletteAction.TAKE_OVER.value
_ACTION_HAND_BACK = PaletteAction.HAND_BACK.value
_ACTION_CANCEL = PaletteAction.CANCEL.value
_ACTION_RETRY = PaletteAction.RETRY.value
_ACTION_RUNG = PaletteAction.RUNG.value
_ACTION_MESSAGE = PaletteAction.MESSAGE.value
_ACTION_PEEK = PaletteAction.PEEK.value
_ACTION_STOP = PaletteAction.STOP.value
_ACTION_START = PaletteAction.START.value
_ACTION_EXPORT = PaletteAction.EXPORT.value
_FIELD_EXPORT_DESTINATION = "export-destination"
_FIELD_EXPORT_FORMAT = "export-format"
_FIELD_EXPORT_OVERWRITE = "export-overwrite"
_FIELD_EXPORT_SIGNALS = "export-signals"
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
SUBMIT_ACTION_LABELS = {
    _ACTION_SUBMIT: "Submit task",
    _ACTION_PRIORITIZE: "Priority",
    _ACTION_PAUSE: "Pause",
    _ACTION_PAUSE_NOW: "Pause now",
    _ACTION_RESUME: "Resume",
    _ACTION_TAKE_OVER: "Take over",
    _ACTION_HAND_BACK: "Hand back",
    _ACTION_CANCEL: "Cancel",
    _ACTION_RETRY: "Retry",
    _ACTION_RUNG: "Rung",
    _ACTION_MESSAGE: "Message",
    _ACTION_PEEK: "Open session",
    _ACTION_EXPORT: "Export evidence",
    _ACTION_STOP: "Stop engine",
    _ACTION_START: "Start engine",
}
_ENTER_HINT_VERBS = {
    _ACTION_EXPORT: "export evidence",
    _ACTION_PAUSE: "pause",
    _ACTION_RESUME: "resume",
    _ACTION_CANCEL: "cancel",
    _ACTION_TAKE_OVER: "take over",
    _ACTION_HAND_BACK: "hand back",
    _ACTION_RETRY: "retry",
    _ACTION_PAUSE_NOW: "pause now",
    _ACTION_PRIORITIZE: "set priority",
    _ACTION_RUNG: "set rung",
    _ACTION_PEEK: "open session",
    _ACTION_STOP: "stop engine",
    _ACTION_START: "start engine",
}
_ENTER_HINT_ACTIONS = frozenset(_ENTER_HINT_VERBS)
_HINT_LABELS = {
    _ACTION_SUBMIT: ("Ctrl+S submit task \N{MIDDLE DOT} Enter new line", "Ctrl+S submit"),
    _ACTION_MESSAGE: ("Ctrl+S queue message \N{MIDDLE DOT} Enter new line", "Ctrl+S queue"),
    **{action: (f"Enter {verb}", f"Enter {verb}") for action, verb in _ENTER_HINT_VERBS.items()},
}
_HINT_CLASS = "hint"
_CONFIRM_HINTS = {
    _ACTION_CANCEL: ("Enter again to cancel", "Enter again"),
    _ACTION_RETRY: ("Enter again to retry", "Enter again"),
    _ACTION_STOP: ("Enter again to stop engine", "Enter again"),
}
_HINT_KEY_ACTIONS = {
    "confirm": _ENTER_HINT_ACTIONS,
    "queue": frozenset({_ACTION_MESSAGE, _ACTION_SUBMIT}),
}
_CONFIRM_TWICE_ACTIONS = frozenset(_CONFIRM_HINTS)
_CLEAR_AFTER_QUEUE_ACTIONS = frozenset({_ACTION_MESSAGE})
_CONFIRMED_ACTIONS = _ENTER_HINT_ACTIONS | _CLEAR_AFTER_QUEUE_ACTIONS | {_ACTION_SUBMIT}
_CONFIRMATION_GLYPH = "\N{CHECK MARK}"
_ENTER_KEY = "enter"
_TASK_ROLES = TASK_ACTION_ROLES
_ROLE_OPTIONS = tuple((role.value.title(), role.value) for role in _TASK_ROLES)
_EMPTY = ""
_MODE_OPTIONS = (
    ("Auto detect", _EMPTY),
    ("Implement", Mode.IMPLEMENT.value),
    ("Plan", Mode.PLAN.value),
)
_COMPLEXITY_OPTIONS = (
    ("Triage decides", _EMPTY),
    ("Low", "low"),
    ("Normal", "normal"),
    ("High", "high"),
)
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
_ROLE_FIRST_ACTIONS = frozenset({_ACTION_MESSAGE, _ACTION_TAKE_OVER, _ACTION_HAND_BACK})
_INITIAL_FIELD_BY_ACTION = {
    _ACTION_SUBMIT: _FIELD_REQUEST,
    _ACTION_PRIORITIZE: _FIELD_PRIORITY,
    _ACTION_RUNG: _FIELD_RUNG,
    _ACTION_MESSAGE: _FIELD_ROLE,
    _ACTION_TAKE_OVER: _FIELD_ROLE,
    _ACTION_HAND_BACK: _FIELD_ROLE,
    _ACTION_PEEK: _FIELD_ROLE,
    _ACTION_EXPORT: _FIELD_EXPORT_DESTINATION,
}
_EXPORT_FORMAT_OPTIONS = (
    ("Markdown", EXPORT_FORMAT_MARKDOWN),
    ("JSON", EXPORT_FORMAT_JSON),
)
_EXPORT_OVERWRITE_OPTIONS = (
    ("Refuse", _EXPORT_REFUSE),
    ("Replace", _EXPORT_OVERWRITE),
)
_EXPORT_SIGNALS_OPTIONS = (
    ("Exclude", _EXPORT_SIGNALS_EXCLUDE),
    ("Include", _EXPORT_SIGNALS_INCLUDE),
)
_ACTION_STATE_ID = "palette-action-state"
_GUIDANCE_ID = "palette-guidance"
_SUBMIT_ID = "palette-submit"
_OPTIONS_FOLD_ID = "palette-options-fold"
_OPTIONS_ID = "palette-options"
_OPTIONS_TITLE = "Options"
_PALETTE_FORM_ID = "palette-form"
_PALETTE_CLOSE = "palette-close"
_PALETTE_ESCAPE_KEY = "escape"
_PALETTE_SHORT_PANE_MAX_ROWS = 18
_PALETTE_SHORT_PANE_MAX_COLUMNS = 40
_PALETTE_WIDE_FIELD_LABEL_COLUMNS = 17
_PALETTE_CONTROL_MIN_COLUMNS = 8
_PALETTE_FRAME_BORDER_COLUMNS = 1
_PALETTE_SHORT_FRAME_PADDING_COLUMNS = 1
_PALETTE_INLINE_FRAME_HORIZONTAL_COLUMNS = 2 * (
    _PALETTE_FRAME_BORDER_COLUMNS + _PALETTE_SHORT_FRAME_PADDING_COLUMNS
)
_PALETTE_STACKED_FIELDS_MAX_COLUMNS = (
    _PALETTE_WIDE_FIELD_LABEL_COLUMNS
    + _PALETTE_CONTROL_MIN_COLUMNS
    + _PALETTE_INLINE_FRAME_HORIZONTAL_COLUMNS
    - 1
)
_PALETTE_TINY_PANE_MAX_ROWS = 8
_PALETTE_ACTION_BUTTON_MIN_COLUMNS = 16
_PALETTE_COMPACT_TEXT_AREA_MAX_HEIGHT = 3
_PALETTE_COMPACT_RESULT_MAX_HEIGHT = 3
_PALETTE_RESULT_MAX_HEIGHT = 12
_PALETTE_FORM_STANDARD_FIXED_ROWS = 9
_PALETTE_FORM_COMPACT_FIXED_ROWS = 4
_PALETTE_FRAME_VERTICAL_MARGIN_ROWS = 2
_PALETTE_TEXT_AREA_MINIMUM_WIDTH = 5
_PALETTE_INLINE_ACTIONS_ROWS = 1
_PALETTE_STACKED_ACTIONS_ROWS = 2
_EXPORT_DEFAULT_EXTENSION = "md"
_EXPORT_DESTINATION_PLACEHOLDER = f"T-1-evidence.{_EXPORT_DEFAULT_EXTENSION}"
_PALETTE_TEXT_AREA_FIELDS = (_FIELD_REQUEST, _FIELD_MESSAGE)
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
_REQUIRED_FIELDS = {
    _ACTION_SUBMIT: _FIELD_REQUEST,
    _ACTION_EXPORT: _FIELD_EXPORT_DESTINATION,
    _ACTION_MESSAGE: _FIELD_MESSAGE,
    _ACTION_PRIORITIZE: _FIELD_PRIORITY,
    _ACTION_RUNG: _FIELD_RUNG,
}
_ACTION_GUIDANCE = {
    _ACTION_PRIORITIZE: "Changes dispatch order while the task is queued.",
    _ACTION_PAUSE: "Holds the task before its next turn or integration effect.",
    _ACTION_PAUSE_NOW: "Stops the current turn and cancels its step, then holds the task.",
    _ACTION_RESUME: (
        "Clears task holds; dependencies, slots and other scheduling limits still apply."
    ),
    _ACTION_TAKE_OVER: (
        "Stops the running turn and holds this task while you work in the role's pane. "
        "Other tasks continue."
    ),
    _ACTION_HAND_BACK: (
        "Returns this role to automation; another taken-over role or pause can still hold the task."
    ),
    _ACTION_CANCEL: (
        "Cancels only this task and keeps its branch. Dirty or off-branch worktrees are preserved "
        "with their path returned; a clean worktree is removed during cleanup."
    ),
    _ACTION_RETRY: "Cancels the current step and runs it again, closing any open decision.",
    _ACTION_RUNG: "The rung is the agent:model:effort level a role uses from its next turn.",
    _ACTION_MESSAGE: (
        "Queues guidance for this role's next turn; it does not interrupt a running turn."
    ),
    _ACTION_PEEK: "Shows recorded session activity for this task and role.",
    _ACTION_STOP: (
        "Stops the engine for every task. Running turns are stopped and continue when the "
        "engine starts again."
    ),
    _ACTION_START: "Starts the engine and ensures its session without attaching.",
    _ACTION_EXPORT: (
        "Writes retained evidence to a local file; relative paths start at the project root, and "
        "parent folders must exist."
    ),
}
_TASK_ACTION_BY_ID = {action.value: action for action in OperatorTaskAction}
_FIELD_CLASS = "palette-field"
_ROW_CLASS = "palette-row"
_WIDE_CLASS = "palette-wide"
_NO_TASK_TARGET = "Choose a task target first"

PaneSelector = Callable[[str], Awaitable[None]]
StartEngine = Callable[[], Awaitable[StartResult]]
_START_NOT_CONFIGURED = "project start is not configured"
_ENGINE_ALREADY_RUNNING = "engine is already running"
_ENGINE_ALREADY_STOPPED = "engine is already stopped"


def _field_classes(*, inline: bool, wide: bool = False) -> str:
    classes = f"{_FIELD_CLASS} {_ROW_CLASS}" if inline else _FIELD_CLASS
    return f"{classes} {_WIDE_CLASS}" if wide else classes


def _default_export_destination(task_id: str) -> str:
    return f"{task_id}-evidence.{_EXPORT_DEFAULT_EXTENSION}"


class PaletteScreen(ModalScreen[str | None]):
    """Show one preset action popup; it dismisses with the action name once it queued it."""

    AUTO_FOCUS = ""
    BINDINGS: ClassVar[list[Binding]] = [
        Binding(_ENTER_KEY, "confirm", "Confirm", show=False),
        Binding("ctrl+s", "queue", "Queue", show=False),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #palette-frame.short { margin: 0 1; padding: 0 1; }
    #palette-frame.stack-fields { padding: 1 0; }
    #palette-frame.tiny { margin: 0; padding: 0; }
    #palette-frame.tiny #palette-close { padding: 0; }
    #palette-frame.stack-fields .palette-row { layout: vertical; }
    #palette-frame.stack-fields .palette-row Label { width: 100%; }
    #palette-frame.stack-fields .palette-row SelectCurrent { padding: 0; }
    #palette-close {
        width: auto; min-width: 5; height: 1; margin: 0;
    }
    #palette-form {
        height: auto; max-height: 1fr; min-height: 0; margin-top: 0; padding-top: 0;
        border-top: none; background: $surface;
    }
    #field-export-destination { margin-top: 1; }
    #field-role, #field-priority { margin-top: 1; }
    .palette-field { height: auto; margin-bottom: 1; background: $surface; }
    .palette-field Label { height: 1; color: $secondary-text; text-style: none; }
    .palette-field Input, .palette-field Select { width: 100%; }
    .palette-field Input:focus {
        border: round $focus;
        text-style: reverse;
    }
    """
        + POPUP_COMPACT_CONTROL_CSS
        + """
    #palette-options-fold { height: auto; margin: 0 0 1 0; color: $secondary-text; }
    #palette-options-fold:focus { background: $selected-surface; background-tint: transparent; }
    #palette-options { height: auto; margin-top: 1; }
    .palette-note { height: auto; color: $secondary-text; }
    #palette-result-scroll { display: none; height: auto; max-height: 12; margin-top: 1; }
    #palette-frame.short #palette-result-scroll { margin-top: 0; }
    #palette-result { height: auto; }
    #palette-submit {
        height: 1; min-height: 1; margin: 0 0 0 1; padding: 0 1; border: none;
        text-style: none;
    }
    #palette-actions {
        layout: horizontal; width: 100%; height: auto; margin-top: 1; align-horizontal: right;
    }
    #palette-actions.short { margin-top: 0; }
    #palette-actions.hint { align-horizontal: left; }
    #palette-actions.hint #palette-submit {
        width: 100%; min-width: 1; height: auto; margin: 0; padding: 0;
        border: none; border-top: none; border-bottom: none;
        background: $surface; color: $secondary-text; text-opacity: 1; text-wrap: wrap;
        content-align: left middle; text-align: left;
    }
    #palette-actions.hint #palette-submit:disabled { color: $secondary-text; text-opacity: 1; }
    #palette-actions Button {
        width: auto; max-width: 100%; height: auto; min-height: 1; padding: 0;
        border: none; border-top: none; border-bottom: none;
        text-wrap: wrap; text-style: none;
    }
    #palette-submit:focus { text-style: reverse; }
    """
        + f"""
    .palette-wide Label {{ width: {_PALETTE_WIDE_FIELD_LABEL_COLUMNS}; }}
    .palette-row Input, .palette-row Select {{ min-width: {_PALETTE_CONTROL_MIN_COLUMNS}; }}
    """
    )

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        pane_selector: PaneSelector,
        *,
        on_change: OnChangeRegistration,
        action_confirmation_seconds: float,
        initial_context: PaletteRequest,
        start_engine: StartEngine | None = None,
        evidence_exporter: TaskEvidenceExporter | None = None,
        remember_contextual_draft: Callable[[PaletteDraft], None] | None = None,
    ) -> None:
        """Use the operator services for actions and queries and inject pane selection."""
        super().__init__()
        prepare_popup(self)
        self._commands = commands
        self._queries = queries
        self._initial_context = initial_context
        self._action = initial_context.action or _EMPTY
        self._task_id = initial_context.task_id or _EMPTY
        self._pane_selector = pane_selector
        self._on_change = on_change
        self._start_engine = start_engine
        self._evidence_exporter = evidence_exporter
        self._remember_contextual_draft = remember_contextual_draft
        self._action_confirmation_seconds = action_confirmation_seconds
        self._confirmed_action: str | None = None
        self._confirmation_timer: Timer | None = None
        self._role_is_explicit = initial_context.role is not None
        self._action_states: dict[str, TaskActionAvailability] = {}
        self._route_error: str | None = None
        self._options_expanded = False
        self._action_in_flight = False
        self._confirm_armed = False
        self._closing_after_queue = False
        self._result_reveal_revision = 0
        self._pending_form_commands: dict[tuple[str, ...], int] = {}
        self._submitting_form_identity: tuple[str, ...] | None = None
        self._command_action = _EMPTY
        self._command_target = _EMPTY
        self._action_worker: Worker[None] | None = None
        self._change_worker: Worker[None] | None = None
        self._form_scroll_y = 0
        self._form_focus_id: str | None = None
        self._layout_form_max_height = 0
        self._restored_contextual_draft_actions: set[str] = set()

    def compose(self) -> ComposeResult:
        """Show one compact form with fields enabled for the selected action."""
        with PopupFrame(widget_id="palette-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(id="palette-heading", classes=POPUP_HEADER_CLASS):
                yield Label("Actions", id="palette-title", classes=POPUP_TITLE_CLASS)
                yield Button(
                    popup_close_label(),
                    id=_PALETTE_CLOSE,
                    classes=POPUP_CLOSE_CLASS,
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            yield from self._compose_form()
            with Vertical(id="palette-actions"):
                yield Button("Submit", id=_SUBMIT_ID, variant="primary")

    def _compose_form(self) -> ComposeResult:
        with VerticalScroll(id=_PALETTE_FORM_ID, classes=POPUP_BODY_CLASS):
            yield Static("", id=_GUIDANCE_ID, markup=False)
            yield from self._compose_task_submission_fields()
            yield from self._compose_task_role_fields()
            yield Static("", id=_ACTION_STATE_ID, markup=False)
            with VerticalScroll(id="palette-result-scroll", classes=POPUP_BODY_CLASS):
                yield Static("", id="palette-result", markup=False)

    def _compose_task_submission_fields(self) -> ComposeResult:
        yield from self._text_area_field("Request", "request", "Describe the work")
        yield FoldLine("", id=_OPTIONS_FOLD_ID, markup=False)
        with Vertical(id=_OPTIONS_ID):
            yield from self._input_field(
                "Title (optional)", _FIELD_TITLE, "Task title", inline=True, wide=True
            )
            yield from self._select_field(
                "Mode", _FIELD_MODE, _MODE_OPTIONS, value=_EMPTY, inline=True, wide=True
            )
            yield from self._select_field(
                "Complexity",
                _FIELD_COMPLEXITY,
                _COMPLEXITY_OPTIONS,
                value=_EMPTY,
                inline=True,
                wide=True,
            )
            yield from self._input_field(
                "Priority", _FIELD_SUBMISSION_PRIORITY, "0", inline=True, wide=True
            )
            yield from self._input_field(
                "Dependencies", _FIELD_DEPENDENCIES, "T-1, T-2", inline=True, wide=True
            )
            yield from self._input_field(
                "Leader rung", _FIELD_LEADER_RUNG, "agent:model:effort", inline=True, wide=True
            )
            yield from self._input_field(
                "Implementer rung",
                _FIELD_IMPLEMENTER_RUNG,
                "agent:model:effort",
                inline=True,
                wide=True,
            )
        yield from self._input_field(
            "Destination",
            _FIELD_EXPORT_DESTINATION,
            _EXPORT_DESTINATION_PLACEHOLDER,
            inline=True,
            wide=True,
        )
        yield from self._select_field(
            "Format",
            _FIELD_EXPORT_FORMAT,
            _EXPORT_FORMAT_OPTIONS,
            value=EXPORT_FORMAT_MARKDOWN,
            inline=True,
            wide=True,
        )
        yield from self._select_field(
            "Existing file",
            _FIELD_EXPORT_OVERWRITE,
            _EXPORT_OVERWRITE_OPTIONS,
            value=_EXPORT_REFUSE,
            inline=True,
            wide=True,
        )
        yield from self._select_field(
            "Agent signals",
            _FIELD_EXPORT_SIGNALS,
            _EXPORT_SIGNALS_OPTIONS,
            value=_EXPORT_SIGNALS_EXCLUDE,
            inline=True,
            wide=True,
        )

    def _compose_task_role_fields(self) -> ComposeResult:
        yield from self._input_field("Priority", _FIELD_PRIORITY, "0", inline=True)
        yield from self._select_field(
            "Role", _FIELD_ROLE, _ROLE_OPTIONS, value=Role.LEADER.value, inline=True
        )
        yield from self._input_field("Rung", _FIELD_RUNG, "agent:model:effort", inline=True)
        yield from self._text_area_field("Message", "message", "Message for the next turn")

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
        result_revision = self._result_reveal_revision
        self._set_text_area_heights(event.size.width, event.size.height)
        self._apply_layout()
        self.call_after_refresh(
            self._restore_form_layout,
            scroll_y,
            focused,
            result_revision,
        )

    async def on_mount(self) -> None:
        """Activate the action routed from a widget and follow database changes."""
        self._set_text_area_heights(self.size.width, self.size.height)
        self._initialize_action_route()
        self._initialize_form_state()
        self._refresh_action_availability()
        self._apply_layout()
        self.call_after_refresh(self._restore_context_form_position)
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_after_database_change),
            group=_PALETTE_STATE_WORKER,
        )
        if self._route_error is not None:
            self._result_reveal_revision += 1
            self._paint_result(self._route_error)
            self.call_after_refresh(self._reveal_result)
            self.query_one(f"#{_SUBMIT_ID}", Button).disabled = True

    def _initialize_action_route(self) -> None:
        if self._action not in SUBMIT_ACTION_LABELS:
            action = self._action or "<missing>"
            self._route_error = f"unknown palette action route: {action}"

    def _initialize_form_state(self) -> None:
        cast("Select[str]", self.query_one("#mode", Select)).value = _EMPTY
        cast("Select[str]", self.query_one("#complexity", Select)).value = _EMPTY
        task_id = self._task_id or None
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
        self._initialize_export_defaults(task_id)
        self._update_fields()
        self._restore_contextual_form_draft(self._action)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        hint_actions = _HINT_KEY_ACTIONS.get(action)
        if hint_actions is None:
            return True
        return (
            self._action in hint_actions
            and self._hint_mode()
            and not self.query_one(f"#{_SUBMIT_ID}", Button).disabled
        )

    def action_confirm(self) -> None:
        """Run the action named by the Enter hint."""
        self._activate_submit()

    def action_queue(self) -> None:
        """Queue the message named by the Ctrl+S hint."""
        self._activate_submit()

    def _hint_mode(self) -> bool:
        return self._action in _HINT_LABELS

    def _confirm_submitted(self, action: str) -> None:
        self._paint_result(_EMPTY)
        if action in _CLEAR_AFTER_QUEUE_ACTIONS:
            self._clear_message()
            self._show_confirmation(action)
            return
        self._closing_after_queue = True
        self.call_later(self._dismiss_queued, action)

    def _dismiss_queued(self, action: str) -> None:
        self.dismiss(action)

    def _clear_message(self) -> None:
        message = self.query_one(f"#{_FIELD_MESSAGE}", TextArea)
        message.text = _EMPTY
        message.focus()

    def _show_confirmation(self, action: str) -> None:
        self._stop_confirmation()
        self._confirmed_action = action
        self._confirmation_timer = self.set_timer(
            self._action_confirmation_seconds, self._end_confirmation
        )
        self._refresh_confirmation()

    def _end_confirmation(self) -> None:
        self._confirmed_action = None
        self._confirmation_timer = None
        self._refresh_confirmation()

    def _stop_confirmation(self) -> None:
        if self._confirmation_timer is not None:
            self._confirmation_timer.stop()
        self._confirmed_action = None
        self._confirmation_timer = None

    def _refresh_confirmation(self) -> None:
        self._refresh_submit()

    def _marked(self, label: Text, action: str) -> Text:
        if self._confirmed_action != action:
            return label
        return Text.assemble(label, "  ", (_CONFIRMATION_GLYPH, f"{STYLE_BOLD} {SUCCESS_COLOR}"))

    def _activate_submit(self) -> None:
        if self._action_in_flight or self._closing_after_queue:
            return
        confirming = self._action in _CONFIRM_TWICE_ACTIONS and self._hint_mode()
        if confirming and not self._confirm_armed:
            self._confirm_armed = True
            self._refresh_submit()
            return
        self._confirm_armed = False
        self._stop_confirmation()
        self._action_in_flight = True
        self.query_one(f"#{_SUBMIT_ID}", Button).disabled = True
        self._refresh_submit()
        self._action_worker = self.run_worker(self._run_action(), exclusive=True)

    def _refresh_submit(self) -> None:
        action = self._action
        submit = self.query_one(f"#{_SUBMIT_ID}", Button)
        hint = self._hint_mode()
        tiny = self.size.height <= _PALETTE_TINY_PANE_MAX_ROWS
        self.query_one("#palette-actions").set_class(hint, _HINT_CLASS)
        self._size_action_buttons(hint=hint)
        submit.can_focus = not hint or action in _ENTER_HINT_ACTIONS
        if not hint:
            compact_label = "Queue" if action == _ACTION_MESSAGE else "Submit"
            label = Text(
                compact_label if tiny else SUBMIT_ACTION_LABELS.get(action, "Submit action")
            )
        elif self._confirm_armed:
            label = key_hint(_CONFIRM_HINTS[action][tiny], f"{STYLE_BOLD} {DUSTY_ROSE}")
        else:
            label = key_hint(
                _HINT_LABELS[action][tiny],
                SECONDARY_TEXT_COLOR,
                enabled=not (submit.disabled or self._closing_after_queue),
            )
        marked = self._marked(label, action)
        submit.set_reactive(Button.label, Content.from_text(marked))
        submit.refresh(layout=True)
        submit.styles.height = "auto"

    def _size_action_buttons(self, *, hint: bool) -> None:
        tiny = self.size.height <= _PALETTE_TINY_PANE_MAX_ROWS
        narrow = self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        button = self.query_one(f"#{_SUBMIT_ID}", Button)
        button.styles.width = "100%" if hint or (narrow and not tiny) else "auto"
        button.styles.min_width = 1 if tiny or hint else _PALETTE_ACTION_BUTTON_MIN_COLUMNS
        button.styles.margin = 0 if narrow or hint else (0, 0, 0, 1)
        button.styles.height = 1 if narrow and (tiny or not hint) else "auto"
        button.styles.line_pad = 0 if hint else None

    async def on_key(self, event: events.Key) -> None:
        if event.key == _ENTER_KEY:
            submit = self.query_one(f"#{_SUBMIT_ID}", Button)
            if self._confirm_armed or (self._hint_mode() and self.focused is submit):
                event.stop()
                event.prevent_default()
                self._activate_submit()
                return
        if self._confirm_armed and event.key != _ENTER_KEY:
            self._confirm_armed = False
            self._refresh_submit()
            if event.key == _PALETTE_ESCAPE_KEY:
                event.stop()
                event.prevent_default()
                return
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
        for field in _PALETTE_TEXT_AREA_FIELDS:
            self.query_one(f"#{field}", TextArea).styles.max_height = (
                _PALETTE_COMPACT_TEXT_AREA_MAX_HEIGHT if compact else None
            )

    def _initialize_export_defaults(self, task_id: str | None) -> None:
        if task_id is not None:
            self.query_one(
                f"#{_FIELD_EXPORT_DESTINATION}", Input
            ).value = _default_export_destination(task_id)
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_EXPORT_FORMAT}", Select)
        ).value = EXPORT_FORMAT_MARKDOWN
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_EXPORT_OVERWRITE}", Select)
        ).value = _EXPORT_REFUSE
        cast(
            "Select[str]", self.query_one(f"#{_FIELD_EXPORT_SIGNALS}", Select)
        ).value = _EXPORT_SIGNALS_EXCLUDE

    def _restore_form_layout(
        self,
        scroll_y: float,
        focused: Widget | None,
        result_revision: int,
    ) -> None:
        if not self.is_mounted or not widget_is_attached(self):
            return
        if self._fit_form_to_submit_height():
            self.call_after_refresh(
                self._restore_form_layout,
                scroll_y,
                focused,
                result_revision,
            )
            return
        if self._result_reveal_revision != result_revision:
            return
        form = self.query_one("#palette-form", VerticalScroll)
        if focused is not None and not self.query_one("#palette-result-scroll").display:
            parent = focused.parent
            while parent is not None:
                if parent.id == _PALETTE_FORM_ID:
                    form.scroll_to_widget(focused, animate=False, immediate=True)
                    return
                parent = parent.parent
        form.scroll_to(y=scroll_y, animate=False, immediate=True)

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Refresh visible fields when a selection changes."""
        if event.select.id == _FIELD_ROLE:
            if not self._role_is_explicit and event.select.has_focus:
                self._role_is_explicit = True
            self._refresh_action_availability()
        elif event.select.id in {
            _FIELD_EXPORT_FORMAT,
            _FIELD_EXPORT_OVERWRITE,
            _FIELD_EXPORT_SIGNALS,
        }:
            self._refresh_action_availability()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Refresh current eligibility when a field changes."""
        del event
        self._refresh_action_availability()

    def on_input_submitted(self) -> None:
        """Run the Enter hint's action when a field of its popup is submitted."""
        if self.check_action("confirm", ()):
            self.action_confirm()

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id in {_FIELD_REQUEST, _FIELD_MESSAGE}:
            self._refresh_action_availability()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Run the selected action in a worker so the popup stays responsive."""
        button_id = event.button.id or _EMPTY
        if button_id == _PALETTE_CLOSE:
            await self.action_close()
        elif button_id == _SUBMIT_ID:
            self._activate_submit()

    @on(FoldLine.Toggled)
    def _options_toggled(self, message: FoldLine.Toggled) -> None:
        message.stop()
        self.action_toggle_options()

    def action_toggle_options(self) -> None:
        if self._action != _ACTION_SUBMIT:
            return
        self._options_expanded = not self._options_expanded
        self.query_one(f"#{_OPTIONS_ID}").display = self._options_expanded
        self._paint_options_fold()

    def on_unmount(self) -> None:
        worker = self._action_worker
        if worker is not None and worker.is_running:
            worker.cancel()
        if self._change_worker is not None:
            self._change_worker.cancel()

    async def action_close(self) -> None:
        """Dismiss the popup, keeping the Message draft."""
        self._remember_form_draft()
        self.dismiss(None)

    def _apply_layout(self) -> None:
        frame = self.query_one("#palette-frame", PopupFrame)
        form = self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll)
        tiny_pane = self.size.height <= _PALETTE_TINY_PANE_MAX_ROWS
        short_pane = (
            self.size.height <= _PALETTE_SHORT_PANE_MAX_ROWS
            or self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        )
        narrow_pane = self.size.width <= _PALETTE_SHORT_PANE_MAX_COLUMNS
        frame.styles.max_height = max(
            1,
            self.size.height - (0 if short_pane else _PALETTE_FRAME_VERTICAL_MARGIN_ROWS),
        )
        actions = self.query_one("#palette-actions", Vertical)
        actions.styles.layout = "horizontal" if tiny_pane or not narrow_pane else "vertical"
        actions.set_class(short_pane, "short")
        frame.set_class(short_pane, "short")
        frame.set_class(tiny_pane, "tiny")
        frame.set_class(
            self.size.width <= _PALETTE_STACKED_FIELDS_MAX_COLUMNS,
            "stack-fields",
        )
        form.set_class(tiny_pane, "tiny")
        fixed_rows = (
            _PALETTE_FORM_COMPACT_FIXED_ROWS if short_pane else _PALETTE_FORM_STANDARD_FIXED_ROWS
        )
        action_rows = (
            _PALETTE_STACKED_ACTIONS_ROWS
            if narrow_pane and not tiny_pane
            else _PALETTE_INLINE_ACTIONS_ROWS
        )
        self._layout_form_max_height = max(1, self.size.height - fixed_rows - action_rows)
        form.styles.max_height = self._layout_form_max_height
        self._refresh_submit()
        self.query_one("#palette-title", Label).update(self._context_form_title())
        self.call_after_refresh(self._refresh_context_form_title)

    def _fit_form_to_submit_height(self) -> bool:
        if (
            self.size.height <= _PALETTE_TINY_PANE_MAX_ROWS
            or self.size.width > _PALETTE_SHORT_PANE_MAX_COLUMNS
        ):
            return False
        button = self.query_one(f"#{_SUBMIT_ID}", Button)
        fixed_rows = (
            _PALETTE_FORM_COMPACT_FIXED_ROWS
            if self.size.height <= _PALETTE_SHORT_PANE_MAX_ROWS
            else _PALETTE_FORM_STANDARD_FIXED_ROWS
        )
        max_height = max(
            1,
            self.size.height
            - fixed_rows
            - max(_PALETTE_STACKED_ACTIONS_ROWS, button.region.height),
        )
        if max_height >= self._layout_form_max_height:
            return False
        self._layout_form_max_height = max_height
        self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll).styles.max_height = max_height
        return True

    def _context_form_title(self) -> str:
        action = self._action
        label = SUBMIT_ACTION_LABELS.get(action, "Actions")
        task_id = self._initial_context.task_id
        if task_id is None or action in {_ACTION_SUBMIT, _ACTION_STOP, _ACTION_START}:
            return label
        return f"{label} \N{MIDDLE DOT} {task_id}"

    def _paint_options_fold(self) -> None:
        label = Text(
            f"{fold_arrow(expanded=self._options_expanded)} {_OPTIONS_TITLE}",
            style=SECONDARY_TEXT_COLOR,
        )
        self.query_one(f"#{_OPTIONS_FOLD_ID}", FoldLine).update(label)

    def _refresh_context_form_title(self) -> None:
        if not self.is_mounted or not widget_is_attached(self):
            return
        self.query_one("#palette-title", Label).update(self._context_form_title())

    def _remember_form_draft(self) -> None:
        task_id = self._initial_context.task_id
        action = self._action
        if task_id is None or action != _ACTION_MESSAGE:
            return
        role = self._selected_value(_FIELD_ROLE)
        if self._remember_contextual_draft is not None:
            self._remember_contextual_draft(
                PaletteDraft(
                    task_id=task_id,
                    action=action,
                    guidance=self._text(_FIELD_MESSAGE),
                    role=role,
                    role_explicit=self._role_is_explicit,
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
        if self._initial_context.role is None and draft.role is not None:
            cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = draft.role
            self._role_is_explicit = draft.role_explicit
        if action == _ACTION_MESSAGE:
            self.query_one(f"#{_FIELD_MESSAGE}", TextArea).text = draft.guidance
        self._restored_contextual_draft_actions.add(action)

    def _restore_context_form_position(self) -> None:
        if not self.is_mounted or not widget_is_attached(self):
            return
        if self._fit_form_to_submit_height():
            self.call_after_refresh(self._restore_context_form_position)
            return
        form = self.query_one(f"#{_PALETTE_FORM_ID}", VerticalScroll)
        control = self.query_one(f"#{self._form_focus_id}") if self._form_focus_id else None
        action = self._action
        if (
            action not in _ROLE_FIRST_ACTIONS
            and control is not None
            and self._is_visible_in_form(control)
            and control.can_focus
        ):
            self.set_focus(control)
            form.scroll_to(y=self._form_scroll_y, animate=False, immediate=True)
            return
        candidate = _INITIAL_FIELD_BY_ACTION.get(action)
        if candidate is not None:
            field = self.query_one(f"#{candidate}")
            if self._is_visible_in_form(field) and field.can_focus:
                self.set_focus(field)
                form.scroll_to_widget(field, animate=False, immediate=True, force=True)
                return
        submit = self.query_one(f"#{_SUBMIT_ID}", Button)
        if submit.display and submit.can_focus:
            self.set_focus(submit)
        else:
            self.set_focus(None)

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
            if self._action == _ACTION_START:
                await self._run_project_start()
            elif self._action == _ACTION_EXPORT:
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

    async def _run_project_start(self) -> None:
        if self._start_engine is None:
            raise ShikiruError(_START_NOT_CONFIGURED)
        self._show_result("Starting engine…")
        await self._start_engine()
        if self._is_current_screen():
            self._confirm_submitted(_ACTION_START)

    async def _run_evidence_export(self) -> None:
        if self._evidence_exporter is None:
            raise ShikiruError(_EXPORT_NOT_CONFIGURED)
        task_id = self._task_id.strip()
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
            _FIELD_EXPORT_FORMAT,
            _FIELD_EXPORT_OVERWRITE,
            _FIELD_EXPORT_SIGNALS,
        ):
            self.query_one(f"#{field}").disabled = disabled
        self.query_one(f"#{_FIELD_EXPORT_DESTINATION}", Input).disabled = disabled

    async def _run_submission(self) -> None:
        action = self._action
        task_id = self._task_id.strip()
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
        failure = None
        if action == _ACTION_TAKE_OVER and ack.status is AckStatus.DONE and role is not None:
            failure = await self._select_role_pane(task_id, role)
        if ack.status is not AckStatus.REJECTED and (
            action == _ACTION_SUBMIT
            or (
                failure is None
                and ack.message != ACK_TIMEOUT_MESSAGE
                and action in _CONFIRMED_ACTIONS
            )
        ):
            self._confirm_submitted(action)
        elif self._is_current_screen():
            self._show_ack(ack, failure)

    async def _dispatch(self) -> Ack | None:
        action = self._action
        task_id = self._task_id.strip()
        if action == _ACTION_PEEK:
            self._show_peek(task_id)
            return None
        if action == _ACTION_STOP and not self._queries.engine_is_up():
            raise ValueError(_ENGINE_ALREADY_STOPPED)
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
        task_id = self._task_id.strip()
        self._action_states = (
            {
                state.action.value: state
                for state in self._queries.task_action_availability(task_id, None)
            }
            if task_id
            else {}
        )
        action = self._action
        disabled_reason = self._disabled_reason(action, task_id)
        if action in _FORM_ROLE_ACTIONS and task_id:
            selected_action = _TASK_ACTION_BY_ID[action]
            role_state = next(
                item
                for item in self._queries.task_action_availability(task_id, self._role())
                if item.action is selected_action
            )
            disabled_reason = role_state.disabled_reason
        state = self.query_one(f"#{_ACTION_STATE_ID}", Static)
        state.update(
            Text(f"Unavailable: {disabled_reason}", style=AMBER)
            if disabled_reason is not None
            else _EMPTY
        )
        state.display = disabled_reason is not None
        self.query_one(f"#{_GUIDANCE_ID}", Static).update(
            _ACTION_GUIDANCE.get(action or "", _EMPTY)
        )
        pending_same_form = form_identity in self._pending_form_commands
        self.query_one(f"#{_SUBMIT_ID}", Button).disabled = (
            self._route_error is not None
            or disabled_reason is not None
            or self._action_in_flight
            or pending_same_form
            or self._required_field_is_blank(action)
        )
        self._refresh_submit()

    async def _refresh_after_database_change(self) -> None:
        if self._is_current_screen():
            self.call_after_refresh(self._refresh_if_current_screen)

    def _refresh_if_current_screen(self) -> None:
        if self._is_current_screen():
            self._refresh_action_availability()

    def _required_field_is_blank(self, action: str | None) -> bool:
        field = _REQUIRED_FIELDS.get(action or _EMPTY)
        return field is not None and not self._identity_value(field).strip()

    def _refresh_pending_commands(self) -> None:
        for identity, command_id in tuple(self._pending_form_commands.items()):
            outcome = self._queries.command_status(command_id)
            if outcome is None:
                message = f"pending command {command_id} is missing from durable history"
                raise RuntimeError(message)
            if outcome.status is CommandStatus.PENDING:
                continue
            del self._pending_form_commands[identity]
            if outcome.status is CommandStatus.DONE and identity[0] in _CONFIRMED_ACTIONS:
                self._show_result(_EMPTY)
            else:
                self._show_stored_command(outcome)

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
        task_id = self._task_id.strip()
        action = self._action
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

    def _form_identity(self) -> tuple[str, ...]:
        action = self._action
        return (
            action,
            self._task_id.strip(),
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
        if action in {_ACTION_SUBMIT, _ACTION_STOP, _ACTION_START}:
            return "project"
        if role is not None:
            return f"{task_id} · {role.value}"
        return task_id or "task"

    def _disabled_reason(self, action: str | None, task_id: str) -> str | None:
        if action in {_ACTION_START, _ACTION_STOP}:
            return self._project_engine_disabled_reason(action)
        if action == _ACTION_EXPORT:
            return (
                _NO_TASK_TARGET
                if not task_id
                else _EXPORT_NOT_CONFIGURED
                if self._evidence_exporter is None
                else None
            )
        if action in _TASK_TARGET_ACTIONS:
            state = self._action_states.get(action)
            return state.disabled_reason if state is not None and task_id else _NO_TASK_TARGET
        return None

    def _project_engine_disabled_reason(self, action: str) -> str | None:
        engine_up = self._queries.engine_is_up()
        if action == _ACTION_START and self._start_engine is None:
            return _START_NOT_CONFIGURED
        if action == _ACTION_START and engine_up:
            return _ENGINE_ALREADY_RUNNING
        if action == _ACTION_STOP and not engine_up:
            return _ENGINE_ALREADY_STOPPED
        return None

    async def _select_role_pane(self, task_id: str, role: Role) -> str | None:
        detail = self._queries.get_task(task_id)
        task = detail.task if detail is not None else None
        pane_id = task.panes.get(role) if task is not None else None
        if pane_id is None:
            return "task is missing" if task is None else f"{role.value} pane is missing"
        try:
            await self._pane_selector(pane_id)
        except ShikiruError as error:
            return f"{_TAKEOVER_SELECTION_FAILURE}: {error}"
        return None

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

    def _update_fields(self) -> None:
        action = self._action
        groups = {
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
        }
        for field, visible in groups.items():
            self.query_one(f"#field-{field}").display = visible
        if action != _ACTION_SUBMIT:
            self._options_expanded = False
        self.query_one(f"#{_OPTIONS_FOLD_ID}").display = action == _ACTION_SUBMIT
        self.query_one(f"#{_OPTIONS_ID}").display = (
            action == _ACTION_SUBMIT and self._options_expanded
        )
        self._paint_options_fold()
        self._confirm_armed = False
        self._refresh_submit()
        self.query_one(f"#{_GUIDANCE_ID}").display = action in _ACTION_GUIDANCE

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
                engine_state = "running" if self._queries.engine_is_up() else "stopped"
                details = (
                    f"acknowledgement wait elapsed; engine is currently {engine_state}; "
                    "still pending"
                )
            else:
                details = "waiting for engine acknowledgement"
        elif ack.status is AckStatus.DONE:
            label = "Applied"
            details = self._applied_text()
        else:
            label = "Rejected"
            details = ack.message or COMMAND_REJECTED_FALLBACK
        message = f"{label} · command {ack.id} · {target}\n{details}"
        if additional_detail is not None:
            message = f"{message}\n{additional_detail}"
        self._show_result(message)

    def _show_stored_command(self, entry: CommandHistoryEntry) -> None:
        if entry.status is CommandStatus.DONE:
            label = "Applied"
            details = (
                "guidance queued for the selected role's next turn"
                if entry.action == CommandKind.SAY.value.capitalize()
                else "engine applied the command effect"
            )
        elif entry.status is CommandStatus.REJECTED:
            label = "Rejected"
            details = command_rejection_cause(entry.result)
        else:
            label = "Pending"
            engine_state = "running" if self._queries.engine_is_up() else "stopped"
            details = (
                f"command remains pending; engine currently {engine_state}; "
                "original acknowledgement wait context unknown"
            )
        self._show_result(f"{label} · command {entry.id} · {entry.target}\n{details}")

    def _applied_text(self) -> str:
        if self._command_action == _ACTION_MESSAGE:
            details = "guidance queued for the selected role's next turn"
        elif self._command_action == _ACTION_RUNG:
            details = "selected rung applies to this role's next turn"
        else:
            details = "engine applied the command effect"
        return details

    def _paint_result(self, text: str) -> None:
        self.query_one("#palette-result", Static).update(text)
        self.query_one("#palette-result-scroll").display = bool(text)

    def _show_result(self, text: str) -> None:
        if self._is_current_screen():
            self._result_reveal_revision += 1
            self._paint_result(text)
            self.call_after_refresh(self._reveal_result)

    def _reveal_result(self) -> None:
        if self._is_current_screen():
            self.query_one("#palette-form", VerticalScroll).scroll_end(animate=False)

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
        *,
        value: str,
        inline: bool = False,
        wide: bool = False,
    ) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes=_field_classes(inline=inline, wide=wide)):
            yield Label(label)
            yield Select(options, value=value, allow_blank=False, id=field_id)

    @staticmethod
    def _input_field(
        label: str,
        field_id: str,
        placeholder: str,
        *,
        inline: bool = False,
        wide: bool = False,
    ) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes=_field_classes(inline=inline, wide=wide)):
            yield Label(label)
            yield Input(placeholder=placeholder, id=field_id)

    @staticmethod
    def _text_area_field(label: str, field_id: str, placeholder: str) -> ComposeResult:
        with Vertical(id=f"field-{field_id}", classes="palette-field"):
            yield Label(label)
            text_area = TextArea(placeholder=placeholder, id=field_id, classes=POPUP_EDIT_CLASS)
            text_area.styles.min_width = _PALETTE_TEXT_AREA_MINIMUM_WIDTH
            yield text_area
