"""The task details list: an arrow-driven popup that opens one section of a task's details."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static

from shikiru.dashboard.details_list_view import DETAILS_HINT, DetailsEntry, entry_renderable
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_SECONDARY_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    prepare_popup,
)
from shikiru.ui.screen import widget_is_attached

if TYPE_CHECKING:
    from textual import events
    from textual.app import ComposeResult

DETAILS_LIST_CLOSE_BUTTON = "task-details-close"
_CLOSE_LABEL = "Esc \N{MULTIPLICATION SIGN}"
_COMPACT_MAX_COLUMNS = 48
_COMPACT_MAX_ROWS = 20
_MOVE_ACTION = "move"


class _EntryRow(Static):
    class Chosen(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, index: int) -> None:
        super().__init__("", classes="task-details-row")
        self._index = index

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self._index))


class _EntryList(Vertical, can_focus=True):
    pass


class TaskDetailsListScreen(ModalScreen[str | None]):
    """List a task's detail sections; dismisses with the chosen entry id, or `None` on Esc."""

    AUTO_FOCUS = ""
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("up", f"{_MOVE_ACTION}(-1)", "Previous section", show=False, priority=True),
        Binding("down", f"{_MOVE_ACTION}(1)", "Next section", show=False, priority=True),
        Binding("enter", "open", "Open", show=False),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #task-details-frame.compact { margin: 0 1; padding: 0 1; }
    #task-details-title { text-wrap: nowrap; text-overflow: ellipsis; }
    #task-details-hint { height: 1; }
    #task-details-entries { height: auto; }
    #task-details-entries:focus { background-tint: transparent; }
    .task-details-row { height: 1; width: 100%; }
    .task-details-row.-selected { background: $selected-surface; }
    """
    )

    def __init__(self, task_id: str, entries: tuple[DetailsEntry, ...]) -> None:
        """Hold the task id and the rows to list; the first row starts selected."""
        super().__init__()
        prepare_popup(self)
        self._task_id = task_id
        self._entries = entries
        self._selected = 0

    @property
    def selected_id(self) -> str:
        """The entry id the selection bar is on."""
        return self._entries[self._selected].entry_id

    def compose(self) -> ComposeResult:
        """Lay out the popup frame; the rows are painted on mount."""
        with PopupFrame(widget_id="task-details-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(
                    f"Task details · {self._task_id}",
                    id="task-details-title",
                    classes=POPUP_TITLE_CLASS,
                )
                yield Button(_CLOSE_LABEL, id=DETAILS_LIST_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            yield Static(
                DETAILS_HINT, id="task-details-hint", classes=POPUP_SECONDARY_CLASS, markup=False
            )
            with (
                VerticalScroll(id="task-details-body", classes=POPUP_BODY_CLASS),
                _EntryList(id="task-details-entries"),
            ):
                for index in range(len(self._entries)):
                    yield _EntryRow(index)

    def on_mount(self) -> None:
        """Paint the rows, focus the list and size the popup to them."""
        self._apply_layout(self.size.width, self.size.height)
        self._paint()
        self.set_focus(self.query_one("#task-details-entries", _EntryList), scroll_visible=False)
        self.call_after_refresh(self.query_one("#task-details-frame", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        """Re-pick the compact layout and resize the popup."""
        if not self.is_mounted or not widget_is_attached(self):
            return
        self._apply_layout(event.size.width, event.size.height)
        self.call_after_refresh(
            self.query_one("#task-details-frame", PopupFrame).update_preferred_size
        )

    def action_close(self) -> None:
        """Close without opening anything."""
        self.dismiss(None)

    def action_move(self, step: int) -> None:
        """Select the neighbouring row, stopping at both ends."""
        self._select(min(max(self._selected + step, 0), len(self._entries) - 1))

    def action_open(self) -> None:
        """Open the selected section."""
        self.dismiss(self.selected_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close from the header control."""
        if event.button.id == DETAILS_LIST_CLOSE_BUTTON:
            self.action_close()

    @on(_EntryRow.Chosen)
    def _row_chosen(self, message: _EntryRow.Chosen) -> None:
        message.stop()
        self._selected = message.index
        self.action_open()

    def _apply_layout(self, width: int, height: int) -> None:
        compact = width <= _COMPACT_MAX_COLUMNS or height <= _COMPACT_MAX_ROWS
        self.query_one("#task-details-frame", PopupFrame).set_class(compact, "compact")

    def _select(self, index: int) -> None:
        self._selected = index
        self._paint()
        self.query_one("#task-details-body", VerticalScroll).scroll_to_widget(
            self.query(_EntryRow)[index], animate=False
        )

    def _paint(self) -> None:
        for index, row in enumerate(self.query(_EntryRow)):
            selected = index == self._selected
            row.set_class(selected, "-selected")
            row.update(entry_renderable(self._entries[index], selected=selected))
