"""Inline Textual selection for `shikiru attach` (06 §5.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from shikiru.errors import UsageError
from shikiru.operator.sessions import SessionRow
from shikiru.ui.keys import key_hint
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual import events
    from textual.binding import BindingType

PICKER_TITLE = "Attach to a Shikiru session"
PICKER_HINT = "↑↓ move · Enter attach · Esc cancel"
PICKER_CANCELLED = "session selection was cancelled"


class SessionPickerList(ListView):
    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._reveal_selected_row)

    def _reveal_selected_row(self) -> None:
        if self.index is not None:
            self.scroll_to_widget(self.children[self.index], animate=False)


class SessionPicker(App[SessionRow | None]):
    """Show one selectable row for each Shikiru tmux session."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = """
    Screen {
        width: 100%; height: 100%; align: center middle; background: $background;
    }
    #session-picker {
        width: 100%; max-width: 100; height: 100%; max-height: 100%; min-height: 0;
        margin: 0; border: solid $steel-blue; padding: 0;
        background: $surface; color: $primary-text;
    }
    #session-picker > Label {
        height: 1; color: $primary-text; text-style: bold;
    }
    #session-picker > Static { height: 1; color: $secondary-text; }
    #session-picker-list {
        width: 100%; height: 1fr; min-height: 0; max-height: 1fr; padding: 0;
        background: $surface; scrollbar-size-vertical: $scrollbar-width;
    }
    #session-picker-list > ListItem {
        width: 100%; height: auto; padding: 0; border: none;
        background: $surface; color: $primary-text;
    }
    #session-picker-list > ListItem.-hovered { background: $surface; }
    #session-picker-list > ListItem > Static {
        width: 100%; height: auto; padding: 0; background: transparent;
        color: $primary-text; text-wrap: wrap;
    }
    #session-picker-list > ListItem.-highlight,
    #session-picker-list > ListItem.-highlight > Static {
        background: $selected-surface; color: $primary-text; text-style: bold;
    }
    """

    def __init__(self, rows: tuple[SessionRow, ...]) -> None:
        """Keep the rows so a visible selection returns its exact session record."""
        super().__init__()
        install_theme(self)
        self._rows = rows

    def compose(self) -> ComposeResult:
        """Render the prompt and sessions as an inline keyboard-selectable list."""
        with Vertical(id="session-picker"):
            yield Label(PICKER_TITLE)
            yield Static(key_hint(PICKER_HINT))
            yield SessionPickerList(
                *(
                    ListItem(
                        Static(
                            f"{row.session} · {row.project_dir} · {row.engine_state} · "
                            f"running {row.running} · "
                            f"queued {row.queued} · escalated {row.escalated}",
                            markup=False,
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


async def pick_session(rows: tuple[SessionRow, ...]) -> SessionRow:
    """Run the picker inline and report cancellation as a normal CLI error."""
    selected = await SessionPicker(rows).run_async(inline=True, inline_no_clear=True)
    if selected is None:
        raise UsageError(PICKER_CANCELLED)
    return selected
