"""The shared Textual operator palette for the popup and dashboard (06 §5.5)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, ClassVar, cast, get_args

from textual.app import App
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, Select, Static, TextArea

from shikiru.agents.contracts import parse_rung
from shikiru.config.settings import ComplexityName
from shikiru.dashboard.contract import PaletteRequest
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
from shikiru.operator.queries import OperatorTaskAction
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult
    from textual.widget import Widget
    from textual.worker import Worker

    from shikiru.agents.contracts import Rung
    from shikiru.dashboard.contract import OnChangeRegistration
    from shikiru.domain.commands import Command
    from shikiru.operator.commands import Ack, OperatorCommands
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
_ACTION_STOP = "stop"
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
_TASK_TARGET_ACTIONS = _TASK_ACTIONS | {_ACTION_PEEK}
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
    ("Stop engine", _ACTION_STOP),
)
_TASK_ROLES = (Role.LEADER, Role.IMPLEMENTER)
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
_PALETTE_SHORT_PANE_MAX_ROWS = 18
_PALETTE_COMPACT_TEXT_AREA_HEIGHT = 2
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
}
_TASK_ACTION_BY_ID = {action.value: action for action in OperatorTaskAction}
_NO_TASK_TARGET = "Choose a task target first"
_ACTION_DISABLED_PREFIX = "unavailable: "

PaneSelector = Callable[[str], Awaitable[None]]


class PaletteScreen(ModalScreen[None]):
    """Render operator actions, dispatch commands and show their acknowledgements."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    PaletteScreen { align: center middle; }
    #palette-frame {
        width: 90%;
        height: 90%;
        max-width: 100;
        border: round $neutral-border;
        padding: 1 2;
        background: $surface;
        color: $primary-text;
    }
    #palette-form { height: 1fr; }
    .palette-field { height: auto; margin-bottom: 1; }
    .palette-field Label { height: 1; }
    .palette-field Input, .palette-field Select { width: 100%; }
    .palette-field Input:focus,
    .palette-field TextArea:focus,
    #palette-submit:focus {
        border: round $focus;
        text-style: bold reverse;
    }
    .palette-field Select:focus > SelectCurrent { border: tall $focus; }
    #palette-result-scroll { height: auto; max-height: 12; margin-top: 1; }
    #palette-escalation-summary, #palette-escalation-status, #palette-evidence-error,
    #palette-original-packet, #palette-result { height: auto; }
    #palette-evidence { height: auto; }
    .palette-evidence { height: auto; }
    #request { height: 6; }
    #message { height: 4; }
    #resolution-text { height: 5; }
    #palette-submit { margin-top: 1; }
    """

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        pane_selector: PaneSelector,
        *,
        on_change: OnChangeRegistration,
        initial_action: str = _EMPTY,
        initial_context: PaletteRequest | None = None,
    ) -> None:
        """Use the operator services for actions and queries and inject pane selection."""
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._initial_context = initial_context or PaletteRequest()
        self._initial_action = self._initial_context.action or initial_action
        self._pane_selector = pane_selector
        self._on_change = on_change
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

    def compose(self) -> ComposeResult:
        """Show one compact form with fields enabled for the selected action."""
        with Vertical(id="palette-frame"):
            yield Label("Shikiru operator palette")
            with VerticalScroll(id="palette-form"):
                yield from self._input_field("Find action", _FIELD_ACTION_SEARCH, "message")
                yield from self._select_field("Action", _FIELD_ACTION, PALETTE_ACTIONS)
                yield Static("", id=_ACTION_STATE_ID, markup=False)
                yield from self._input_field(
                    "Find task by ID or title", _FIELD_TASK_SEARCH, "T-1 or title"
                )
                yield from self._select_field("Task match", _FIELD_TASK_CHOICE, ())
                yield Static("", id=_TASK_COUNT_ID, markup=False)
                yield Button("More matches", id=_TASK_MORE_ID)
                yield Button("Advanced submission options", id=_ADVANCED_TOGGLE_ID)
                yield from self._input_field("Task ID · editable", _FIELD_TASK_ID, "T-1")
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
                yield from self._select_field("Role", _FIELD_ROLE, _ROLE_OPTIONS)
                yield from self._input_field("Rung", "rung", "agent:model:effort")
                yield from self._text_area_field("Message", "message", "Message for the next turn")
                yield from self._select_field("Escalation", _FIELD_ESCALATION, ())
                with Vertical(id=f"field-{_ESCALATION_STATUS_ID}"):
                    yield Static("", id=_ESCALATION_STATUS_ID, markup=False)
                with Vertical(id=f"field-{_ESCALATION_SUMMARY_ID}"):
                    yield Static("", id=_ESCALATION_SUMMARY_ID, markup=False)
                yield from self._select_field("Resolution", _FIELD_RESOLUTION, ())
                yield from self._text_area_field(
                    "Answer", "resolution-text", "Guidance for the Leader"
                )
                yield from self._input_field(
                    "Retry rung (optional)", "resolution-rung", "agent:model:effort"
                )
                yield Static("", id=_GUIDANCE_ID, markup=False)
                with Vertical(id=f"field-{_EVIDENCE_ERROR_ID}"):
                    yield Static("", id=_EVIDENCE_ERROR_ID, markup=False)
                with Vertical(id=_EVIDENCE_CONTAINER_ID):
                    for title, section_id in _EVIDENCE_SECTIONS:
                        with Collapsible(
                            Static("", id=f"palette-evidence-{section_id}", markup=False),
                            title=title,
                            collapsed=True,
                            id=f"palette-evidence-toggle-{section_id}",
                            classes="palette-evidence",
                        ):
                            pass
                    with Collapsible(
                        Static("", id=_ORIGINAL_PACKET_ID, markup=False),
                        title="Original full packet",
                        collapsed=True,
                        id="palette-evidence-original",
                        classes="palette-evidence",
                    ):
                        pass
                with VerticalScroll(id="palette-result-scroll"):
                    yield Static("", id="palette-result", markup=False)
            yield Button("Run action", id=_SUBMIT_ID, variant="primary")

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        parent = event.widget.parent
        while parent is not None:
            if parent.id == _PALETTE_FORM_ID:
                self.query_one("#palette-form", VerticalScroll).scroll_to_widget(
                    event.widget,
                    animate=False,
                    immediate=True,
                )
                return
            parent = parent.parent

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_mounted:
            return
        form = self.query_one("#palette-form", VerticalScroll)
        scroll_y = form.scroll_y
        focused = cast("App[None]", self.app).focused
        self._set_text_area_heights(event.size.height)
        self.call_after_refresh(self._restore_form_layout, scroll_y, focused)

    async def on_mount(self) -> None:
        """Load open escalation packets and activate an action routed from a widget."""
        self._set_text_area_heights(self.size.height)
        self._refresh_escalations()
        escalation_select = cast("Select[str]", self.query_one("#escalation", Select))
        action_select = cast("Select[str]", self.query_one("#action", Select))
        if self._initial_action == _EMPTY:
            action_select.value = _ACTION_SUBMIT
        elif self._initial_action in {action for _, action in PALETTE_ACTIONS}:
            action_select.value = self._initial_action
        elif self._initial_action.startswith("resolve "):
            action_select.value = _ACTION_RESOLVE
            _, _, escalation_id = self._initial_action.partition(" ")
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
            self._route_error = f"unknown role context: {role}"
            role = Role.LEADER.value
            self._role_is_explicit = True
        cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = role
        self.query_one("#priority", Input).value = "0"
        self.query_one("#submission-priority", Input).value = "0"
        self.query_one(f"#{_FIELD_TASK_ID}", Input).value = self._initial_context.task_id or _EMPTY
        self._refresh_task_choices()
        self._route_action = self._selected_value(_FIELD_ACTION)
        self._update_fields(action_select.value)
        self._refresh_action_availability()
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_after_database_change),
            group=_PALETTE_STATE_WORKER,
        )
        if self._route_error is not None:
            self.query_one("#palette-result", Static).update(self._route_error)
            self.call_after_refresh(self._reveal_result)
            self.query_one(f"#{_SUBMIT_ID}", Button).disabled = True

    def _set_text_area_heights(self, height: int) -> None:
        compact = height <= _PALETTE_SHORT_PANE_MAX_ROWS
        for field, normal_height in _PALETTE_TEXT_AREA_HEIGHTS:
            self.query_one(f"#{field}", TextArea).styles.height = (
                _PALETTE_COMPACT_TEXT_AREA_HEIGHT if compact else normal_height
            )

    def _restore_form_layout(self, scroll_y: float, focused: Widget | None) -> None:
        if not self.is_mounted:
            return
        form = self.query_one("#palette-form", VerticalScroll)
        if focused is not None and focused.is_mounted and focused.screen is self:
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
            self._refresh_implicit_role()
            self._refresh_action_availability()
        else:
            self._refresh_action_availability()

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id in {_FIELD_REQUEST, _FIELD_MESSAGE, _FIELD_RESOLUTION_TEXT}:
            self._refresh_action_availability()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Run the selected action in a worker so the palette stays responsive."""
        if event.button.id == _TASK_MORE_ID:
            self._refresh_task_choices()
            return
        if event.button.id == _ADVANCED_TOGGLE_ID:
            self._advanced_expanded = not self._advanced_expanded
            self.query_one(f"#{_ADVANCED_FIELDS_ID}").display = self._advanced_expanded
            return
        if event.button.id == _SUBMIT_ID:
            if self._action_in_flight:
                return
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
        app = cast("App[None]", self.app)
        if isinstance(app, PaletteApp):
            app.exit()
        else:
            self.dismiss()

    async def _run_action(self) -> None:
        try:
            await self._run_submission()
        except (ShikiruError, ValueError) as error:
            if self._is_current_screen():
                self._show_result(str(error))
        finally:
            self._action_in_flight = False
            self._submitting_form_identity = None
            self._action_worker = None
            if self._is_current_screen():
                self._refresh_action_availability()

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
        state = next(
            item
            for item in self._queries.task_action_availability(task_id, self._role())
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
                for state in self._queries.task_action_availability(task_id, self._role())
            }
            if task_id
            else {}
        )
        self._refresh_action_options()
        action = self._selected_value(_FIELD_ACTION)
        disabled_reason = self._disabled_reason(action, task_id)
        if action == _ACTION_RESOLVE:
            disabled_reason = self._resolve_disabled_reason() or disabled_reason
        state = self.query_one(f"#{_ACTION_STATE_ID}", Static)
        state.update(f"Unavailable: {disabled_reason}" if disabled_reason is not None else _EMPTY)
        guidance = _ACTION_GUIDANCE.get(action or "", _EMPTY)
        self.query_one(f"#{_GUIDANCE_ID}", Static).update(guidance)
        pending_same_form = form_identity in self._pending_form_commands
        self.query_one(f"#{_SUBMIT_ID}", Button).disabled = (
            self._route_error is not None
            or disabled_reason is not None
            or self._action_in_flight
            or pending_same_form
        )

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
        if focused is not None and focused.is_mounted and focused.screen is self:
            focused.focus()
        self.query_one("#palette-form", VerticalScroll).scroll_to(
            y=scroll_y,
            animate=False,
            immediate=True,
        )

    def _escalation_select_options(self) -> list[tuple[str, str]]:
        return [
            (
                f"{item.escalation.id} · {item.escalation.headline}"
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
        resolution = cast("Select[str]", self.query_one("#resolution", Select))
        if item is None:
            self._escalation_packet = None
            self._escalation_state_message = None
            status.update(_EMPTY)
            summary.update(self._route_error or self._missing_escalation_message(escalation_id))
            evidence_error.update(_EMPTY)
            self._show_packet_evidence(None, None)
            resolution.set_options(())
            return

        self._escalation_state_message = self._queries.escalation_resolution_disabled_reason(
            escalation_id
        )
        status.update(self._escalation_state_message or "Open escalation")
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
        lines = [f"{item.escalation.id} · {item.escalation.headline}"]
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
        groups = {
            _FIELD_ACTION_SEARCH: True,
            _FIELD_TASK_SEARCH: action in _TASK_TARGET_ACTIONS,
            _FIELD_TASK_CHOICE: action in _TASK_TARGET_ACTIONS,
            _FIELD_TASK_ID: action in _TASK_TARGET_ACTIONS,
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
            _FIELD_ESCALATION: action == _ACTION_RESOLVE,
            _ESCALATION_STATUS_ID: action == _ACTION_RESOLVE,
            _ESCALATION_SUMMARY_ID: action == _ACTION_RESOLVE,
            _EVIDENCE_ERROR_ID: action == _ACTION_RESOLVE,
            _FIELD_RESOLUTION: action == _ACTION_RESOLVE,
            "resolution-text": action == _ACTION_RESOLVE
            and self._selected_value(_FIELD_RESOLUTION) == ResolutionKind.ANSWER,
            "resolution-rung": action == _ACTION_RESOLVE
            and self._selected_value(_FIELD_RESOLUTION) == ResolutionKind.RETRY,
        }
        for field, visible in groups.items():
            self.query_one(f"#field-{field}").display = visible
        self.query_one(f"#{_ACTION_STATE_ID}").display = action in _TASK_TARGET_ACTIONS
        self.query_one(f"#{_GUIDANCE_ID}").display = action in _ACTION_GUIDANCE
        self.query_one(f"#{_TASK_COUNT_ID}").display = action in _TASK_TARGET_ACTIONS
        self.query_one(f"#{_TASK_MORE_ID}").display = (
            action in _TASK_TARGET_ACTIONS and self._task_search_has_more
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
            yield TextArea(placeholder=placeholder, id=field_id)


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
    ) -> None:
        """Retain the services used by the palette screen."""
        super().__init__()
        install_theme(self)
        self._commands = commands
        self._queries = queries
        self._pane_selector = pane_selector
        self._on_change = on_change
        self._initial_context = initial_context

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
            )
        )
