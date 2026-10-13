"""The escalation resolution screen: one concrete decision, a choice list, one send."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static, TextArea

from shikiru.dashboard.resolution_view import (
    ResolutionLayout,
    choice_renderable,
    choose_renderable,
    decision_renderable,
    layout_for,
    resolution_title,
    starred_indexes,
    tried_renderable,
)
from shikiru.domain.commands import ResolutionKind, command_rejection_cause
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.escalation_panel import ChoiceKind, build_resolution_panel
from shikiru.errors import ShikiruError
from shikiru.operator.commands import AckStatus
from shikiru.ui.age import format_short_age
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
from shikiru.ui.tokens import AMBER, DUSTY_ROSE

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult
    from textual.worker import Worker

    from shikiru.clock import Clock
    from shikiru.dashboard.contract import OnChangeRegistration
    from shikiru.domain.escalation_panel import PanelChoice, ResolutionPanel
    from shikiru.operator.commands import OperatorCommands
    from shikiru.operator.queries import OperatorQueries

RESOLUTION_CLOSE_BUTTON = "resolution-close"
_CLOSE_LABEL = "Esc \N{MULTIPLICATION SIGN}"
_REFRESH_GROUP = "resolution-refresh"
_SEND_GROUP = "resolution-send"
_NEWLINE_KEYS = frozenset({"ctrl+enter", "ctrl+j"})
_ACTION_MOVE = "move"
_ACTION_ACTIVATE = "activate"
_ACTION_TOGGLE_TRIED = "toggle_tried"
_SELECTION_ACTIONS = frozenset({_ACTION_MOVE, _ACTION_ACTIVATE, _ACTION_TOGGLE_TRIED})
_SUBMIT_KEY = "enter"
_MOVE_STEPS = {"up": -1, "down": 1}


class ResolutionDrafts:
    """The typed answers kept per task and escalation while the dashboard runs."""

    def __init__(self) -> None:
        """Start without drafts."""
        self._texts: dict[tuple[str, str], str] = {}

    def get(self, task_id: str, escalation_id: str) -> str:
        """Return the remembered answer text, empty when none."""
        return self._texts.get((task_id, escalation_id), "")

    def remember(self, task_id: str, escalation_id: str, text: str) -> None:
        """Keep `text` as the answer draft of one escalation."""
        self._texts[(task_id, escalation_id)] = text


class _ChoiceList(Vertical, can_focus=True):
    pass


class _ChoiceRow(Static):
    class Chosen(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, index: int) -> None:
        super().__init__("", classes="resolution-choice")
        self._index = index

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self._index))


class _TriedSection(Static):
    class Toggled(Message):
        pass

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Toggled())


class _AnswerBox(TextArea):
    class Submitted(Message):
        pass

    def on_key(self, event: events.Key) -> None:
        if event.key == _SUBMIT_KEY:
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
        elif event.key in _NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert("\n")


class ResolutionScreen(ModalScreen[bool]):
    """Resolve one escalation from a choice list; dismisses `True` once a resolution is queued."""

    AUTO_FOCUS = ""
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("up", f"{_ACTION_MOVE}(-1)", "Previous choice", show=False),
        Binding("down", f"{_ACTION_MOVE}(1)", "Next choice", show=False),
        Binding(_SUBMIT_KEY, _ACTION_ACTIVATE, "Send", show=False),
        Binding("t", _ACTION_TOGGLE_TRIED, "What was tried", show=False),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #resolution-frame.compact { margin: 0 1; padding: 0 1; }
    #resolution-title { text-wrap: nowrap; text-overflow: ellipsis; }
    #resolution-notice, #resolution-decision, #resolution-choose, #resolution-tried {
        height: auto;
    }
    #resolution-choices { height: auto; }
    #resolution-choices:focus { background-tint: transparent; }
    .resolution-choice { height: auto; width: 100%; }
    .resolution-choice.-selected { background: $selected-surface; }
    #resolution-answer { height: 4; margin: 0 0 0 3; }
    """
    )

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        *,
        clock: Clock,
        on_change: OnChangeRegistration,
        drafts: ResolutionDrafts,
        escalation_id: str,
    ) -> None:
        """Hold the services for one escalation; nothing is dispatched until a choice is sent."""
        super().__init__()
        prepare_popup(self)
        self._commands = commands
        self._queries = queries
        self._clock = clock
        self._on_change = on_change
        self._drafts = drafts
        self._escalation_id = escalation_id
        self._panel = self._load()
        self._selected = self._panel.preselected if self._panel is not None else 0
        self._answer_open = False
        self._tried_open = False
        self._cancel_armed = False
        self._sending = False
        self._send_error: str | None = None
        self._change_worker: Worker[None] | None = None
        draft = self._draft()
        if draft:
            self._answer_open = True
            self._selected = self._own_answer_index()

    def compose(self) -> ComposeResult:
        """Lay out the popup frame; contents are filled on mount."""
        panel = self._panel
        with PopupFrame(widget_id="resolution-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(
                    resolution_title(self._escalation_id, panel, ResolutionLayout.WIDE),
                    id="resolution-title",
                    classes=POPUP_TITLE_CLASS,
                )
                yield Button(_CLOSE_LABEL, id=RESOLUTION_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            with VerticalScroll(id="resolution-body", classes=POPUP_BODY_CLASS):
                yield Static("", id="resolution-notice", markup=False)
                if panel is not None:
                    yield Static("", id="resolution-decision", markup=False)
                    yield Static("", id="resolution-choose", markup=False)
                    with _ChoiceList(id="resolution-choices"):
                        for index, choice in enumerate(panel.choices):
                            yield _ChoiceRow(index)
                            if choice.kind is ChoiceKind.OWN_ANSWER:
                                yield _AnswerBox(id="resolution-answer", text=self._draft())
                    if panel.what_was_tried:
                        yield _TriedSection("", id="resolution-tried", markup=False)

    def on_mount(self) -> None:
        self._paint()
        if self._panel is not None:
            self.set_focus(self.query_one("#resolution-choices", _ChoiceList), scroll_visible=False)
        self.call_after_refresh(self.query_one("#resolution-frame", PopupFrame).content_ready)
        self._change_worker = self.run_worker(self._on_change(self._refresh), group=_REFRESH_GROUP)

    def on_unmount(self) -> None:
        if self._change_worker is not None:
            self._change_worker.cancel()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if not self.is_mounted:
            return
        self._paint()
        self.call_after_refresh(
            self.query_one("#resolution-frame", PopupFrame).update_preferred_size
        )

    def on_key(self, event: events.Key) -> None:
        if self._cancel_armed and event.key != _SUBMIT_KEY:
            self._disarm()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        if action in _SELECTION_ACTIONS:
            return not isinstance(self.focused, TextArea)
        return True

    def action_close(self) -> None:
        """Close without sending; the typed answer stays remembered."""
        self.dismiss(False)

    def action_move(self, step: int) -> None:
        """Select the neighbouring choice, stopping at both ends."""
        panel = self._panel
        if panel is not None:
            self._select(min(max(self._selected + step, 0), len(panel.choices) - 1))

    def action_activate(self) -> None:
        """Send the selected choice; Cancel asks for a second Enter."""
        if self._panel is not None:
            self._activate(self._selected)

    def action_toggle_tried(self) -> None:
        """Expand or collapse `What was tried`."""
        if self._panel is not None and self._panel.what_was_tried:
            self._tried_open = not self._tried_open
            self._paint()
            self._relayout()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == RESOLUTION_CLOSE_BUTTON:
            self.action_close()

    @on(_ChoiceRow.Chosen)
    def _row_chosen(self, message: _ChoiceRow.Chosen) -> None:
        message.stop()
        self._activate(message.index)

    @on(_TriedSection.Toggled)
    def _tried_toggled(self, message: _TriedSection.Toggled) -> None:
        message.stop()
        self.action_toggle_tried()

    @on(_AnswerBox.Submitted)
    def _answer_submitted(self, message: _AnswerBox.Submitted) -> None:
        message.stop()
        text = self.query_one("#resolution-answer", _AnswerBox).text.strip()
        if text:
            self._start_send(ResolutionKind.ANSWER, text)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._panel is not None:
            self._drafts.remember(self._panel.task_id, self._escalation_id, event.text_area.text)

    def _load(self) -> ResolutionPanel | None:
        view = self._queries.get_escalation(self._escalation_id)
        if view is None:
            return None
        task = self._queries.get_task_state(view.escalation.task_id)
        packet = parse_escalation_packet(view.packet) if view.packet is not None else None
        return build_resolution_panel(
            view.escalation,
            packet,
            task.title if task is not None else "",
            self._queries.escalation_resolution_disabled_reason(self._escalation_id),
        )

    def _disabled_reason(self) -> str | None:
        if self._panel is None:
            return self._queries.escalation_resolution_disabled_reason(self._escalation_id)
        return self._panel.disabled_reason

    def _draft(self) -> str:
        if self._panel is None:
            return ""
        return self._drafts.get(self._panel.task_id, self._escalation_id)

    def _own_answer_index(self) -> int:
        panel = cast("ResolutionPanel", self._panel)
        return next(
            (i for i, c in enumerate(panel.choices) if c.kind is ChoiceKind.OWN_ANSWER),
            self._selected,
        )

    async def _refresh(self) -> None:
        if not self.is_mounted:
            return
        panel = self._load()
        previous = self._panel
        self._panel = panel
        if panel is not None and previous is not None and panel.choices != previous.choices:
            self._selected = min(self._selected, len(panel.choices) - 1)
            await self.recompose()
            self.call_after_refresh(self._after_recompose)
            return
        self._paint()
        self._relayout()

    def _after_recompose(self) -> None:
        self._paint()
        if self._panel is not None:
            self.set_focus(self.query_one("#resolution-choices", _ChoiceList), scroll_visible=False)
        self._relayout()

    def _paint(self) -> None:
        panel = self._panel
        layout = layout_for(self.size.width, self.size.height)
        frame = self.query_one("#resolution-frame", PopupFrame)
        frame.set_class(layout is not ResolutionLayout.WIDE, "compact")
        self.query_one("#resolution-title", Label).update(
            resolution_title(self._escalation_id, panel, layout)
        )
        self._render_notice()
        if panel is None:
            return
        age = format_short_age(self._clock.now() - panel.created_at)
        self.query_one("#resolution-decision", Static).update(
            decision_renderable(panel, age, layout)
        )
        choose = self.query_one("#resolution-choose", Static)
        choose.update(choose_renderable())
        choose.display = layout is not ResolutionLayout.TINY
        disabled = self._disabled_reason() is not None
        starred = starred_indexes(panel)
        for index, row in enumerate(self.query(_ChoiceRow)):
            choice = panel.choices[index]
            selected = index == self._selected
            row.set_class(selected, "-selected")
            row.update(
                choice_renderable(
                    choice,
                    starred=index in starred,
                    selected=selected,
                    armed=selected and self._cancel_armed,
                    disabled=disabled,
                )
            )
        for answer in self.query(_AnswerBox):
            answer.display = self._answer_open
            answer.disabled = disabled
        if panel.what_was_tried:
            self.query_one("#resolution-tried", _TriedSection).update(
                tried_renderable(panel.what_was_tried, expanded=self._tried_open)
            )

    def _render_notice(self) -> None:
        reason = self._disabled_reason()
        notice = self.query_one("#resolution-notice", Static)
        if reason is not None:
            notice.update(Text(reason, style=AMBER))
        elif self._send_error is not None:
            notice.update(Text(self._send_error, style=DUSTY_ROSE))
        notice.display = reason is not None or self._send_error is not None

    def _relayout(self) -> None:
        frame = self.query_one("#resolution-frame", PopupFrame)
        frame.reset_preferred_size()
        self.call_after_refresh(frame.content_ready)

    def _select(self, index: int) -> None:
        self._selected = index
        self._cancel_armed = False
        self._send_error = None
        self._paint()
        self.query_one("#resolution-body", VerticalScroll).scroll_to_widget(
            self.query(_ChoiceRow)[index], animate=False
        )

    def _disarm(self) -> None:
        self._cancel_armed = False
        self._paint()

    def _activate(self, index: int) -> None:
        panel = self._panel
        if panel is None or self._disabled_reason() is not None:
            return
        confirming = self._cancel_armed and index == self._selected
        self._select(index)
        choice = panel.choices[index]
        if choice.kind is ChoiceKind.OWN_ANSWER:
            self._answer_open = True
            self._paint()
            self._relayout()
            self.set_focus(self.query_one("#resolution-answer", _AnswerBox))
        elif choice.kind is ChoiceKind.CANCEL and not confirming:
            self._cancel_armed = True
            self._paint()
        else:
            self._send_choice(choice)

    def _send_choice(self, choice: PanelChoice) -> None:
        if choice.kind is ChoiceKind.ANSWER_OPTION:
            self._start_send(ResolutionKind.ANSWER, choice.label or "")
        else:
            self._start_send(ResolutionKind(choice.kind.value), "")

    def _start_send(self, kind: ResolutionKind, answer: str) -> None:
        if self._sending or self._disabled_reason() is not None:
            return
        self._sending = True
        self.run_worker(self._send(kind, answer), group=_SEND_GROUP)

    async def _send(self, kind: ResolutionKind, answer: str) -> None:
        try:
            command = self._commands.prepare_palette_resolution(
                self._escalation_id, kind, answer=answer, retry_rung=None
            )
            if self._queries.pending_command(command) is None:
                ack = await self._commands.send(command)
                if ack.status is AckStatus.REJECTED:
                    self._fail(command_rejection_cause(ack.result))
                    return
        except ShikiruError as error:
            self._fail(str(error))
            return
        finally:
            self._sending = False
        self.dismiss(True)

    def _fail(self, reason: str) -> None:
        self._cancel_armed = False
        self._send_error = reason
        self._paint()
        self._relayout()
