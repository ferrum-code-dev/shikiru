"""Plain numbered session selection for the attach command."""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

from shikiru.cli.render import session_options_renderable
from shikiru.errors import UsageError
from shikiru.ui.console import human_console

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.operator.sessions import SessionRow


SESSION_CANCELLED = "session selection was cancelled"
SESSION_PROMPT = "Select a session number (or q to cancel)"
SESSION_CANCEL_CHOICES = frozenset({"q", "quit"})


def prompt_for_session(rows: Sequence[SessionRow], host: str | None) -> SessionRow:
    """Show full session identities and return the validated numbered selection."""
    console = human_console()
    console.print(session_options_renderable(tuple(rows), host=host, console=console))
    while True:
        previous_handler = signal.signal(signal.SIGINT, _interrupt_input)
        try:
            answer = input(f"{SESSION_PROMPT}: ").strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise UsageError(SESSION_CANCELLED) from error
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        if answer.lower() in SESSION_CANCEL_CHOICES:
            raise UsageError(SESSION_CANCELLED)
        if answer.isdecimal():
            try:
                selected = int(answer)
            except ValueError:
                pass
            else:
                if 1 <= selected <= len(rows):
                    return rows[selected - 1]
        console.print(f"Enter a number from 1 to {len(rows)}, or q to cancel.")


def _interrupt_input(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt
