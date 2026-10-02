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
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from shikiru.agents.contracts import Rung
    from shikiru.operator.commands import Ack, OperatorCommands
    from shikiru.operator.queries import EscalationView, OperatorQueries


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
_SUBMIT_ID = "palette-submit"
_SELECT_REQUIRED = "Select an item"
_TAKEOVER_SELECTION_FAILURE = "take-over succeeded, but the role pane could not be selected"

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
    ) -> None:
        """Use the operator services for actions and queries and inject pane selection."""
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._initial_action = initial_action
        self._pane_selector = pane_selector
        self._escalations: dict[str, EscalationView] = {}
        self._shown_escalation_id: str | None = None
        self._route_error: str | None = None
        self._route_action: str | None = None

    def compose(self) -> ComposeResult:
        """Show one compact form with fields enabled for the selected action."""
        with Vertical(id="palette-frame"):
            yield Label("Shikiru operator palette")
            with VerticalScroll(id="palette-form"):
                yield from self._select_field("Action", _FIELD_ACTION, PALETTE_ACTIONS)
                yield from self._input_field("Task id", "task-id", "T-1")
                yield from self._text_area_field("Request", "request", "Describe the work")
                yield from self._input_field("Title (optional)", "title", "Task title")
                yield from self._select_field("Mode", "mode", _MODE_OPTIONS)
                yield from self._select_field("Complexity", "complexity", _COMPLEXITY_OPTIONS)
                yield from self._input_field("Priority", "priority", "0")
                yield from self._input_field(
                    "Dependencies (comma separated)", "dependencies", "T-1, T-2"
                )
                yield from self._input_field("Leader rung", "leader-rung", "agent:model:effort")
                yield from self._input_field(
                    "Implementer rung", "implementer-rung", "agent:model:effort"
                )
                yield from self._select_field("Role", "role", _ROLE_OPTIONS)
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
        cast("Select[str]", self.query_one("#role", Select)).value = Role.LEADER.value
        self.query_one("#priority", Input).value = "0"
        self._route_action = self._selected_value(_FIELD_ACTION)
        self._update_fields(action_select.value)
        if self._route_error is not None:
            self._show_result(self._route_error)
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
        elif event.select.id == _FIELD_ESCALATION:
            if self._route_error is not None and event.value in self._escalations:
                self._route_error = None
                self.query_one(f"#{_SUBMIT_ID}", Button).disabled = False
                self._show_result(_EMPTY)
            self._show_escalation(event.value)
        elif event.select.id == _FIELD_RESOLUTION:
            self._update_fields(self._selected_value(_FIELD_ACTION))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Run the selected action in a worker so the palette stays responsive."""
        if event.button.id == _SUBMIT_ID:
            event.button.disabled = True
            self.run_worker(self._run_action(), exclusive=True)

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
            task_id = self._input("task-id").strip()
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
            if self._is_current_screen():
                self.query_one(f"#{_SUBMIT_ID}", Button).disabled = False

    async def _dispatch(self) -> Ack | None:
        action = self._value(_FIELD_ACTION)
        task_id = self._input("task-id").strip()
        if action == _ACTION_SUBMIT:
            return await self._commands.submit_task(
                self._text("request"),
                title=self._optional_input("title"),
                mode=self._mode(),
                complexity=self._complexity(),
                priority=self._integer("priority"),
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
        selected_role = self._selected_value("role")
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
            "task-id": action
            in {
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
                _ACTION_PEEK,
            },
            "request": action == _ACTION_SUBMIT,
            "title": action == _ACTION_SUBMIT,
            "mode": action == _ACTION_SUBMIT,
            "complexity": action == _ACTION_SUBMIT,
            "priority": action in {_ACTION_PRIORITIZE, _ACTION_SUBMIT},
            "dependencies": action == _ACTION_SUBMIT,
            "leader-rung": action == _ACTION_SUBMIT,
            "implementer-rung": action == _ACTION_SUBMIT,
            "role": action
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
        return cast("App[None]", self.app).screen is self

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
        return Role(self._value("role"))

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
    ) -> None:
        """Retain the services used by the palette screen."""
        super().__init__()
        install_theme(self)
        self._commands = commands
        self._queries = queries
        self._pane_selector = pane_selector

    def compose(self) -> ComposeResult:
        """Keep a neutral base screen beneath the modal palette."""
        yield Static()

    async def on_mount(self) -> None:
        """Open the same screen used by Ctrl+P in the dashboard."""
        self.push_screen(PaletteScreen(self._commands, self._queries, self._pane_selector))
