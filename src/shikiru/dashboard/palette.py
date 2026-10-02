"""The shared Textual operator palette for the popup and dashboard (06 §5.5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar, cast, get_args

from textual.app import App
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from shikiru.agents.contracts import parse_rung
from shikiru.config.settings import ComplexityName
from shikiru.dashboard.contract import PaletteRequest
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    ResolutionKind,
    RetryResolution,
)
from shikiru.domain.tasks import Mode, Role
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.operator.queries import OperatorTaskAction
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.worker import Worker

    from shikiru.agents.contracts import Rung
    from shikiru.operator.commands import Ack, OperatorCommands
    from shikiru.operator.queries import (
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
_RESOLUTION_OPTIONS = tuple((kind.value.title(), kind.value) for kind in ResolutionKind)
_EMPTY = ""
_FIELD_ACTION = "action"
_FIELD_ESCALATION = "escalation"
_FIELD_RESOLUTION = "resolution"
_FIELD_ACTION_SEARCH = "action-search"
_FIELD_TASK_SEARCH = "task-search"
_FIELD_TASK_CHOICE = "task-choice"
_FIELD_TASK_ID = "task-id"
_FIELD_ROLE = "role"
_ACTION_STATE_ID = "palette-action-state"
_GUIDANCE_ID = "palette-guidance"
_SUBMIT_ID = "palette-submit"
_TASK_COUNT_ID = "palette-task-count"
_TASK_MORE_ID = "palette-task-more"
_ADVANCED_TOGGLE_ID = "palette-advanced-toggle"
_ADVANCED_FIELDS_ID = "palette-advanced-fields"
_SELECT_REQUIRED = "Select an item"
_TASK_SEARCH_PAGE_LIMIT = 50
_TAKEOVER_SELECTION_FAILURE = "take-over succeeded, but the role pane could not be selected"
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
    #palette-packet-scroll {
        height: auto;
        max-height: 16;
        border: round $neutral-border;
        padding: 1;
        background: $background;
    }
    #palette-result-scroll { height: auto; max-height: 12; margin-top: 1; }
    #palette-packet, #palette-result { height: auto; }
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
        self._route_error: str | None = None
        self._route_action: str | None = None
        self._advanced_expanded = False
        self._action_in_flight = False
        self._action_worker: Worker[None] | None = None

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
                yield from self._select_field("Resolution", _FIELD_RESOLUTION, _RESOLUTION_OPTIONS)
                yield from self._text_area_field(
                    "Answer", "resolution-text", "Guidance for the Leader"
                )
                yield from self._input_field(
                    "Retry rung (optional)", "resolution-rung", "agent:model:effort"
                )
                yield Static("", id=_GUIDANCE_ID, markup=False)
                with VerticalScroll(id="palette-packet-scroll"):
                    yield Static("", id="palette-packet", markup=False)
                with VerticalScroll(id="palette-result-scroll"):
                    yield Static("", id="palette-result", markup=False)
            yield Button("Run action", id=_SUBMIT_ID, variant="primary")

    async def on_mount(self) -> None:
        """Load open escalation packets and activate an action routed from a widget."""
        self._escalations = {item.escalation.id: item for item in self._queries.list_escalations()}
        escalation_select = cast("Select[str]", self.query_one("#escalation", Select))
        escalation_select.set_options(
            (f"{item.escalation.id} · {item.escalation.headline}", item.escalation.id)
            for item in self._escalations.values()
        )
        action_select = cast("Select[str]", self.query_one("#action", Select))
        if self._initial_action == _EMPTY:
            action_select.value = _ACTION_SUBMIT
        elif self._initial_action in {action for _, action in PALETTE_ACTIONS}:
            action_select.value = self._initial_action
        elif self._initial_action.startswith("resolve "):
            action_select.value = _ACTION_RESOLVE
            _, _, escalation_id = self._initial_action.partition(" ")
            if escalation_id in self._escalations:
                escalation_select.value = escalation_id
            else:
                self._route_error = f"escalation {escalation_id} is not open"
                escalation_select.set_options(
                    [
                        (f"{item.escalation.id} · {item.escalation.headline}", item.escalation.id)
                        for item in self._escalations.values()
                    ]
                    + [(f"{escalation_id} · not open", escalation_id)]
                )
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
        cast("Select[str]", self.query_one(f"#{_FIELD_ROLE}", Select)).value = role
        self.query_one("#priority", Input).value = "0"
        self.query_one("#submission-priority", Input).value = "0"
        self.query_one(f"#{_FIELD_TASK_ID}", Input).value = self._initial_context.task_id or _EMPTY
        self._refresh_task_choices()
        self._route_action = self._selected_value(_FIELD_ACTION)
        self._update_fields(action_select.value)
        self._refresh_action_availability()
        if self._route_error is not None:
            self.query_one("#palette-result", Static).update(self._route_error)
            self.call_after_refresh(self._reveal_result)
            self.query_one(f"#{_SUBMIT_ID}", Button).disabled = True

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
            if self._route_error is not None and event.value in self._escalations:
                self._route_error = None
                self.query_one(f"#{_SUBMIT_ID}", Button).disabled = False
                self._show_result(_EMPTY)
            self._show_escalation(event.value)
        elif event.select.id == _FIELD_RESOLUTION:
            self._update_fields(self._selected_value(_FIELD_ACTION))
        elif event.select.id == _FIELD_TASK_CHOICE:
            if isinstance(event.value, str):
                self.query_one(f"#{_FIELD_TASK_ID}", Input).value = event.value
        elif event.select.id == _FIELD_ROLE:
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

    async def action_close(self) -> None:
        """Dismiss the shared screen or exit its standalone popup app."""
        app = cast("App[None]", self.app)
        if isinstance(app, PaletteApp):
            app.exit()
        else:
            self.dismiss()

    async def _run_action(self) -> None:
        try:
            action = self._value(_FIELD_ACTION)
            task_id = self._input(_FIELD_TASK_ID).strip()
            role = self._role() if action == _ACTION_TAKE_OVER else None
            ack = await self._dispatch()
        except (ShikiruError, ValueError) as error:
            if self._is_current_screen():
                self._show_result(str(error))
        else:
            if ack is not None:
                if self._is_current_screen():
                    self._show_ack(ack)
                if (
                    action == _ACTION_TAKE_OVER
                    and ack.status is AckStatus.DONE
                    and role is not None
                ):
                    await self._select_role_pane(ack, task_id, role)
        finally:
            self._action_in_flight = False
            self._action_worker = None
            if self._is_current_screen():
                self._refresh_action_availability()

    async def _dispatch(self) -> Ack | None:
        action = self._value(_FIELD_ACTION)
        task_id = self._input(_FIELD_TASK_ID).strip()
        if action == _ACTION_SUBMIT:
            return await self._commands.submit_task(
                self._text("request"),
                title=self._optional_input("title"),
                mode=self._mode(),
                complexity=self._complexity(),
                priority=self._integer("submission-priority"),
                depends_on=self._dependencies(),
                leader=self._optional_rung("leader-rung"),
                implementer=self._optional_rung("implementer-rung"),
            )
        if action in _TASK_ACTIONS:
            return await self._dispatch_task_action(action, task_id)
        if action == _ACTION_PEEK:
            self._show_peek(task_id)
            return None
        if action == _ACTION_RESOLVE:
            return await self._resolve()
        if action == _ACTION_STOP:
            return await self._commands.stop_engine()
        message = f"unsupported palette action: {action}"
        raise ValueError(message)

    async def _dispatch_task_action(self, action: str, task_id: str) -> Ack:
        self._validate_task_action(action, task_id)
        operations: dict[str, Callable[[], Awaitable[Ack]]] = {
            _ACTION_PRIORITIZE: lambda: self._commands.prioritize_task(
                task_id, self._integer("priority")
            ),
            _ACTION_PAUSE: lambda: self._commands.pause_task(task_id),
            _ACTION_PAUSE_NOW: lambda: self._commands.pause_task(task_id, now=True),
            _ACTION_RESUME: lambda: self._commands.resume_task(task_id),
            _ACTION_TAKE_OVER: lambda: self._commands.take_over(task_id, self._role()),
            _ACTION_HAND_BACK: lambda: self._commands.hand_back(task_id, self._role()),
            _ACTION_CANCEL: lambda: self._commands.cancel_task(task_id),
            _ACTION_RETRY: lambda: self._commands.retry_task(task_id),
            _ACTION_RUNG: lambda: self._commands.set_rung(
                task_id, self._role(), parse_rung(self._input("rung"))
            ),
            _ACTION_MESSAGE: lambda: self._commands.send_message(
                task_id, self._role(), self._text("message")
            ),
        }
        operation = operations.get(action)
        if operation is None:
            message = f"unsupported task action: {action}"
            raise ValueError(message)
        return await operation()

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
        state = self.query_one(f"#{_ACTION_STATE_ID}", Static)
        state.update(f"Unavailable: {disabled_reason}" if disabled_reason is not None else _EMPTY)
        guidance = _ACTION_GUIDANCE.get(action or "", _EMPTY)
        self.query_one(f"#{_GUIDANCE_ID}", Static).update(guidance)
        self.query_one(f"#{_SUBMIT_ID}", Button).disabled = (
            self._route_error is not None or disabled_reason is not None or self._action_in_flight
        )

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
        selected = self._selected_value(_FIELD_TASK_CHOICE)
        if selected is None:
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

    async def _resolve(self) -> Ack:
        escalation_id = self._value(_FIELD_ESCALATION)
        item = self._escalations.get(escalation_id)
        if item is None:
            message = f"escalation {escalation_id} is not open"
            raise ValueError(message)
        kind = ResolutionKind(self._value(_FIELD_RESOLUTION))
        if kind is ResolutionKind.ANSWER:
            resolution = AnswerResolution(text=self._text("resolution-text"))
        elif kind is ResolutionKind.RETRY:
            resolution = RetryResolution(rung=self._optional_rung("resolution-rung"))
        elif kind is ResolutionKind.ACCEPT:
            resolution = AcceptResolution()
        else:
            resolution = CancelResolution()
        if not any(option.label is kind for option in item.escalation.options):
            message = f"{kind.value} is not an option for {escalation_id}"
            raise ValueError(message)
        return await self._commands.resolve_escalation(escalation_id, resolution)

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

    def _show_escalation(self, value: object) -> None:
        escalation_id = value if isinstance(value, str) else _EMPTY
        selected_id = escalation_id or None
        if selected_id == self._shown_escalation_id:
            return
        self._shown_escalation_id = selected_id
        item = self._escalations.get(escalation_id)
        packet = self.query_one("#palette-packet", Static)
        resolution = cast("Select[str]", self.query_one("#resolution", Select))
        if item is None:
            packet.update(_EMPTY)
            resolution.set_options(_RESOLUTION_OPTIONS)
            return
        options = tuple(
            (
                f"{'★ ' if option.recommended else ''}{option.label.value}",
                option.label.value,
            )
            for option in item.escalation.options
        )
        resolution.set_options(options)
        recommended = next(
            option.label.value for option in item.escalation.options if option.recommended
        )
        resolution.value = recommended
        packet.update(
            f"{item.escalation.id} · {item.escalation.headline}\n"
            f"{' · '.join(label for label, _ in options)}\n\n{item.packet}"
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
        self.query_one("#palette-packet-scroll").display = action == _ACTION_RESOLVE
        self._show_escalation(self._selected_value(_FIELD_ESCALATION))

    def _show_ack(self, ack: Ack, additional_detail: str | None = None) -> None:
        result = ack.result or {}
        task_id = result.get("task_id")
        detail = f" · {task_id}" if task_id is not None else _EMPTY
        message = f"{ack.status.value} · command {ack.id}{detail}"
        for detail_message in (ack.message, additional_detail):
            if detail_message:
                message = f"{message}\n{detail_message}"
        self._show_result(message)

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
        return self.is_mounted and cast("App[None]", self.app).screen is self

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
        initial_context: PaletteRequest | None = None,
    ) -> None:
        """Retain the services used by the palette screen."""
        super().__init__()
        install_theme(self)
        self._commands = commands
        self._queries = queries
        self._pane_selector = pane_selector
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
                initial_context=self._initial_context,
            )
        )
