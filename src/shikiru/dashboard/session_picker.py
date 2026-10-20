"""Inline Textual selection for `shikiru attach` (06 §5.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from shikiru.errors import UsageError
from shikiru.operator.sessions import SessionRow
from shikiru.ui.keys import key_hint
from shikiru.ui.section import SECONDARY_TEXT_COLOR, wrap_groups
from shikiru.ui.session import session_facts, session_identity
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual import events
    from textual.binding import BindingType

PICKER_TITLE = "Attach to a Shikiru session"
PICKER_HINT = "↑↓ move · Enter attach · Esc cancel"
PICKER_CANCELLED = "session selection was cancelled"


class SessionPickerList(ListView):
    def on_mount(self) -> None:
        self.call_after_refresh(self._limit_height)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._resize_contents)

    def _resize_contents(self) -> None:
        self._limit_height()
        self._reveal_selected_row()

    def _limit_height(self) -> None:
        parent = self.parent
        if parent is None:
            return
        fixed_height = sum(
            child.region.height + child.styles.margin.height
            for child in parent.children
            if child is not self and child.display
        )
        available = max(
            0,
            cast("SessionPicker", self.app).size.height
            - parent.styles.margin.height
            - parent.styles.gutter.height
            - parent.styles.padding.height
            - fixed_height,
        )
        if self.styles.max_height is None or self.styles.max_height.cells != available:
            self.styles.max_height = available

    def _reveal_selected_row(self) -> None:
        if self.index is not None:
            self.scroll_to_widget(self.children[self.index], animate=False)


class SessionPicker(App[SessionRow | None]):
    """Show one selectable row for each Shikiru tmux session."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = """
    Screen {
        width: 100%; height: auto; max-height: 100%; align: left top; background: $background;
    }
    #session-picker {
        width: 100%; max-width: 100; height: auto; max-height: 100%; min-height: 0;
        margin: 0; padding: 0 1; background: $background; color: $primary-text;
    }
    #session-picker > Label {
        height: auto; color: $steel-blue; text-style: bold;
    }
    #session-picker > Static { height: auto; color: $secondary-text; }
    #session-picker-list {
        width: 100%; height: auto; min-height: 0; max-height: 1fr; padding: 0;
        background: $background; scrollbar-size-vertical: $scrollbar-width;
    }
    #session-picker-list > ListItem {
        width: 100%; height: auto; padding: 0; border: none;
        background: $background; color: $primary-text;
    }
    #session-picker-list > ListItem.-hovered { background: $background; }
    #session-picker-list > ListItem > Static {
        width: 100%; height: auto; padding: 0; background: transparent;
        color: $primary-text; text-wrap: wrap;
    }
    #session-picker-list > ListItem.-highlight,
    #session-picker-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: bold underline;
    }
    """

    def __init__(self, rows: tuple[SessionRow, ...], *, host: str | None = None) -> None:
        """Keep the rows so a visible selection returns its exact session record."""
        super().__init__()
        install_theme(self)
        self._rows = rows
        self._host = host

    def compose(self) -> ComposeResult:
        """Render the prompt and sessions as an inline keyboard-selectable list."""
        with Vertical(id="session-picker"):
            yield Label(PICKER_TITLE)
            if self._host is not None:
                yield Static(Text(f"Remote host · {self._host}"), id="session-picker-host")
            yield Static(key_hint(PICKER_HINT))
            yield SessionPickerList(
                *(
                    ListItem(
                        Static(
                            _session_text(row),
                        )
                    )
                    for row in self._rows
                ),
                id="session-picker-list",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Return the row selected with Enter."""
        self.exit(self._rows[event.index])

    def action_cancel(self) -> None:
        """Return to the CLI without starting an attach process."""
        self.exit(None)


async def pick_session(rows: tuple[SessionRow, ...], *, host: str | None = None) -> SessionRow:
    """Run the picker inline and report cancellation as a normal CLI error."""
    selected = await SessionPicker(rows, host=host).run_async(inline=True, inline_no_clear=True)
    if selected is None:
        raise UsageError(PICKER_CANCELLED)
    return selected


def _session_text(row: SessionRow) -> Text:
    text = Text(overflow="fold")
    text.append_text(session_identity(row.session))
    text.append("\n  ")
    text.append(row.project_dir, style=SECONDARY_TEXT_COLOR)
    text.append("\n")
    text.append_text(
        wrap_groups(session_facts(row.engine_state, row.running, row.queued, row.escalated), None)
    )
    return text
