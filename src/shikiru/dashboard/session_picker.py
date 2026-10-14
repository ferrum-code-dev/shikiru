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
    from textual.binding import BindingType

PICKER_TITLE = "Attach to a Shikiru session"
PICKER_HINT = "Select a session and press Enter. Esc cancels."
PICKER_CANCELLED = "session selection was cancelled"


class SessionPicker(App[SessionRow | None]):
    """Show one selectable row for each Shikiru tmux session."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]
    CSS = """
    Screen { height: 80%; align: center middle; }
    #session-picker {
        width: 90%; max-width: 100; height: auto; max-height: 80%;
        border: round $accent; padding: 1 2;
    }
    #session-picker-list { height: auto; max-height: 1fr; }
    #session-picker-list > ListItem.-highlight {
        background: $selected-surface; color: $primary-text; text-style: none;
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
            yield ListView(
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
