"""The `dialogue` widget: recent speech bubbles (06 §5.4, §6.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from shikiru.ui import SpeechBubble

if TYPE_CHECKING:
    from textual.app import App, ComposeResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import DialogueBubbleView, WidgetContext


class DialogueTextScreen(ModalScreen[None]):
    """Show the full text of one dialogue bubble."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self, text: str) -> None:
        """Hold the full event text to display."""
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        """Render the complete bubble text in a scrollable modal."""
        with VerticalScroll():
            yield Static(self._text, markup=False, id="dialogue-full-text")

    async def action_close(self) -> None:
        """Close the full-text modal."""
        self.dismiss()


class DialoguePanel(Static):
    """List the latest twenty bubbles and open a selected bubble's full text on Enter."""

    DEFAULT_CSS = """
    DialoguePanel { height: auto; }
    #dialogue-table { height: auto; }
    """

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; the event list is read on mount and on state changes."""
        super().__init__(id="dialogue")
        self._widget_context = context
        self._bubbles: list[DialogueBubbleView] = []

    def compose(self) -> ComposeResult:
        """Reserve a table with one row per recent speech bubble."""
        yield DataTable(show_header=False, cursor_type="row", id="dialogue-table")

    async def on_mount(self) -> None:
        """Render recent events and refresh after each state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show the complete text of the selected event."""
        if self._bubbles:
            bubble = self._bubbles[event.cursor_row]
            cast("App[None]", self.app).push_screen(DialogueTextScreen(bubble.text))

    async def _refresh(self) -> None:
        self._bubbles = self._widget_context.views.dialogue()
        table = cast("DataTable[object]", self.query_one("#dialogue-table", DataTable))
        table.clear(columns=True)
        table.add_column("dialogue")
        if not self._bubbles:
            table.add_row("no dialogue yet")
            return
        for bubble in self._bubbles:
            table.add_row(
                SpeechBubble(
                    role=bubble.role,
                    text=bubble.text,
                    task_id=bubble.task_id,
                    age=bubble.age,
                ).render()
            )


class DialogueWidget:
    """The `dialogue` dashboard widget plugin (06 §5.4, §6.3)."""

    name = "dialogue"

    def create(self, context: WidgetContext) -> Widget:
        """Build the dialogue panel for `context`."""
        return DialoguePanel(context)
